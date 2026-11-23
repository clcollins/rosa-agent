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


class BuildEpicCommentTests(unittest.TestCase):
    def _flatten_text(self, node):
        """Recursively extract all text from an ADF node."""
        texts = []
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                texts.extend(self._flatten_text(child))
            for mark in node.get("marks", []):
                if "attrs" in mark and "href" in mark["attrs"]:
                    texts.append(mark["attrs"]["href"])
        return texts

    def test_returns_adf_doc(self):
        stories = [
            MergedStory(key="X-1", summary="test", pr_number=1, pr_url="https://example.com/pull/1", merged_date="2026-01-01"),
        ]
        doc = build_epic_comment(stories, repo="org/repo")
        self.assertEqual(doc["type"], "doc")
        self.assertEqual(doc["version"], 1)

    def test_includes_all_merged_stories(self):
        stories = [
            MergedStory(key="MCS-278", summary="warn insecure", pr_number=69, pr_url="https://github.com/org/repo/pull/69", merged_date="2026-11-04"),
            MergedStory(key="MCS-319", summary="harden last_sandbox", pr_number=70, pr_url="https://github.com/org/repo/pull/70", merged_date="2026-11-04"),
        ]
        doc = build_epic_comment(stories, repo="org/repo")
        all_text = " ".join(self._flatten_text(doc))
        self.assertIn("MCS-278", all_text)
        self.assertIn("MCS-319", all_text)
        self.assertIn("PR #69", all_text)
        self.assertIn("PR #70", all_text)
        self.assertIn("2026-11-04", all_text)

    def test_has_table_with_header_row(self):
        stories = [
            MergedStory(key="X-1", summary="test", pr_number=1, pr_url="https://example.com/pull/1", merged_date="2026-01-01"),
        ]
        doc = build_epic_comment(stories, repo="org/repo")
        tables = [n for n in doc["content"] if n["type"] == "table"]
        self.assertEqual(len(tables), 1)
        first_row = tables[0]["content"][0]
        self.assertEqual(first_row["content"][0]["type"], "tableHeader")

    def test_empty_stories_raises(self):
        with self.assertRaises(JiraPayloadError):
            build_epic_comment([], repo="org/repo")

    def test_preserves_special_chars_in_summary(self):
        stories = [
            MergedStory(key="X-1", summary="fix [array|list] parsing", pr_number=1, pr_url="https://example.com/pull/1", merged_date="2026-01-01"),
        ]
        doc = build_epic_comment(stories, repo="org/repo")
        all_text = " ".join(self._flatten_text(doc))
        self.assertIn("fix [array|list] parsing", all_text)

    def test_custom_jira_base_url(self):
        stories = [
            MergedStory(key="X-1", summary="test", pr_number=1, pr_url="https://example.com/pull/1", merged_date="2026-01-01"),
        ]
        doc = build_epic_comment(stories, repo="org/repo", jira_base_url="https://custom.atlassian.net")
        all_text = " ".join(self._flatten_text(doc))
        self.assertIn("custom.atlassian.net/browse/X-1", all_text)
        self.assertNotIn("redhat.atlassian.net", all_text)

    def test_no_false_tests_pass_claim(self):
        stories = [
            MergedStory(key="X-1", summary="test", pr_number=1, pr_url="https://example.com/pull/1", merged_date="2026-01-01"),
        ]
        doc = build_epic_comment(stories, repo="org/repo")
        all_text = " ".join(self._flatten_text(doc))
        self.assertNotIn("Tests pass", all_text)


if __name__ == "__main__":
    unittest.main()
