"""Parsing for quay.io repository / manifest links.

quay.io repository URLs take the shape::

    https://quay.io/repository/<namespace>/<repo-path...>
    https://quay.io/repository/<namespace>/<repo-path...>/manifest/sha256:<digest>

``<repo-path...>`` is one or more path segments. Most quay.io repositories are a
single segment (``namespace=chcollin``, ``repo-path=dwarbot``), but some
organizations (e.g. Red Hat's internal tenants) nest repositories under extra
path segments (``namespace=redhat-services-prod``,
``repo-path=rosa-tenant/rosa-agent/rosa-agent``). Both are valid quay.io
"repository" identifiers and are handled identically here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, parse_qs

DIGEST_PREFIX = "sha256:"


class QuayLinkError(ValueError):
    """Raised when a link is not a well-formed quay.io repository/manifest URL."""


@dataclass(frozen=True)
class ParsedQuayLink:
    namespace: str
    repo_path: str
    digest: Optional[str] = None
    tag: Optional[str] = None

    @property
    def full_repository(self) -> str:
        """The ``namespace/repo-path`` form quay.io's API expects."""
        return f"{self.namespace}/{self.repo_path}"


def parse_quay_link(link: str) -> ParsedQuayLink:
    """Parse a quay.io repository or manifest URL.

    Accepts links such as:

    - https://quay.io/repository/chcollin/dwarbot
    - https://quay.io/repository/chcollin/dwarbot?tab=tags
    - https://quay.io/repository/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent
      /manifest/sha256:<64-hex>?tab=vulnerabilities&fixable=true

    Raises QuayLinkError for anything that isn't a quay.io repository link.
    """
    if not link or not link.strip():
        raise QuayLinkError("--link was empty")

    parsed = urlparse(link.strip())

    if parsed.scheme not in ("http", "https"):
        raise QuayLinkError(
            f"'{link}' does not look like a URL (missing http:// or https://)"
        )

    host = (parsed.netloc or "").lower()
    # allow an optional port, but reject any other host entirely.
    host_without_port = host.split(":", 1)[0]
    if host_without_port != "quay.io":
        raise QuayLinkError(
            f"unsupported host '{parsed.netloc}': this tool only supports quay.io "
            "links (not Docker Hub, GHCR, ECR, other registries, or self-hosted "
            "Quay/Project Quay instances)"
        )

    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2 or segments[0] != "repository":
        raise QuayLinkError(
            f"'{link}' is not a quay.io repository link "
            "(expected a path like /repository/<namespace>/<repo>...)"
        )

    remainder = segments[1:]
    namespace = remainder[0]

    digest: Optional[str] = None
    if "manifest" in remainder:
        manifest_idx = remainder.index("manifest")
        repo_segments = remainder[1:manifest_idx]
        digest_segments = remainder[manifest_idx + 1 :]
        if not digest_segments:
            raise QuayLinkError(
                f"'{link}' has a /manifest/ segment but no digest after it"
            )
        digest = digest_segments[0]
        if not digest.startswith(DIGEST_PREFIX):
            raise QuayLinkError(
                f"expected a 'sha256:<digest>' after /manifest/ in '{link}', "
                f"got '{digest}'"
            )
    else:
        repo_segments = remainder[1:]

    if not repo_segments:
        raise QuayLinkError(
            f"'{link}' is missing the repository path after the namespace "
            f"('{namespace}')"
        )

    repo_path = "/".join(repo_segments)

    tag = None
    query = parse_qs(parsed.query)
    if "tag" in query and query["tag"]:
        tag = query["tag"][0]

    return ParsedQuayLink(namespace=namespace, repo_path=repo_path, digest=digest, tag=tag)
