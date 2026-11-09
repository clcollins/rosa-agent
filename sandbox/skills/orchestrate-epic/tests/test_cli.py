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
    def test_outputs_json_args(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = run([
                "sandbox-create-args",
                "--name", "mcs-278",
                "--template", "/tmp/keep.yaml",
                "--prompt", "/implement-ticket MCS-278",
            ])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertIn("sandbox", output)
        self.assertIn("create", output)
        self.assertIn("mcs-278", output)


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
