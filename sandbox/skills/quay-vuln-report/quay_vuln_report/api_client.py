"""Thin client for the quay.io public REST API.

Only the read-only, unauthenticated-for-public-repos endpoints this tool
needs are wrapped here:

- ``GET /api/v1/repository/{namespace}/{repo_path}/tag/`` to resolve a tag
  name to a manifest digest.
- ``GET /api/v1/repository/{namespace}/{repo_path}/manifest/{digest}`` to
  fetch a manifest's raw data - used only when that digest turns out to be a
  multi-arch manifest list, to find a scannable per-platform child digest.
- ``GET /api/v1/repository/{namespace}/{repo_path}/manifest/{digest}/security``
  to fetch the Clair-based vulnerability report for that manifest.

Both are documented in the Red Hat Quay API reference (chapters "tag" and
"secscan" / ``getRepoManifestSecurity``) and are what quay.io's own web UI
calls under the hood - this client talks to the same JSON API instead of
scraping the HTML vulnerabilities page.

HTTP transport: this shells out to the ``curl`` binary rather than using the
``requests`` (or any pip-installed) library. That is a deliberate choice, not
a style preference: in environments that egress-gate by binary path (e.g. an
OpenShell/HyperShell sandbox with a deny-by-default network policy scoped to
specific binaries like ``/usr/bin/curl``), a raw socket opened from within
the Python interpreter is a *different* binary (``python3``) than any
allow-listed one, and gets denied even if quay.io itself is allow-listed for
curl. Shelling out to curl also means this tool has zero pip/PyPI dependency
at runtime - nothing to install in an environment that may not have `pip`
available at all, only what the base image already ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Dict, Optional
from urllib.parse import urlencode

QUAY_API_BASE = "https://quay.io/api/v1"
DEFAULT_TIMEOUT = 30
DEFAULT_CURL_BIN = "/usr/bin/curl"

# Written after the response body by curl's -w flag, then parsed back out.
# Long and unlikely enough not to collide with anything in a JSON body.
_STATUS_MARKER = "\n__QUAY_VULN_REPORT_HTTP_STATUS__:"


class QuayAPIError(RuntimeError):
    """Raised for any non-2xx response, or a transport-level failure."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class QuayAPIClient:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT, curl_bin: str = DEFAULT_CURL_BIN):
        self._timeout = timeout
        self._curl_bin = curl_bin

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{QUAY_API_BASE}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        if shutil.which(self._curl_bin) is None:
            raise QuayAPIError(
                f"'{self._curl_bin}' was not found on PATH - this tool shells out "
                "to curl for all HTTP requests and cannot run without it"
            )

        cmd = [
            self._curl_bin,
            "-sS",
            "--max-time",
            str(self._timeout),
            "-H",
            "Accept: application/json",
            "-w",
            _STATUS_MARKER + "%{http_code}",
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise QuayAPIError(f"failed to run {self._curl_bin} for {url}: {exc}") from exc

        if proc.returncode != 0:
            raise QuayAPIError(
                f"curl exited {proc.returncode} calling {url}: "
                f"{proc.stderr.strip() or '(no stderr output)'} - if this is a "
                "policy/permission-denied style error, quay.io:443 is likely not "
                "allow-listed for curl in the current sandbox network policy"
            )

        marker_at = proc.stdout.rfind(_STATUS_MARKER)
        if marker_at == -1:
            raise QuayAPIError(
                f"curl succeeded but its output for {url} didn't include the "
                "expected status marker - unexpected curl version/output format?"
            )
        body = proc.stdout[:marker_at]
        status_text = proc.stdout[marker_at + len(_STATUS_MARKER) :].strip()
        try:
            status_code = int(status_text)
        except ValueError:
            raise QuayAPIError(
                f"curl returned a non-numeric HTTP status for {url}: {status_text!r}"
            )

        if status_code == 404:
            raise QuayAPIError(
                f"{url} returned 404 - the repository, tag, or manifest doesn't "
                "exist, or the repository is private (this tool only supports "
                "public quay.io repositories)",
                status_code=404,
            )
        if status_code in (401, 403):
            raise QuayAPIError(
                f"{url} returned {status_code} - the repository appears to "
                "be private, or this request was blocked by network policy. "
                "This tool does not support authentication; only public "
                "quay.io repositories are supported",
                status_code=status_code,
            )
        if not (200 <= status_code < 300):
            raise QuayAPIError(
                f"{url} returned HTTP {status_code}: {body[:500]}",
                status_code=status_code,
            )

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise QuayAPIError(f"{url} did not return valid JSON: {exc}") from exc

    def resolve_tag_digest(self, full_repository: str, tag: str) -> str:
        """Look up the manifest digest for a tag.

        ``full_repository`` is ``namespace/repo-path`` (repo-path may itself
        contain slashes for nested quay.io repositories).

        If the tag resolves to a multi-arch manifest list rather than a
        single-platform manifest, this resolves further down to a scannable
        per-platform child digest - see ``_resolve_manifest_list_child_digest``.
        """
        data = self._get(
            f"/repository/{full_repository}/tag/",
            params={"specificTag": tag, "onlyActiveTags": "true"},
        )
        tags = data.get("tags") or []
        if not tags:
            raise QuayAPIError(
                f"no active tag named '{tag}' found on quay.io repository "
                f"'{full_repository}'"
            )
        tag_entry = tags[0]
        digest = tag_entry.get("manifest_digest")
        if not digest:
            raise QuayAPIError(
                f"tag '{tag}' on '{full_repository}' has no manifest_digest in "
                "the API response"
            )
        if tag_entry.get("is_manifest_list"):
            digest = self._resolve_manifest_list_child_digest(full_repository, digest)
        return digest

    def _resolve_manifest_list_child_digest(self, full_repository: str, list_digest: str) -> str:
        """Pick a scannable per-platform digest out of a multi-arch manifest list.

        quay.io's Clair-based scanner only ever scans per-platform child
        manifests - ``get_manifest_security`` called against the manifest-list
        digest itself always reports scan status "unsupported". Fetches the
        manifest list (no ``/security``), parses the embedded Docker
        manifest-list JSON out of ``manifest_data``, and prefers the
        linux/amd64 child, falling back to the first platform-specific child
        available if amd64 isn't present.
        """
        manifest = self._get(f"/repository/{full_repository}/manifest/{list_digest}")
        raw_manifest_data = manifest.get("manifest_data")
        if not raw_manifest_data:
            raise QuayAPIError(
                f"manifest list '{list_digest}' on '{full_repository}' has no "
                "manifest_data in the API response"
            )
        try:
            manifest_list = json.loads(raw_manifest_data)
        except (TypeError, json.JSONDecodeError) as exc:
            raise QuayAPIError(
                f"manifest list '{list_digest}' on '{full_repository}' has "
                f"unparseable manifest_data: {exc}"
            ) from exc

        children = [
            child for child in (manifest_list.get("manifests") or []) if child.get("digest")
        ]
        if not children:
            raise QuayAPIError(
                f"manifest list '{list_digest}' on '{full_repository}' has no "
                "usable platform-specific child manifests"
            )

        for child in children:
            platform = child.get("platform") or {}
            if platform.get("architecture") == "amd64" and platform.get("os") == "linux":
                return child["digest"]

        return children[0]["digest"]

    def get_manifest_security(self, full_repository: str, digest: str) -> Dict[str, Any]:
        """Fetch the vulnerability scan report for a manifest digest."""
        return self._get(
            f"/repository/{full_repository}/manifest/{digest}/security",
            params={"vulnerabilities": "true"},
        )
