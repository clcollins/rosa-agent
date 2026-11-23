"""Unit tests for orchestrate_epic.sandbox_models."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from orchestrate_epic.sandbox_models import (
    SandboxLabelError,
    SandboxNameError,
    SandboxStatus,
    StuckVerdict,
    build_create_args,
    build_labels,
    detect_stuck,
    make_run_id,
    parse_sandbox_list,
    parse_sandbox_status,
    sandbox_name,
    sandbox_name_suffixed,
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


class BuildLabelsTests(unittest.TestCase):
    def _base_labels(self, **overrides):
        defaults = dict(
            run_id="epic-1-202611231430",
            epic_key="EPIC-1",
            ticket_key="MCS-278",
            sandbox_type="implementation",
        )
        defaults.update(overrides)
        return build_labels(**defaults)

    def test_base_keys_present(self):
        labels = self._base_labels()
        self.assertEqual(labels["project"], "rosa-agent")
        self.assertEqual(labels["workload"], "orchestrate-epic")
        self.assertEqual(labels["owner"], "sd-sre-platform")

    def test_per_child_keys_present(self):
        labels = self._base_labels()
        self.assertEqual(labels["environment"], "test")
        self.assertEqual(labels["run-id"], "epic-1-202611231430")
        self.assertEqual(labels["epic"], "epic-1")
        self.assertEqual(labels["ticket"], "mcs-278")
        self.assertEqual(labels["sandbox-type"], "implementation")

    def test_keys_lowercased(self):
        labels = self._base_labels(epic_key="EPIC-1", ticket_key="MCS-278")
        self.assertEqual(labels["epic"], "epic-1")
        self.assertEqual(labels["ticket"], "mcs-278")

    def test_extra_merges(self):
        labels = self._base_labels(extra={"cost-center": "eng"})
        self.assertEqual(labels["cost-center"], "eng")

    def test_extra_overriding_base_raises(self):
        with self.assertRaises(SandboxLabelError):
            self._base_labels(extra={"project": "other"})

    def test_extra_overriding_per_child_raises(self):
        with self.assertRaises(SandboxLabelError):
            self._base_labels(extra={"run-id": "other"})

    def test_invalid_char_in_value_raises(self):
        with self.assertRaises(SandboxLabelError):
            self._base_labels(extra={"custom": "bad value!"})

    def test_value_over_63_chars_raises(self):
        with self.assertRaises(SandboxLabelError):
            self._base_labels(extra={"custom": "a" * 64})

    def test_slash_in_value_raises(self):
        with self.assertRaises(SandboxLabelError):
            self._base_labels(extra={"custom": "a/b"})

    def test_slash_in_key_allowed(self):
        labels = self._base_labels(extra={"example.com/custom": "valid"})
        self.assertEqual(labels["example.com/custom"], "valid")

    def test_invalid_sandbox_type_raises(self):
        with self.assertRaises(SandboxLabelError):
            self._base_labels(sandbox_type="unknown")

    def test_custom_environment(self):
        labels = self._base_labels(environment="staging")
        self.assertEqual(labels["environment"], "staging")

    def test_all_valid_sandbox_types(self):
        for stype in ("implementation", "fixup", "nit", "rebase"):
            labels = self._base_labels(sandbox_type=stype)
            self.assertEqual(labels["sandbox-type"], stype)


class MakeRunIdTests(unittest.TestCase):
    def test_format_with_fixed_datetime(self):
        dt = datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc)
        run_id = make_run_id("EPIC-1", dt)
        self.assertEqual(run_id, "epic-1-202611231430")

    def test_lowercases_epic_key(self):
        dt = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        run_id = make_run_id("ROSAENG-123", dt)
        self.assertTrue(run_id.startswith("rosaeng-123-"))

    def test_long_epic_key_raises(self):
        dt = datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc)
        with self.assertRaises(SandboxLabelError):
            make_run_id("A" * 60, dt)

    def test_matches_expected_pattern(self):
        import re

        dt = datetime(2026, 6, 15, 9, 5, tzinfo=timezone.utc)
        run_id = make_run_id("x-1", dt)
        self.assertRegex(run_id, r"^[a-z0-9-]+-\d{12}$")


class BuildCreateArgsTests(unittest.TestCase):
    SAMPLE_LABELS = {
        "project": "rosa-agent",
        "workload": "orchestrate-epic",
        "owner": "sd-sre-platform",
        "environment": "test",
        "run-id": "epic-1-202611231430",
        "epic": "epic-1",
        "ticket": "mcs-278",
        "sandbox-type": "implementation",
    }

    def test_basic_implementation_command(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="/implement-ticket MCS-278",
            labels=self.SAMPLE_LABELS,
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
            labels=self.SAMPLE_LABELS,
            model="claude-opus-4-6",
        )
        self.assertIn("--env", args)
        self.assertIn("ANTHROPIC_MODEL=claude-opus-4-6", args)

    def test_includes_detach_and_replace(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="test",
            labels=self.SAMPLE_LABELS,
        )
        self.assertIn("--detach", args)
        self.assertIn("--replace", args)

    def test_no_model_omits_env(self):
        args = build_create_args(
            name="mcs-278",
            template_path="/tmp/keep.yaml",
            prompt="test",
            labels=self.SAMPLE_LABELS,
        )
        self.assertNotIn("--env", args)

    def test_starts_with_sandbox_create(self):
        args = build_create_args(
            name="x-1",
            template_path="t.yaml",
            prompt="hello",
            labels=self.SAMPLE_LABELS,
        )
        self.assertEqual(args[0], "sandbox")
        self.assertEqual(args[1], "create")

    def test_one_label_per_entry_sorted(self):
        labels = {"b-key": "b-val", "a-key": "a-val"}
        args = build_create_args(
            name="x-1",
            template_path="t.yaml",
            prompt="hello",
            labels=labels,
        )
        label_pairs = []
        for i, a in enumerate(args):
            if a == "--label":
                label_pairs.append(args[i + 1])
        self.assertEqual(label_pairs, ["a-key=a-val", "b-key=b-val"])

    def test_multiline_prompt_preserved(self):
        prompt = "Fix these:\n- file.py:10 rename foo\n- file.py:20 remove bar"
        args = build_create_args(
            name="x-1",
            template_path="t.yaml",
            prompt=prompt,
            labels={"a": "b"},
        )
        prompt_idx = args.index("PROMPT=" + prompt)
        self.assertGreater(prompt_idx, 0)

    def test_empty_labels_raises(self):
        with self.assertRaises(SandboxLabelError):
            build_create_args(
                name="x-1",
                template_path="t.yaml",
                prompt="hello",
                labels={},
            )


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

    def test_rebase_sandbox_ok_at_15_min(self):
        verdict = detect_stuck("rebase", elapsed_minutes=15)
        self.assertEqual(verdict, StuckVerdict.OK)

    def test_rebase_sandbox_investigate_at_20_min(self):
        verdict = detect_stuck("rebase", elapsed_minutes=20)
        self.assertEqual(verdict, StuckVerdict.INVESTIGATE)

    def test_rebase_sandbox_kill_at_30_min(self):
        verdict = detect_stuck("rebase", elapsed_minutes=30)
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
