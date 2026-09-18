"""Turn a raw quay.io /security API response into the tool's normalized report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Clair severities, worst first. Anything unrecognized sorts after these,
# alphabetically, rather than being dropped.
SEVERITY_ORDER = ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"]


class SecurityReportError(RuntimeError):
    """Raised when the raw API response doesn't look like a scan report we understand."""


@dataclass(frozen=True)
class ImageRef:
    namespace: str
    repo_path: str
    tag: Optional[str]
    digest: str
    registry: str = "quay.io"

    @property
    def full_repository(self) -> str:
        return f"{self.namespace}/{self.repo_path}"

    @property
    def source_url(self) -> str:
        return (
            f"https://{self.registry}/repository/{self.full_repository}"
            f"/manifest/{self.digest}?tab=vulnerabilities&fixable=true"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registry": self.registry,
            "namespace": self.namespace,
            "repository": self.repo_path,
            "full_repository": self.full_repository,
            "tag": self.tag,
            "digest": self.digest,
            "source_url": self.source_url,
        }


def _severity_sort_key(severity: str):
    try:
        return (SEVERITY_ORDER.index(severity), severity)
    except ValueError:
        return (len(SEVERITY_ORDER), severity or "")


def extract_vulnerabilities(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten the Layer -> Features -> Vulnerabilities tree from the API
    response into one record per (package, vulnerability) pair.

    Raises SecurityReportError if the response isn't in the expected shape
    (e.g. the scan hasn't completed yet, or quay.io changes its schema).
    """
    status = raw.get("status")
    if status not in ("scanned", None):
        # e.g. "queued", "failed" - no data to report yet.
        raise SecurityReportError(
            f"quay.io reports this manifest's scan status as '{status}', not "
            "'scanned' - there is no vulnerability data to fetch yet"
        )

    data = raw.get("data")
    if not data or "Layer" not in data:
        raise SecurityReportError(
            "unexpected response shape from the quay.io security API: no "
            "'data.Layer' field found"
        )

    layer = data["Layer"] or {}
    features = layer.get("Features") or []

    records: List[Dict[str, Any]] = []
    for feature in features:
        package_name = feature.get("Name", "")
        package_version = feature.get("Version", "")
        layer_id = feature.get("AddedBy", "")
        for vuln in feature.get("Vulnerabilities") or []:
            fixed_in = vuln.get("FixedBy") or None
            records.append(
                {
                    "cve": vuln.get("Name", ""),
                    "severity": vuln.get("Severity", "Unknown"),
                    "package": package_name,
                    "installed_version": package_version,
                    "fixed_in_version": fixed_in,
                    "fixable": bool(fixed_in),
                    "layer_introduced_in": layer_id,
                    "cve_link": vuln.get("Link", ""),
                    "description": vuln.get("Description", ""),
                }
            )
    return records


def build_report(
    raw: Dict[str, Any],
    image: ImageRef,
    include_non_fixable: bool = False,
    sort_by: str = "severity",
) -> Dict[str, Any]:
    """Build the final JSON-serializable report (a JSON object/map).

    By default this excludes vulnerabilities with no fixed version, matching
    quay.io's own UI "fixable=true" filter. Pass include_non_fixable=True to
    keep everything.
    """
    vulns = extract_vulnerabilities(raw)

    if not include_non_fixable:
        vulns = [v for v in vulns if v["fixable"]]

    if sort_by == "severity":
        vulns.sort(key=lambda v: (_severity_sort_key(v["severity"]), v["cve"]))
    elif sort_by == "cve":
        vulns.sort(key=lambda v: v["cve"])
    elif sort_by == "package":
        vulns.sort(key=lambda v: (v["package"], v["cve"]))
    elif sort_by == "layer":
        vulns.sort(key=lambda v: (v["layer_introduced_in"], v["cve"]))
    else:
        raise ValueError(f"unknown sort_by: {sort_by!r}")

    return {
        "image": image.to_dict(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixable_only": not include_non_fixable,
        "sorted_by": sort_by,
        "vulnerability_count": len(vulns),
        "vulnerabilities": vulns,
    }
