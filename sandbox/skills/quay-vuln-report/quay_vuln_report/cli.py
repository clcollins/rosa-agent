"""Command-line interface for quay-vuln-report."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .api_client import DEFAULT_CURL_BIN, QuayAPIClient, QuayAPIError
from .link_parser import QuayLinkError, parse_quay_link
from .report import ImageRef, SecurityReportError, build_report

DEFAULT_TAG = "latest"

PROG = "quay-vuln-report"

DESCRIPTION = (
    "Fetch fixable vulnerability data for a public image on quay.io "
    "(Quay Container Registry), via quay.io's REST API - not by scraping the "
    "quay.io web UI. THIS TOOL IS SPECIFIC TO QUAY.IO: it does not support "
    "Docker Hub, GHCR, ECR, Artifactory, or self-hosted Quay/Project Quay "
    "instances."
)

EPILOG = """\
examples:
  # From a quay.io UI link (repository or manifest link both work):
  %(prog)s --link https://quay.io/repository/chcollin/dwarbot

  %(prog)s --link "https://quay.io/repository/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent/manifest/sha256:2255511cf47eb4dfae90c51f2d94ad03a3fe1b9689a02f52ce3bc348f496c3db"

  # Equivalent, spelled out as separate fields (defaults to the "latest" tag):
  %(prog)s --repository chcollin --image dwarbot

  %(prog)s --repository redhat-services-prod --image rosa-tenant/rosa-agent/rosa-agent --tag latest

--link and --repository/--image are mutually exclusive: pick one style.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    parser.add_argument(
        "--link",
        metavar="URL",
        help=(
            "A full quay.io repository or manifest URL, e.g. "
            "https://quay.io/repository/<namespace>/<image-path>"
            "[/manifest/sha256:<digest>]. Mutually exclusive with "
            "--repository/--image."
        ),
    )
    parser.add_argument(
        "--repository",
        metavar="NAMESPACE",
        help=(
            "The quay.io namespace (organization or user), e.g. "
            "'redhat-services-prod' or 'chcollin'. Must be used with --image; "
            "mutually exclusive with --link."
        ),
    )
    parser.add_argument(
        "--image",
        metavar="PATH",
        help=(
            "The image path within the namespace, e.g. 'dwarbot' or the "
            "nested path 'rosa-tenant/rosa-agent/rosa-agent'. Required when "
            "--repository is given."
        ),
    )
    parser.add_argument(
        "--tag",
        metavar="TAG",
        default=None,
        help=(
            f"Tag to analyze (default: '{DEFAULT_TAG}'). Ignored if --link "
            "already points at a specific manifest digest."
        ),
    )
    parser.add_argument(
        "--include-non-fixable",
        action="store_true",
        help=(
            "Include vulnerabilities with no fixed version available "
            "(default: excluded, matching quay.io's own 'fixable=true' UI "
            "filter)."
        ),
    )
    parser.add_argument(
        "--sort-by",
        choices=["severity", "cve", "package", "layer"],
        default="severity",
        help="Sort order for the vulnerabilities list (default: severity).",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Write JSON to this file instead of stdout.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON instead of pretty-printed (indent=2).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        metavar="SECONDS",
        help="HTTP request timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--curl-bin",
        default=DEFAULT_CURL_BIN,
        metavar="PATH",
        help=(
            "Path to the curl binary used for all HTTP requests (default: "
            f"{DEFAULT_CURL_BIN}). This tool has no pip/PyPI dependency and "
            "makes every request by shelling out to curl, so it also works "
            "unmodified in network-policy sandboxes that allow-list curl by "
            "absolute path rather than allowing arbitrary interpreters."
        ),
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.link and (args.repository or args.image):
        parser.error("--link cannot be combined with --repository/--image")
    if not args.link and not args.repository and not args.image:
        parser.error("one of --link or --repository/--image is required")
    if args.repository and not args.image:
        parser.error("--image is required when --repository is given")
    if args.image and not args.repository:
        parser.error("--repository is required when --image is given")


def resolve_image(
    parser: argparse.ArgumentParser, args: argparse.Namespace, client: QuayAPIClient
) -> ImageRef:
    if args.link:
        try:
            parsed = parse_quay_link(args.link)
        except QuayLinkError as exc:
            parser.error(str(exc))
            raise  # unreachable, parser.error exits
        namespace, repo_path = parsed.namespace, parsed.repo_path
        tag = args.tag or parsed.tag or DEFAULT_TAG
        digest = parsed.digest
    else:
        namespace, repo_path = args.repository, args.image
        tag = args.tag or DEFAULT_TAG
        digest = None

    full_repository = f"{namespace}/{repo_path}"
    if digest is None:
        digest = client.resolve_tag_digest(full_repository, tag)
    else:
        tag = None  # an explicit digest overrides tag-based resolution

    return ImageRef(namespace=namespace, repo_path=repo_path, tag=tag, digest=digest)


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    client = QuayAPIClient(timeout=args.timeout, curl_bin=args.curl_bin)

    try:
        image = resolve_image(parser, args, client)
        raw = client.get_manifest_security(image.full_repository, image.digest)
        report = build_report(
            raw,
            image,
            include_non_fixable=args.include_non_fixable,
            sort_by=args.sort_by,
        )
    except (QuayAPIError, SecurityReportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    text = json.dumps(report, indent=indent, sort_keys=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
    else:
        print(text)

    return 0


def main() -> None:
    sys.exit(run())
