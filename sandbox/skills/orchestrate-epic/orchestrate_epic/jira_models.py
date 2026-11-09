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


def build_sprint_payload(issue_keys: List[str]) -> Dict[str, Any]:
    """Build a POST payload to add issues to a sprint."""
    if not issue_keys:
        raise JiraPayloadError("issue_keys must not be empty")
    return {"issues": list(issue_keys)}


def build_epic_comment(
    stories: List[MergedStory],
    repo: str,
) -> str:
    """Build a Jira wiki markup comment summarizing the epic completion.

    Uses Jira wiki markup (not markdown): h2., ||table||, [text|url], (/).
    """
    if not stories:
        raise JiraPayloadError("stories must not be empty")

    lines = [
        "h2. Epic completion summary",
        "",
        "All stories in this epic have been implemented and merged.",
        "",
        "h3. PRs merged ({repo})".format(repo=repo),
        "",
        "||Card||Summary||PR||Merged||",
    ]
    for s in stories:
        lines.append(
            "|[{key}|https://redhat.atlassian.net/browse/{key}]"
            "|{summary}"
            "|[PR #{pr}|{url}]"
            "|{date}|".format(
                key=s.key,
                summary=s.summary,
                pr=s.pr_number,
                url=s.pr_url,
                date=s.merged_date,
            )
        )

    lines.extend([
        "",
        "h3. Definition of Done",
        "",
        "* (/) All stories merged",
        "* (/) Tests pass on all PRs",
    ])

    return "\n".join(lines)
