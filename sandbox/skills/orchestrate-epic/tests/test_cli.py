"""Unit tests for orchestrate_epic.cli."""

from __future__ import annotations

import io
import json
import sys
import unittest

from orchestrate_epic.cli import run


class FilterActionableCliTests(unittest.TestCase):
    def test_filters_and_outputs_json(self):
        input_data = {
            "issues": [
                {
                    "key": "X-1",
                    "fields": {
                        "summary": "story one",
                        "status": {"name": "New", "statusCategory": {"key": "new"}},
                        "issuetype": {"name": "Story"},
                    },
                },
                {
                    "key": "X-2",
                    "fields": {
                        "summary": "done story",
                        "status": {"name": "Closed", "statusCategory": {"key": "done"}},
                        "issuetype": {"name": "Story"},
                    },
                },
                {
                    "key": "X-3",
                    "fields": {
                        "summary": "a spike",
                        "status": {"name": "New", "statusCategory": {"key": "new"}},
                        "issuetype": {"name": "Spike"},
                    },
                },
            ],
        }
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(input_data))
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["filter-actionable"])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["key"], "X-1")


class SandboxNameCliTests(unittest.TestCase):
    def test_outputs_lowercase_name(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["sandbox-name", "MCS-278"])
            output = sys.stdout.getvalue().strip()
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(output, "mcs-278")

    def test_with_suffix(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["sandbox-name", "MCS-278", "--suffix", "fixup"])
            output = sys.stdout.getvalue().strip()
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(output, "mcs-278-fixup")


class SandboxCreateArgsCliTests(unittest.TestCase):
    _REQUIRED_ARGS = [
        "sandbox-create-args",
        "--name", "mcs-278",
        "--template", "/tmp/keep.yaml",
        "--prompt", "/implement-ticket MCS-278",
        "--run-id", "epic-1-202611231430",
        "--epic", "EPIC-1",
        "--ticket", "MCS-278",
        "--type", "implementation",
    ]

    def test_outputs_json_args(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(self._REQUIRED_ARGS)
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertIn("sandbox", output)
        self.assertIn("create", output)
        self.assertIn("mcs-278", output)

    def test_contains_expected_label_pairs(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(self._REQUIRED_ARGS)
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertIn("--label", output)
        self.assertIn("project=rosa-agent", output)
        self.assertIn("run-id=epic-1-202611231430", output)
        self.assertIn("ticket=mcs-278", output)

    def test_missing_run_id_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            run([
                "sandbox-create-args",
                "--name", "mcs-278",
                "--template", "/tmp/keep.yaml",
                "--prompt", "test",
                "--epic", "EPIC-1",
                "--ticket", "MCS-278",
                "--type", "implementation",
            ])
        self.assertEqual(ctx.exception.code, 2)

    def test_multiline_prompt_in_json_output(self):
        prompt = "Fix these:\n- file.py:10 rename foo\n- file.py:20 remove bar"
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run([
                "sandbox-create-args",
                "--name", "mcs-278",
                "--template", "/tmp/keep.yaml",
                "--prompt", prompt,
                "--run-id", "epic-1-202611231430",
                "--epic", "EPIC-1",
                "--ticket", "MCS-278",
                "--type", "implementation",
            ])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertIn("PROMPT=" + prompt, output)

    def test_extra_label_no_equals_exits(self):
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            code = run(self._REQUIRED_ARGS + ["--label", "bad"])
            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(code, 1)
        self.assertIn("error:", stderr_output)


class RunIdCliTests(unittest.TestCase):
    def test_outputs_run_id(self):
        import re

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["run-id", "--epic", "EPIC-1"])
            output = sys.stdout.getvalue().strip()
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertRegex(output, r"^[a-z0-9-]+-\d{12}$")

    def test_lowercases_epic(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["run-id", "--epic", "ROSAENG-123"])
            output = sys.stdout.getvalue().strip()
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("rosaeng-123-"))


class EpicCommentCliTests(unittest.TestCase):
    def test_outputs_adf_json(self):
        stories = [
            {"key": "X-1", "summary": "test", "pr_number": 1,
             "pr_url": "https://example.com/pull/1", "merged_date": "2026-01-01"},
        ]
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(stories))
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["epic-comment", "--repo", "org/repo"])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertIn("body", output)
        self.assertEqual(output["body"]["type"], "doc")

    def test_bad_merged_story_input_exits_1(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps([{"key": "X-1"}]))
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            code = run(["epic-comment", "--repo", "org/repo"])
            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        self.assertEqual(code, 1)
        self.assertIn("error:", stderr_output)

    def test_invalid_json_exits_1(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("not json")
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            code = run(["epic-comment", "--repo", "org/repo"])
            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        self.assertEqual(code, 1)
        self.assertIn("error:", stderr_output)


class SandboxStatusCliTests(unittest.TestCase):
    def test_single_sandbox(self):
        data = {
            "metadata": {"name": "mcs-278"},
            "phase": "Ready",
            "resource_version": 42,
        }
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(data))
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["sandbox-status"])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(output["name"], "mcs-278")
        self.assertEqual(output["phase"], "Ready")
        self.assertEqual(output["resource_version"], 42)

    def test_sandbox_list(self):
        data = [
            {"metadata": {"name": "mcs-278"}, "phase": "Ready", "resource_version": 1},
            {"metadata": {"name": "mcs-319"}, "phase": "Completed", "resource_version": 2},
        ]
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(data))
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["sandbox-status", "--list"])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0]["name"], "mcs-278")
        self.assertEqual(output[1]["name"], "mcs-319")


class DetectStuckCliTests(unittest.TestCase):
    def test_outputs_verdict(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run(["detect-stuck", "--type", "nit", "--elapsed-minutes", "25"])
            output = sys.stdout.getvalue().strip()
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(output, "kill")


class NoCommandTests(unittest.TestCase):
    def test_no_command_returns_1(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run([])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
