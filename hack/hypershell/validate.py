#!/usr/bin/env python3
"""Validate the .hypershell/ Kustomize tree before it is ever applied.

This is the correctness gate for .hypershell/. It exists because the two
things that will actually consume this tree -- `hsctl apply -k` and the
HyperShell RepositoryReconciler -- both fail *quietly* in the ways that
matter most:

  * A non-importable Kind renders fine and is then silently skipped by the
    import (Repository, ManagedCluster, Role and RoleBinding are logged and
    dropped). A typo in one of those fails without an error anywhere.
  * Repository.status is never written by anything, so there is no import
    health signal to check afterwards. Problems surface only in hyp0
    controller logs, which we cannot read.

`hsctl apply -k --dry-run` is explicitly NOT a substitute: it prints kind and
name only, validates nothing server-side, and still requires a live
authenticated connection.

So everything that can be checked offline is checked here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(
        "ERROR: PyYAML is required.\n"
        "  Fedora/RHEL: sudo dnf install -y python3-pyyaml\n"
        "  venv:        python3 -m pip install pyyaml"
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
HYPERSHELL_DIR = REPO_ROOT / ".hypershell"

# The seven Kinds the RepositoryReconciler will actually import.
# Source: components/control-plane/internal/reconciler/repository_writer.go
# (the Upsert switch). Anything outside this set is dead weight at best.
IMPORTABLE_KINDS = frozenset(
    {
        "AgentRuntime",
        "Gateway",
        "InferenceRoute",
        "ProviderBinding",
        "ProviderSpec",
        "SandboxTemplate",
        "SecretSource",
    }
)

# Kinds that render cleanly but are deliberately dropped by the import.
# Repository is excluded to prevent recursive self-registration; the rest are
# admin kinds. These are called out separately from "unknown kind" because
# they are the plausible mistake -- copying them out of the hypershell
# reference tree -- and because the failure is invisible at run time.
SILENTLY_SKIPPED_KINDS = frozenset(
    {"ManagedCluster", "Repository", "Role", "RoleBinding"}
)

# Each build root we render, and the Gateway name it must produce.
#
# The bare root is included so `kustomize build .hypershell/` is known-good,
# but it renders the prod identity and must never be used as an import_path
# or an `hsctl apply -k` target -- always apply an overlay. Pinning its name
# here means that fact is enforced rather than merely documented.
EXPECTED_GATEWAY_NAME = {
    ".hypershell": "rosa-agent",
    ".hypershell/overlays/integration": "rosa-agent-integration",
    ".hypershell/overlays/staging": "rosa-agent-staging",
    ".hypershell/overlays/prod": "rosa-agent",
}


class Findings:
    """Collects every problem so one run reports all of them, not just the
    first. A validator that stops at the first error turns a five-minute fix
    into five separate five-minute fixes."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def report(self) -> int:
        if not self.errors:
            print(f"OK: {len(EXPECTED_GATEWAY_NAME)} build roots valid.")
            return 0
        print(f"\n{len(self.errors)} problem(s) found:\n", file=sys.stderr)
        for err in self.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1


def require_kustomize() -> None:
    try:
        subprocess.run(
            ["kustomize", "version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        sys.exit(
            "ERROR: kustomize not found on PATH.\n"
            "  https://kubectl.docs.kubernetes.io/installation/kustomize/\n"
            "Note: HyperShell renders this tree with the upstream krusty API "
            "(sigs.k8s.io/kustomize/api), so a stock kustomize binary matches "
            "what the server will do."
        )
    except subprocess.CalledProcessError as exc:
        sys.exit(f"ERROR: kustomize is present but not runnable: {exc.stderr}")


def build(rel_path: str, findings: Findings) -> list[dict] | None:
    """Render one build root. Returns the parsed documents, or None if the
    render itself failed (in which case the per-resource checks are skipped
    for this root -- there is nothing meaningful to check)."""
    target = REPO_ROOT / rel_path
    result = subprocess.run(
        ["kustomize", "build", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        findings.error(rel_path, f"kustomize build failed:\n      {result.stderr.strip()}")
        return None
    try:
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    except yaml.YAMLError as exc:
        findings.error(rel_path, f"rendered output is not valid YAML: {exc}")
        return None
    if not docs:
        findings.error(rel_path, "rendered to zero resources")
        return None
    return docs


def check_resource(rel_path: str, doc: dict, index: int, findings: Findings) -> None:
    where = f"{rel_path} [doc {index}]"
    kind = doc.get("kind")

    if not kind:
        findings.error(where, "resource has no kind")
        return

    if not doc.get("apiVersion"):
        findings.error(where, f"{kind} has no apiVersion")

    # A nameless importable declaration is a hard import error: the reconciler
    # cannot own or prune it. repository_reconciler.go:438.
    metadata = doc.get("metadata") or {}
    if not metadata.get("name"):
        findings.error(where, f"{kind} has no metadata.name (hard import error)")

    # `hsctl apply` copies metadata.description straight into the create body
    # (cli/cmd/hsctl/apply/cmd.go:341-346), but no importable Kind's create
    # request accepts it -- of every *CreateRequest model in the API only the
    # gateway service account has a Description field. The API therefore
    # rejects the entire apply with
    #   400 ... Invalid request format: json: unknown field "description"
    # The import writer ignores the field instead, so a tree carrying it looks
    # fine under import and fails hard under hsctl. The hypershell reference
    # tree has this bug, so it is an easy one to copy in.
    if "description" in metadata:
        findings.error(
            where,
            f"{kind} sets metadata.description; hsctl apply forwards it and "
            f"the API rejects the request with 'unknown field \"description\"'. "
            f"Put the description in a YAML comment instead.",
        )

    if kind in SILENTLY_SKIPPED_KINDS:
        findings.error(
            where,
            f"{kind} is NOT importable -- it renders fine and is then silently "
            f"dropped by the import. Move it out of .hypershell/ "
            f"(see hack/hypershell/ for the manually-applied manifests).",
        )
    elif kind not in IMPORTABLE_KINDS:
        findings.error(
            where,
            f"{kind} is not a known HyperShell kind; the import rejects it as "
            f"an unsupported kind.",
        )

    check_spec_fields(where, kind, doc, findings)

    if kind == "Gateway":
        check_gateway(where, doc, findings)


def check_spec_fields(where: str, kind: str, doc: dict, findings: Findings) -> None:
    """Reject spec keys the Kind's create request does not declare, because
    the API decodes with DisallowUnknownFields and 400s the whole apply."""
    accepted = KIND_SPEC_FIELDS.get(kind)
    if accepted is None:
        return
    spec = doc.get("spec") or {}
    if not isinstance(spec, dict):
        findings.error(where, f"{kind} spec must be a mapping")
        return
    for key in sorted(set(spec) - accepted):
        findings.error(
            where,
            f"{kind} spec field {key!r} is not accepted by its create request; "
            f"the API rejects unknown fields outright. Accepted: "
            f"{', '.join(sorted(accepted))}.",
        )


def check_gateway(where: str, doc: dict, findings: Findings) -> None:
    """Gateway placement rules, enforced here because the server applies them
    at CREATE only and never exposes placement on the read surface -- a later
    import cannot correct a mistake, so it has to be right the first time."""
    spec = doc.get("spec") or {}

    # The api-server requires exactly one of cluster_id or placement and
    # rejects both-or-neither. We additionally forbid cluster_id outright:
    # pinning a gateway to a cluster ID defeats a portable tree and is the
    # thing MCS-223 AC3 exists to prevent.
    if "cluster_id" in spec:
        findings.error(
            where,
            "Gateway declares cluster_id; use placement selectors instead so "
            "the control plane binds a matching cluster (MCS-223 AC3).",
        )

    placement = spec.get("placement")
    if placement is None:
        findings.error(
            where,
            "Gateway declares no placement; with no cluster_id either, the "
            "api-server rejects the create.",
        )
        return
    if not isinstance(placement, dict):
        findings.error(where, "Gateway placement must be a mapping")
        return

    selector = placement.get("selector")
    mode = placement.get("mode")
    if selector:
        if not isinstance(selector, list) or not all(
            isinstance(s, str) for s in selector
        ):
            findings.error(where, "Gateway placement.selector must be a list of strings")
        else:
            check_capability_labels(where, selector, findings)
    elif mode == "local-kind":
        pass
    else:
        findings.error(
            where,
            "Gateway placement declares neither a non-empty selector nor "
            "mode: local-kind; the import writer errors on this.",
        )


# Accepted spec fields per Kind, taken from that Kind's *CreateRequest model
# in components/api-server/pkg/api/openapi/.
#
# This is a closed list on purpose. The generated models call
# decoder.DisallowUnknownFields(), so the API rejects a request carrying any
# field it does not know -- the whole apply fails with
#   400 ... Invalid request format: json: unknown field "<name>"
# `hsctl apply` copies every spec key into the request body verbatim, so one
# stray or misspelled key fails the apply rather than being ignored. Checking
# it here turns a round-trip to a live instance into an offline error.
#
# `name` is not listed: hsctl sets it from metadata.name, not from spec.
# Add an entry here for each new Kind as it is taken on.
KIND_SPEC_FIELDS = {
    # model_gateway_create_request.go
    "Gateway": frozenset(
        {
            "cluster_id",
            "placement",
            "external_dns",
            "tls_mode",
            "service_type",
            "status",
            "phase",
            "image",
            "supervisor_image",
            "server_dns_names",
            "oidc",
            "route",
            "credential_driver",
        }
    ),
}

# Capability labels are validated server-side against a closed set of
# namespaces, one value per namespace.
# Source: components/api-server/plugins/managedClusters/service.go
CAPABILITY_NAMESPACES = frozenset(
    {"provider", "network", "region", "runtime", "environment", "gateway-version"}
)


def check_capability_labels(where: str, selector: list[str], findings: Findings) -> None:
    seen: set[str] = set()
    for label in selector:
        if label.count("/") != 1:
            findings.error(
                where, f"capability label {label!r} is not in namespace/value form"
            )
            continue
        namespace, value = label.split("/", 1)
        if namespace not in CAPABILITY_NAMESPACES:
            findings.error(
                where,
                f"capability namespace {namespace!r} is not recognized; "
                f"valid: {', '.join(sorted(CAPABILITY_NAMESPACES))}",
            )
        if not value:
            findings.error(where, f"capability label {label!r} has an empty value")
        if namespace in seen:
            findings.error(
                where,
                f"capability namespace {namespace!r} appears more than once; "
                f"the server accepts only one value per namespace",
            )
        seen.add(namespace)


def check_gateway_name(rel_path: str, docs: list[dict], findings: Findings) -> None:
    gateways = [d for d in docs if d.get("kind") == "Gateway"]
    if len(gateways) != 1:
        findings.error(
            rel_path,
            f"expected exactly 1 Gateway, rendered {len(gateways)}",
        )
        return
    actual = (gateways[0].get("metadata") or {}).get("name")
    expected = EXPECTED_GATEWAY_NAME[rel_path]
    if actual != expected:
        findings.error(
            rel_path,
            f"Gateway name is {actual!r}, expected {expected!r}. Each instance "
            f"gets its own name so a wrong-context apply is visible rather "
            f"than silent.",
        )


def main() -> int:
    if not HYPERSHELL_DIR.is_dir():
        sys.exit(f"ERROR: {HYPERSHELL_DIR} does not exist")
    require_kustomize()

    findings = Findings()
    for rel_path in EXPECTED_GATEWAY_NAME:
        docs = build(rel_path, findings)
        if docs is None:
            continue
        for index, doc in enumerate(docs):
            check_resource(rel_path, doc, index, findings)
        check_gateway_name(rel_path, docs, findings)

    return findings.report()


if __name__ == "__main__":
    sys.exit(main())
