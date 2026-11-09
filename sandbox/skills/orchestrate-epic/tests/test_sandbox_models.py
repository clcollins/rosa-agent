"""Unit tests for orchestrate_epic.sandbox_models."""

from __future__ import annotations

import unittest

from orchestrate_epic.sandbox_models import (
    SandboxNameError,
    SandboxStatus,
    StuckVerdict,
    sandbox_name,
    sandbox_name_suffixed,
    build_create_args,
    parse_sandbox_status,
    parse_sandbox_list,
    detect_stuck,
)


class SandboxNameTests(unittest.TestCase):
    def test_lowercases_ticket_key(self):
        self.assertEqual(sandbox_name("MCS-278"), "mcs-278")

    def test_strips_whitespace(self):
        self.assertEqual(sandbox_name("  MCS-278  "), "mcs-278")

    def test_rejects_name_over_19_chars(self):
        with self.assertRaises(SandboxNameError):
            sandbox_name("REALLY-LONG-TICKET-NAME")

    def test_rejects_empty_name(self):
        with self.assertRaises(SandboxNameError):
            sandbox_name("")

    def test_short_key_passes(self):
        self.assertEqual(sandbox_name("X-1"), "x-1")


class SandboxNameSuffixedTests(unittest.TestCase):
    def test_appends_suffix(self):
        self.assertEqual(sandbox_name_suffixed("MCS-278", "fixup"), "mcs-278-fixup")

    def test_rejects_combined_over_19_chars(self):
        with self.assertRaises(SandboxNameError):
            sandbox_name_suffixed("MCS-278", "really-long-suffix")

    def test_rejects_empty_suffix(self):
        with self.assertRaises(SandboxNameError):
            sandbox_name_suffixed("MCS-278", "")


class BuildCreateArgsTests(unittest.TestCase):
    def test_basic_implementation_command(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="/implement-ticket MCS-278",
        )
        self.assertIn("--name", args)
        self.assertIn("mcs-278", args)
        self.assertIn("-f", args)
        self.assertIn("/tmp/keep.yaml", args)
        self.assertIn("-p", args)
        self.assertIn("NAME=mcs-278", args)
        self.assertIn("PROMPT=/implement-ticket MCS-278", args)

    def test_includes_model_env(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="test",
            model="claude-opus-4-6",
        )
        self.assertIn("--env", args)
        self.assertIn("ANTHROPIC_MODEL=claude-opus-4-6", args)

    def test_includes_detach_and_replace(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="test",
        )
        self.assertIn("--detach", args)
        self.assertIn("--replace", args)

    def test_no_model_omits_env(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="test",
        )
        self.assertNotIn("--env", args)

    def test_starts_with_sandbox_create(self):
        args = build_create_args(
            name="x-1",
            template_path="t.yaml",
            prompt="hello",
        )
        self.assertEqual(args[0], "sandbox")
        self.assertEqual(args[1], "create")


class ParseSandboxStatusTests(unittest.TestCase):
    def test_parses_ready_sandbox(self):
        data = {
            "metadata": {"name": "mcs-278"},
            "phase": "Ready",
            "resource_version": 15,
        }
        status = parse_sandbox_status(data)
        self.assertEqual(status.name, "mcs-278")
        self.assertEqual(status.phase, "Ready")
        self.assertEqual(status.resource_version, 15)

    def test_parses_completed_sandbox(self):
        data = {
            "metadata": {"name": "mcs-278"},
            "phase": "Completed",
            "resource_version": 30,
        }
        status = parse_sandbox_status(data)
        self.assertEqual(status.phase, "Completed")

    def test_missing_phase_raises(self):
        with self.assertRaises(KeyError):
            parse_sandbox_status({"metadata": {"name": "x"}})


class ParseSandboxListTests(unittest.TestCase):
    def test_parses_multiple_sandboxes(self):
        data = [
            {"metadata": {"name": "mcs-278"}, "phase": "Ready", "resource_version": 10},
            {"metadata": {"name": "mcs-318"}, "phase": "Completed", "resource_version": 20},
        ]
        statuses = parse_sandbox_list(data)
        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[0].name, "mcs-278")
        self.assertEqual(statuses[1].name, "mcs-318")

    def test_empty_list(self):
        self.assertEqual(parse_sandbox_list([]), [])


class DetectStuckTests(unittest.TestCase):
    def test_nit_sandbox_not_stuck_at_10_min(self):
        verdict = detect_stuck("nit", elapsed_minutes=10)
        self.assertEqual(verdict, StuckVerdict.OK)

    def test_nit_sandbox_investigate_at_15_min(self):
        verdict = detect_stuck("nit", elapsed_minutes=15)
        self.assertEqual(verdict, StuckVerdict.INVESTIGATE)

    def test_nit_sandbox_kill_at_20_min(self):
        verdict = detect_stuck("nit", elapsed_minutes=20)
        self.assertEqual(verdict, StuckVerdict.KILL)

    def test_fixup_sandbox_ok_at_15_min(self):
        verdict = detect_stuck("fixup", elapsed_minutes=15)
        self.assertEqual(verdict, StuckVerdict.OK)

    def test_fixup_sandbox_investigate_at_20_min(self):
        verdict = detect_stuck("fixup", elapsed_minutes=20)
        self.assertEqual(verdict, StuckVerdict.INVESTIGATE)

    def test_fixup_sandbox_kill_at_30_min(self):
        verdict = detect_stuck("fixup", elapsed_minutes=30)
        self.assertEqual(verdict, StuckVerdict.KILL)

    def test_implementation_sandbox_ok_at_45_min(self):
        verdict = detect_stuck("implementation", elapsed_minutes=45)
        self.assertEqual(verdict, StuckVerdict.OK)

    def test_implementation_sandbox_investigate_at_60_min(self):
        verdict = detect_stuck("implementation", elapsed_minutes=60)
        self.assertEqual(verdict, StuckVerdict.INVESTIGATE)

    def test_implementation_sandbox_kill_at_90_min(self):
        verdict = detect_stuck("implementation", elapsed_minutes=90)
        self.assertEqual(verdict, StuckVerdict.KILL)

    def test_unknown_type_defaults_to_implementation_thresholds(self):
        verdict = detect_stuck("unknown", elapsed_minutes=60)
        self.assertEqual(verdict, StuckVerdict.INVESTIGATE)

    def test_boundary_values_favor_escalation(self):
        self.assertEqual(detect_stuck("nit", elapsed_minutes=15), StuckVerdict.INVESTIGATE)
        self.assertEqual(detect_stuck("nit", elapsed_minutes=20), StuckVerdict.KILL)
        self.assertEqual(detect_stuck("fixup", elapsed_minutes=20), StuckVerdict.INVESTIGATE)
        self.assertEqual(detect_stuck("fixup", elapsed_minutes=30), StuckVerdict.KILL)


if __name__ == "__main__":
    unittest.main()
