"""Tests for hack/check_placeholder_literals.py."""

import unittest

from check_placeholder_literals import PATTERN, find_violations


class TestPattern(unittest.TestCase):
    def test_matches_lowercase_colon_form(self):
        self.assertIsNotNone(PATTERN.search("openshell:resolve:env:FOO"))

    def test_matches_uppercase_dash_form(self):
        self.assertIsNotNone(PATTERN.search("OPENSHELL-RESOLVE-ENV-BAR"))

    def test_case_insensitive(self):
        self.assertIsNotNone(PATTERN.search("Openshell:Resolve:Env:x"))

    def test_no_match_on_safe_wording(self):
        self.assertIsNone(PATTERN.search("credential placeholder (openshell resolve-env)"))

    def test_no_match_on_spaced_form(self):
        self.assertIsNone(PATTERN.search("openshell:resolve:env :FOO"))


class TestFindViolations(unittest.TestCase):
    def test_detects_violation(self):
        entries = [("some/file.md", "use openshell:resolve:env:TOKEN here")]
        results = find_violations(entries)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "some/file.md")
        self.assertEqual(results[0][1], 1)

    def test_detects_dash_form(self):
        entries = [("foo.yaml", "# OPENSHELL-RESOLVE-ENV-SECRET")]
        results = find_violations(entries)
        self.assertEqual(len(results), 1)

    def test_allowlists_containerfile(self):
        entries = [("Containerfile", "sed openshell:resolve:env:X")]
        results = find_violations(entries)
        self.assertEqual(len(results), 0)

    def test_allowlists_containerfile_in_subdirectory(self):
        entries = [("subdir/Containerfile", "sed openshell:resolve:env:X")]
        results = find_violations(entries)
        self.assertEqual(len(results), 0)

    def test_clean_file_passes(self):
        entries = [("clean.md", "credential placeholder (openshell resolve-env)")]
        results = find_violations(entries)
        self.assertEqual(len(results), 0)

    def test_reports_correct_line_number(self):
        text = "line one\nline two\nopenshell:resolve:env:BAD\nline four"
        entries = [("multi.md", text)]
        results = find_violations(entries)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], 3)

    def test_multiple_files_multiple_violations(self):
        entries = [
            ("a.md", "openshell:resolve:env:A"),
            ("b.yaml", "safe line\nOPENSHELL-RESOLVE-ENV-B"),
            ("Containerfile", "openshell:resolve:env:allowed"),
        ]
        results = find_violations(entries)
        self.assertEqual(len(results), 2)

    def test_empty_input(self):
        results = find_violations([])
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
