"""Pure functions for parsing Jira responses and building Jira API payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


class JiraPayloadError(ValueError):
    """Raised when a Jira API response or input is malformed or invalid."""


@dataclass(frozen=True)
class EpicChild:
    key: str
    summary: str
    status_name: str
    status_category: str
    issue_type: str


@dataclass(frozen=True)
class MergedStory:
    key: str
    summary: str
    pr_number: int
    pr_url: str
    merged_date: str


def parse_epic_children(response: Dict[str, Any]) -> List[EpicChild]:
    """Parse a Jira v3 search/jql response into a list of EpicChild."""
    if "issues" not in response:
        raise JiraPayloadError(
            "response missing 'issues' key — expected a Jira search result"
        )
    children: List[EpicChild] = []
    for issue in response["issues"]:
        if "fields" not in issue:
            raise JiraPayloadError(
                f"issue {issue.get('key', '?')} missing 'fields'"
            )
        fields = issue["fields"]
        children.append(
            EpicChild(
                key=issue["key"],
                summary=fields.get("summary", ""),
                status_name=fields.get("status", {}).get("name", ""),
                status_category=fields.get("status", {}).get("statusCategory", {}).get("key", ""),
                issue_type=fields.get("issuetype", {}).get("name", ""),
            )
        )
    return children


def filter_actionable(children: List[EpicChild]) -> List[EpicChild]:
    """Filter to stories/tasks that are not done and not Spikes."""
    return [
        c
        for c in children
        if c.status_category != "done"
        and c.issue_type.lower() != "spike"
    ]


def build_assignee_payload(account_id: str) -> Dict[str, Any]:
    """Build a PUT payload to assign an issue."""
    if not account_id:
        raise JiraPayloadError("account_id must not be empty")
    return {"fields": {"assignee": {"accountId": account_id}}}


def build_remote_link_payload(url: str, title: str) -> Dict[str, Any]:
    """Build a POST payload to add a remote link to an issue."""
    if not url:
        raise JiraPayloadError("url must not be empty")
    if not title:
        raise JiraPayloadError("title must not be empty")
    return {"object": {"url": url, "title": title}}


def build_transition_payload(transition_id: str) -> Dict[str, Any]:
    """Build a POST payload to transition an issue."""
    if not transition_id:
        raise JiraPayloadError("transition_id must not be empty")
    return {"transition": {"id": transition_id}}


def _adf_text(text: str) -> Dict[str, Any]:
    """Create an ADF text node."""
    return {"type": "text", "text": text}


def _adf_link(text: str, href: str) -> Dict[str, Any]:
    """Create an ADF text node with a link mark."""
    return {
        "type": "text",
        "text": text,
        "marks": [{"type": "link", "attrs": {"href": href}}],
    }


def _adf_table_header(text: str) -> Dict[str, Any]:
    """Create an ADF table header cell."""
    return {
        "type": "tableHeader",
        "content": [{"type": "paragraph", "content": [_adf_text(text)]}],
    }


def _adf_table_cell(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create an ADF table cell with inline content."""
    return {
        "type": "tableCell",
        "content": [{"type": "paragraph", "content": content}],
    }


def build_epic_comment(
    stories: List[MergedStory],
    repo: str,
    jira_base_url: str = "https://redhat.atlassian.net",
) -> Dict[str, Any]:
    """Build an ADF comment payload summarizing the epic completion.

    Returns a dict suitable for POST /rest/api/3/issue/KEY/comment as the
    request body (wrap in {"body": <result>}).
    """
    if not stories:
        raise JiraPayloadError("stories must not be empty")

    base = jira_base_url.rstrip("/")

    header_row = {
        "type": "tableRow",
        "content": [
            _adf_table_header("Card"),
            _adf_table_header("Summary"),
            _adf_table_header("PR"),
            _adf_table_header("Merged"),
        ],
    }

    data_rows = []
    for s in stories:
        data_rows.append({
            "type": "tableRow",
            "content": [
                _adf_table_cell([_adf_link(s.key, f"{base}/browse/{s.key}")]),
                _adf_table_cell([_adf_text(s.summary)]),
                _adf_table_cell([_adf_link(f"PR #{s.pr_number}", s.pr_url)]),
                _adf_table_cell([_adf_text(s.merged_date)]),
            ],
        })

    doc: Dict[str, Any] = {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [_adf_text("Epic completion summary")],
            },
            {
                "type": "paragraph",
                "content": [_adf_text(
                    "All stories in this epic have been implemented and merged."
                )],
            },
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [_adf_text(f"PRs merged ({repo})")],
            },
            {
                "type": "table",
                "content": [header_row] + data_rows,
            },
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [_adf_text("Definition of Done")],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{"type": "paragraph", "content": [
                            _adf_text("All stories merged"),
                        ]}],
                    },
                ],
            },
        ],
    }

    return doc
