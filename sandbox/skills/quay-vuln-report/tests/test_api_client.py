"""Unit tests for quay_vuln_report.api_client.

All HTTP is mocked at the subprocess.run boundary - no real curl or network
calls happen here, matching the tool's own curl-only transport design.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from quay_vuln_report.api_client import _STATUS_MARKER, QuayAPIClient, QuayAPIError


def _completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["curl"], returncode=returncode, stdout=stdout, stderr=stderr)


def _curl_ok(body, status=200):
    return _completed(body + _STATUS_MARKER + str(status))


class QuayAPIClientGetTests(unittest.TestCase):
    def setUp(self):
        self.client = QuayAPIClient()
        self.which_patcher = patch("quay_vuln_report.api_client.shutil.which", return_value="/usr/bin/curl")
        self.which_patcher.start()
        self.addCleanup(self.which_patcher.stop)

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_200_returns_parsed_json(self, mock_run):
        mock_run.return_value = _curl_ok(json.dumps({"ok": True}))
        result = self.client._get("/repository/ns/repo/tag/")
        self.assertEqual(result, {"ok": True})

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_404_raises_with_status_code(self, mock_run):
        mock_run.return_value = _curl_ok("{}", status=404)
        with self.assertRaises(QuayAPIError) as ctx:
            self.client._get("/repository/ns/repo/tag/")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_401_and_403_raise_with_status_code(self, mock_run):
        for status in (401, 403):
            mock_run.return_value = _curl_ok("{}", status=status)
            with self.assertRaises(QuayAPIError) as ctx:
                self.client._get("/repository/ns/repo/tag/")
            self.assertEqual(ctx.exception.status_code, status)

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_other_non_2xx_raises(self, mock_run):
        mock_run.return_value = _curl_ok("server error", status=500)
        with self.assertRaises(QuayAPIError) as ctx:
            self.client._get("/repository/ns/repo/tag/")
        self.assertEqual(ctx.exception.status_code, 500)

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_nonzero_curl_exit_raises(self, mock_run):
        mock_run.return_value = _completed("", returncode=7, stderr="couldn't connect")
        with self.assertRaises(QuayAPIError) as ctx:
            self.client._get("/repository/ns/repo/tag/")
        self.assertIn("couldn't connect", str(ctx.exception))

    def test_missing_curl_binary_raises(self):
        with patch("quay_vuln_report.api_client.shutil.which", return_value=None):
            with self.assertRaises(QuayAPIError):
                self.client._get("/repository/ns/repo/tag/")

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_missing_status_marker_raises(self, mock_run):
        mock_run.return_value = _completed('{"ok": true}')
        with self.assertRaises(QuayAPIError):
            self.client._get("/repository/ns/repo/tag/")

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_non_numeric_status_raises(self, mock_run):
        mock_run.return_value = _completed("{}" + _STATUS_MARKER + "oops")
        with self.assertRaises(QuayAPIError):
            self.client._get("/repository/ns/repo/tag/")

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_invalid_json_body_raises(self, mock_run):
        mock_run.return_value = _curl_ok("not json")
        with self.assertRaises(QuayAPIError):
            self.client._get("/repository/ns/repo/tag/")

    @patch("quay_vuln_report.api_client.subprocess.run")
    def test_os_error_running_curl_raises(self, mock_run):
        mock_run.side_effect = OSError("no such file")
        with self.assertRaises(QuayAPIError):
            self.client._get("/repository/ns/repo/tag/")


class ResolveTagDigestTests(unittest.TestCase):
    def setUp(self):
        self.client = QuayAPIClient()

    @patch.object(QuayAPIClient, "_get")
    def test_happy_path(self, mock_get):
        mock_get.return_value = {"tags": [{"manifest_digest": "sha256:abc"}]}
        digest = self.client.resolve_tag_digest("ns/repo", "latest")
        self.assertEqual(digest, "sha256:abc")

    @patch.object(QuayAPIClient, "_get")
    def test_no_active_tag_raises(self, mock_get):
        mock_get.return_value = {"tags": []}
        with self.assertRaises(QuayAPIError):
            self.client.resolve_tag_digest("ns/repo", "latest")

    @patch.object(QuayAPIClient, "_get")
    def test_tag_missing_digest_raises(self, mock_get):
        mock_get.return_value = {"tags": [{}]}
        with self.assertRaises(QuayAPIError):
            self.client.resolve_tag_digest("ns/repo", "latest")

    @patch.object(QuayAPIClient, "_get")
    def test_get_manifest_security_passes_through(self, mock_get):
        mock_get.return_value = {"status": "scanned"}
        result = self.client.get_manifest_security("ns/repo", "sha256:abc")
        self.assertEqual(result, {"status": "scanned"})
        mock_get.assert_called_once_with(
            "/repository/ns/repo/manifest/sha256:abc/security",
            params={"vulnerabilities": "true"},
        )

    @patch.object(QuayAPIClient, "_get")
    def test_manifest_list_picks_amd64_child(self, mock_get):
        manifest_list = {
            "manifests": [
                {"digest": "sha256:arm64child", "platform": {"architecture": "arm64", "os": "linux"}},
                {"digest": "sha256:amd64child", "platform": {"architecture": "amd64", "os": "linux"}},
            ]
        }
        mock_get.side_effect = [
            {"tags": [{"manifest_digest": "sha256:listdigest", "is_manifest_list": True}]},
            {"manifest_data": json.dumps(manifest_list)},
        ]
        digest = self.client.resolve_tag_digest("ns/repo", "latest")
        self.assertEqual(digest, "sha256:amd64child")
        mock_get.assert_any_call("/repository/ns/repo/manifest/sha256:listdigest")

    @patch.object(QuayAPIClient, "_get")
    def test_manifest_list_falls_back_to_first_child_without_amd64(self, mock_get):
        manifest_list = {
            "manifests": [
                {"digest": "sha256:ppc64child", "platform": {"architecture": "ppc64le", "os": "linux"}},
                {"digest": "sha256:armchild", "platform": {"architecture": "arm", "os": "linux"}},
            ]
        }
        mock_get.side_effect = [
            {"tags": [{"manifest_digest": "sha256:listdigest", "is_manifest_list": True}]},
            {"manifest_data": json.dumps(manifest_list)},
        ]
        digest = self.client.resolve_tag_digest("ns/repo", "latest")
        self.assertEqual(digest, "sha256:ppc64child")

    @patch.object(QuayAPIClient, "_get")
    def test_manifest_list_with_no_usable_children_raises(self, mock_get):
        mock_get.side_effect = [
            {"tags": [{"manifest_digest": "sha256:listdigest", "is_manifest_list": True}]},
            {"manifest_data": json.dumps({"manifests": []})},
        ]
        with self.assertRaises(QuayAPIError):
            self.client.resolve_tag_digest("ns/repo", "latest")

    @patch.object(QuayAPIClient, "_get")
    def test_manifest_list_missing_manifest_data_raises(self, mock_get):
        mock_get.side_effect = [
            {"tags": [{"manifest_digest": "sha256:listdigest", "is_manifest_list": True}]},
            {},
        ]
        with self.assertRaises(QuayAPIError):
            self.client.resolve_tag_digest("ns/repo", "latest")

    @patch.object(QuayAPIClient, "_get")
    def test_manifest_list_unparseable_manifest_data_raises(self, mock_get):
        mock_get.side_effect = [
            {"tags": [{"manifest_digest": "sha256:listdigest", "is_manifest_list": True}]},
            {"manifest_data": "not json"},
        ]
        with self.assertRaises(QuayAPIError):
            self.client.resolve_tag_digest("ns/repo", "latest")

    @patch.object(QuayAPIClient, "_get")
    def test_non_manifest_list_tag_skips_manifest_list_resolution(self, mock_get):
        mock_get.return_value = {
            "tags": [{"manifest_digest": "sha256:abc", "is_manifest_list": False}]
        }
        digest = self.client.resolve_tag_digest("ns/repo", "latest")
        self.assertEqual(digest, "sha256:abc")
        mock_get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
