"""Unit tests for orchestrate_epic.jira_models."""

from __future__ import annotations

import unittest

from orchestrate_epic.jira_models import (
    EpicChild,
    JiraPayloadError,
    MergedStory,
    filter_actionable,
    parse_epic_children,
    build_assignee_payload,
    build_remote_link_payload,
    build_transition_payload,
    build_sprint_payload,
    build_epic_comment,
)


class ParseEpicChildrenTests(unittest.TestCase):
    def test_parses_issues_from_v3_response(self):
        response = {
            "issues": [
                {
                    "key": "MCS-278",
                    "fields": {
                        "summary": "warn on --gateway-insecure",
                        "status": {
                            "name": "New",
                            "statusCategory": {"key": "new"},
                        },
                        "issuetype": {"name": "Story"},
                    },
                },
                {
                    "key": "MCS-330",
                    "fields": {
                        "summary": "Spike — workspace isolation",
                        "status": {
                            "name": "Backlog",
                            "statusCategory": {"key": "new"},
                        },
                        "issuetype": {"name": "Spike"},
                    },
                },
            ],
        }
        children = parse_epic_children(response)
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0].key, "MCS-278")
        self.assertEqual(children[0].summary, "warn on --gateway-insecure")
        self.assertEqual(children[0].status_name, "New")
        self.assertEqual(children[0].status_category, "new")
        self.assertEqual(children[0].issue_type, "Story")
        self.assertEqual(children[1].key, "MCS-330")
        self.assertEqual(children[1].issue_type, "Spike")

    def test_empty_issues_list(self):
        children = parse_epic_children({"issues": []})
        self.assertEqual(children, [])

    def test_missing_issues_key_raises(self):
        with self.assertRaises(JiraPayloadError):
            parse_epic_children({"something": "else"})

    def test_missing_fields_raises(self):
        with self.assertRaises(JiraPayloadError):
            parse_epic_children({"issues": [{"key": "X-1"}]})


class FilterActionableTests(unittest.TestCase):
    def _child(self, key, status_cat, issue_type="Story"):
        return EpicChild(
            key=key,
            summary=f"summary for {key}",
            status_name="Whatever",
            status_category=status_cat,
            issue_type=issue_type,
        )

    def test_filters_done_status(self):
        children = [
            self._child("X-1", "new"),
            self._child("X-2", "done"),
            self._child("X-3", "indeterminate"),
        ]
        result = filter_actionable(children)
        self.assertEqual([c.key for c in result], ["X-1", "X-3"])

    def test_filters_spike_issue_type(self):
        children = [
            self._child("X-1", "new", "Story"),
            self._child("X-2", "new", "Spike"),
            self._child("X-3", "new", "Task"),
        ]
        result = filter_actionable(children)
        self.assertEqual([c.key for c in result], ["X-1", "X-3"])

    def test_filters_both_done_and_spike(self):
        children = [
            self._child("X-1", "done", "Spike"),
            self._child("X-2", "new", "Story"),
        ]
        result = filter_actionable(children)
        self.assertEqual([c.key for c in result], ["X-2"])

    def test_empty_input(self):
        self.assertEqual(filter_actionable([]), [])

    def test_all_filtered_returns_empty(self):
        children = [self._child("X-1", "done"), self._child("X-2", "new", "Spike")]
        self.assertEqual(filter_actionable(children), [])


class BuildAssigneePayloadTests(unittest.TestCase):
    def test_produces_correct_structure(self):
        payload = build_assignee_payload("abc123")
        self.assertEqual(payload, {"fields": {"assignee": {"accountId": "abc123"}}})

    def test_empty_account_id_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_assignee_payload("")


class BuildRemoteLinkPayloadTests(unittest.TestCase):
    def test_produces_correct_structure(self):
        payload = build_remote_link_payload(
            "https://github.com/org/repo/pull/42", "PR #42: fix things"
        )
        self.assertEqual(
            payload,
            {
                "object": {
                    "url": "https://github.com/org/repo/pull/42",
                    "title": "PR #42: fix things",
                }
            },
        )

    def test_empty_url_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_remote_link_payload("", "title")

    def test_empty_title_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_remote_link_payload("https://example.com", "")


class BuildTransitionPayloadTests(unittest.TestCase):
    def test_produces_correct_structure(self):
        payload = build_transition_payload("81")
        self.assertEqual(payload, {"transition": {"id": "81"}})

    def test_empty_id_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_transition_payload("")


class BuildSprintPayloadTests(unittest.TestCase):
    def test_single_issue(self):
        payload = build_sprint_payload(["MCS-278"])
        self.assertEqual(payload, {"issues": ["MCS-278"]})

    def test_multiple_issues(self):
        payload = build_sprint_payload(["MCS-278", "MCS-318"])
        self.assertEqual(payload, {"issues": ["MCS-278", "MCS-318"]})

    def test_empty_list_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_sprint_payload([])


class BuildEpicCommentTests(unittest.TestCase):
    def test_includes_all_merged_stories(self):
        stories = [
            MergedStory(key="MCS-278", summary="warn insecure", pr_number=69, pr_url="https://github.com/org/repo/pull/69", merged_date="2026-11-04"),
            MergedStory(key="MCS-319", summary="harden last_sandbox", pr_number=70, pr_url="https://github.com/org/repo/pull/70", merged_date="2026-11-04"),
        ]
        comment = build_epic_comment(stories, repo="org/repo")
        self.assertIn("MCS-278", comment)
        self.assertIn("MCS-319", comment)
        self.assertIn("PR #69", comment)
        self.assertIn("PR #70", comment)
        self.assertIn("2026-11-04", comment)

    def test_uses_jira_wiki_markup(self):
        stories = [
            MergedStory(key="X-1", summary="test", pr_number=1, pr_url="https://example.com/pull/1", merged_date="2026-01-01"),
        ]
        comment = build_epic_comment(stories, repo="org/repo")
        self.assertIn("||", comment)
        self.assertIn("h2.", comment)
        self.assertNotIn("##", comment)
        self.assertNotIn("](", comment)

    def test_empty_stories_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_epic_comment([], repo="org/repo")


if __name__ == "__main__":
    unittest.main()
