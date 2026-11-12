"""Tests for provider-profiles/*.yaml shape.

Requires PyYAML (python3-pyyaml), which is a lint build-root dependency.
Tests are skipped when the module is absent so the baseline stays clean
in environments that have not installed it.
"""

import fnmatch
import re
import unittest
from pathlib import Path

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

PROFILES_DIR = Path(__file__).parent.parent.parent / "provider-profiles"
PROFILE_PATH = PROFILES_DIR / "openshell.yaml"

# ProviderProfileCategory enum values at v0.1.5 (sdk/go/proto/openshellv1/openshell.pb.go).
# "infrastructure" is not in this enum — use "other" for categories that don't fit.
_VALID_CATEGORIES = frozenset(
    {"other", "inference", "agent", "source_control", "messaging", "data", "knowledge"}
)


def _is_keycloak(endpoint):
    return "keycloak." in endpoint.get("host", "")


def _is_gateway(endpoint):
    return "gw-openshell-" in endpoint.get("host", "")


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML required (install python3-pyyaml)")
class TestOpenshellProfileShape(unittest.TestCase):
    def setUp(self):
        with open(PROFILE_PATH, encoding="utf-8") as fh:
            self.profile = yaml.safe_load(fh)
        self.endpoints = self.profile.get("endpoints", [])

    # --- top-level fields ---

    def test_profile_file_exists(self):
        self.assertTrue(PROFILE_PATH.exists(), f"missing {PROFILE_PATH}")

    def test_id(self):
        self.assertEqual(self.profile["id"], "openshell")

    def test_category_is_valid_enum(self):
        cat = self.profile.get("category")
        self.assertIn(
            cat,
            _VALID_CATEGORIES,
            f"category {cat!r} is not a valid ProviderProfileCategory enum value; "
            f"valid values: {sorted(_VALID_CATEGORIES)}",
        )

    def test_inference_capable_false(self):
        self.assertIs(self.profile.get("inference_capable"), False)

    def test_binaries_exactly_openshellctl(self):
        self.assertEqual(
            self.profile.get("binaries"),
            ["/usr/local/bin/openshellctl"],
        )

    # --- credential ---

    def test_credential_env_var_present(self):
        creds = self.profile.get("credentials", [])
        all_env_vars = [v for c in creds for v in c.get("env_vars", [])]
        self.assertIn(
            "OPENSHELL_OIDC_CLIENT_SECRET",
            all_env_vars,
            "OPENSHELL_OIDC_CLIENT_SECRET must be declared as an env_var",
        )

    def test_credential_required(self):
        for cred in self.profile.get("credentials", []):
            if "OPENSHELL_OIDC_CLIENT_SECRET" in cred.get("env_vars", []):
                self.assertTrue(
                    cred.get("required"),
                    "OPENSHELL_OIDC_CLIENT_SECRET must be required: true",
                )

    # --- Keycloak endpoints ---

    def test_has_at_least_one_keycloak_endpoint(self):
        self.assertGreater(
            sum(1 for e in self.endpoints if _is_keycloak(e)),
            0,
            "at least one Keycloak endpoint required",
        )

    def test_keycloak_protocol_rest(self):
        for ep in self.endpoints:
            if _is_keycloak(ep):
                self.assertEqual(
                    ep.get("protocol"),
                    "rest",
                    f"Keycloak host {ep['host']} must have protocol: rest",
                )

    def test_keycloak_enforcement_enforce(self):
        for ep in self.endpoints:
            if _is_keycloak(ep):
                self.assertEqual(
                    ep.get("enforcement"),
                    "enforce",
                    f"Keycloak host {ep['host']} must have enforcement: enforce",
                )

    def test_keycloak_request_body_credential_rewrite(self):
        for ep in self.endpoints:
            if _is_keycloak(ep):
                self.assertTrue(
                    ep.get("request_body_credential_rewrite"),
                    f"Keycloak host {ep['host']} must have request_body_credential_rewrite: true",
                )

    def test_keycloak_exactly_two_rules(self):
        for ep in self.endpoints:
            if _is_keycloak(ep):
                rules = ep.get("rules", [])
                self.assertEqual(
                    len(rules),
                    2,
                    f"Keycloak host {ep['host']} must have exactly 2 rules, got {len(rules)}",
                )

    def test_keycloak_rule_get_discovery(self):
        for ep in self.endpoints:
            if _is_keycloak(ep):
                get_paths = [
                    r["allow"]["path"]
                    for r in ep.get("rules", [])
                    if r.get("allow", {}).get("method") == "GET"
                ]
                self.assertTrue(
                    any(".well-known/openid-configuration" in p for p in get_paths),
                    f"Keycloak host {ep['host']} must allow GET openid-configuration",
                )

    def test_keycloak_rule_post_token(self):
        for ep in self.endpoints:
            if _is_keycloak(ep):
                post_paths = [
                    r["allow"]["path"]
                    for r in ep.get("rules", [])
                    if r.get("allow", {}).get("method") == "POST"
                ]
                self.assertTrue(
                    any("openid-connect/token" in p for p in post_paths),
                    f"Keycloak host {ep['host']} must allow POST openid-connect/token",
                )

    # --- Gateway endpoints (L4 — no L7 fields) ---

    def test_has_at_least_one_gateway_endpoint(self):
        self.assertGreater(
            sum(1 for e in self.endpoints if _is_gateway(e)),
            0,
            "at least one gateway endpoint (gw-openshell-...) required",
        )

    def test_gateway_no_protocol(self):
        for ep in self.endpoints:
            if _is_gateway(ep):
                self.assertNotIn(
                    "protocol",
                    ep,
                    f"gateway host {ep['host']} must not have 'protocol' (must be L4)",
                )

    def test_gateway_no_access(self):
        for ep in self.endpoints:
            if _is_gateway(ep):
                self.assertNotIn(
                    "access",
                    ep,
                    f"gateway host {ep['host']} must not have 'access' field",
                )

    def test_gateway_no_rules(self):
        for ep in self.endpoints:
            if _is_gateway(ep):
                self.assertNotIn(
                    "rules",
                    ep,
                    f"gateway host {ep['host']} must not have 'rules' (L4 endpoint)",
                )

    def test_gateway_allow_uninspected_credentials(self):
        # The linter requires this field when the sandbox holds a declared
        # credential and the endpoint is L4. It is an explicit acknowledgement
        # that the credential does NOT travel to this host, not a security
        # relaxation. Without it, `openshell profile lint` fails.
        for ep in self.endpoints:
            if _is_gateway(ep):
                self.assertTrue(
                    ep.get("allow_uninspected_credentials"),
                    f"gateway host {ep['host']} must have allow_uninspected_credentials: true "
                    f"(linter requires explicit opt-in for L4 endpoints in credentialed profiles)",
                )

    def test_gateway_tls_skip(self):
        for ep in self.endpoints:
            if _is_gateway(ep):
                self.assertEqual(
                    ep.get("tls"),
                    "skip",
                    f"gateway host {ep['host']} must have tls: skip "
                    f"(without it the supervisor terminates TLS with ALPN http/1.1 only, "
                    f"which gRPC rejects with 'tls: no application protocol')",
                )

    def test_l7_endpoints_have_no_tls_key(self):
        for ep in self.endpoints:
            if ep.get("protocol"):
                self.assertNotIn(
                    "tls",
                    ep,
                    f"L7 endpoint {ep['host']} must not have a 'tls' key "
                    f"(tls: skip would disable credential rewrite)",
                )

    def test_gateway_port_443(self):
        for ep in self.endpoints:
            if _is_gateway(ep):
                self.assertEqual(
                    ep.get("port"),
                    443,
                    f"gateway host {ep['host']} must have port: 443",
                )

    # --- placeholder literal guard ---

    def test_no_raw_placeholder_literal(self):
        content = PROFILE_PATH.read_text(encoding="utf-8")
        # Build the forbidden prefix dynamically so this file itself does not
        # contain the literal (check_placeholder_literals.py would flag it).
        # The spaced form (env :*) is safe; the unspaced form is forbidden.
        _forbidden = ":".join(["openshell", "resolve", "env"]) + ":"
        match = re.search(re.escape(_forbidden) + r"[^\s]", content, re.IGNORECASE)
        self.assertIsNone(
            match,
            "raw credential-placeholder literal found; use 'openshell:resolve:env :' with a space",
        )


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML required (install python3-pyyaml)")
class TestAllProfileCategories(unittest.TestCase):
    """Every provider profile must use a valid ProviderProfileCategory enum value.

    Catches copy-paste errors (e.g. 'infrastructure') before the profile is
    linted against a live gateway, where the parse failure is less informative.
    Applies to every *.yaml in provider-profiles/ so new profiles are covered
    automatically.
    """

    def test_all_profiles_have_valid_category(self):
        yaml_files = sorted(PROFILES_DIR.glob("*.yaml"))
        self.assertTrue(yaml_files, f"no YAML files found in {PROFILES_DIR}")
        violations = []
        for path in yaml_files:
            with open(path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh)
            cat = profile.get("category") if isinstance(profile, dict) else None
            if cat not in _VALID_CATEGORIES:
                violations.append(
                    f"  {path.name}: category={cat!r} — "
                    f"must be one of {sorted(_VALID_CATEGORIES)}"
                )
        self.assertFalse(
            violations,
            "Invalid category in provider profile(s):\n" + "\n".join(violations),
        )


def host_pattern_matches(pattern, host):
    """Pure host-pattern matcher mirroring OpenShell's host_pattern.rs.

    Rules:
    - Both pattern and host are lower-cased and split on '.'.
    - A '**' label consumes one or more host labels.
    - Any other label is matched with fnmatch against exactly one host label.
    - All labels must be consumed for a match.
    """
    pattern = pattern.lower()
    host = host.lower()
    p_labels = pattern.split(".")
    h_labels = host.split(".")

    def _match(pi, hi):
        if pi == len(p_labels) and hi == len(h_labels):
            return True
        if pi == len(p_labels) or hi == len(h_labels):
            return False
        if p_labels[pi] == "**":
            for consume in range(1, len(h_labels) - hi + 1):
                if _match(pi + 1, hi + consume):
                    return True
            return False
        if fnmatch.fnmatchcase(h_labels[hi], p_labels[pi]):
            return _match(pi + 1, hi + 1)
        return False

    return _match(0, 0)


class TestHostPatternMatches(unittest.TestCase):
    """Table-driven tests for the pure host_pattern_matches function."""

    _CASES = [
        # (pattern, host, expected)
        ("**.infra.hypershell.app", "keycloak.hyp0.infra.hypershell.app", True),
        ("**.infra.hypershell.app", "gw-openshell-02c8f9e0680fb903.hyp2-spoke1.infra.hypershell.app", True),
        ("gw-openshell-*.*.infra.hypershell.app", "gw-openshell-02c8f9e0680fb903.hyp2-spoke1.infra.hypershell.app", True),
        ("gw-openshell-*.*.infra.hypershell.app", "gw-openshell-0000000000000000.hyp1-hub.infra.hypershell.app", True),
        ("gw-openshell-*.*.infra.hypershell.app", "gw-openshell-0000000000000000.hyp0-spoke2.infra.hypershell.app", True),
        ("gw-openshell-*.*.infra.hypershell.app", "keycloak.hyp0.infra.hypershell.app", False),
        ("gw-openshell-*.*.infra.hypershell.app", "keycloak.hyp1.infra.hypershell.app", False),
        ("gw-openshell-*.*.infra.hypershell.app", "keycloak.hyp2.infra.hypershell.app", False),
        ("keycloak.*.infra.hypershell.app", "keycloak.hyp0.infra.hypershell.app", True),
        ("keycloak.*.infra.hypershell.app", "keycloak.hyp1.infra.hypershell.app", True),
        ("keycloak.*.infra.hypershell.app", "keycloak.hyp2.infra.hypershell.app", True),
        ("keycloak.*.infra.hypershell.app", "keycloak.a.b.infra.hypershell.app", False),
        ("*.example.com", "api.example.com", True),
        ("*.example.com", "example.com", False),
        ("*.example.com", "deep.api.example.com", False),
        ("**.example.com", "deep.api.example.com", True),
        ("**.example.com", "example.com", False),
    ]

    def test_host_pattern_matching(self):
        for pattern, host, expected in self._CASES:
            with self.subTest(pattern=pattern, host=host):
                self.assertEqual(
                    host_pattern_matches(pattern, host),
                    expected,
                    f"host_pattern_matches({pattern!r}, {host!r}) should be {expected}",
                )


KEYCLOAK_HOSTS = [
    "keycloak.hyp0.infra.hypershell.app",
    "keycloak.hyp1.infra.hypershell.app",
    "keycloak.hyp2.infra.hypershell.app",
]
GATEWAY_HOSTS = [
    "gw-openshell-02c8f9e0680fb903.hyp2-spoke1.infra.hypershell.app",
    "gw-openshell-0000000000000000.hyp1-hub.infra.hypershell.app",
    "gw-openshell-0000000000000000.hyp0-spoke2.infra.hypershell.app",
]


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML required (install python3-pyyaml)")
class TestEndpointOverlap(unittest.TestCase):
    """No L4 (no-protocol) endpoint must match a Keycloak host.

    When a credentialed profile has two endpoints matching the same host:port,
    the supervisor combines their guards with all(). If one is L4
    (allow_uninspected_credentials, no protocol) and the other is L7
    (protocol: rest), the combined guard refuses the connection and the client
    sees a bare EOF. This test catches that regression.
    """

    def setUp(self):
        with open(PROFILE_PATH, encoding="utf-8") as fh:
            self.profile = yaml.safe_load(fh)
        self.endpoints = self.profile.get("endpoints", [])

    def test_no_l4_endpoint_matches_keycloak(self):
        """AC (a): no endpoint without protocol matches any Keycloak host."""
        l4_endpoints = [e for e in self.endpoints if "protocol" not in e]
        for ep in l4_endpoints:
            for kc_host in KEYCLOAK_HOSTS:
                self.assertFalse(
                    host_pattern_matches(ep["host"], kc_host),
                    f"L4 endpoint {ep['host']!r} matches Keycloak host {kc_host!r} — "
                    f"this causes the supervisor to refuse the connection (EOF)",
                )

    def test_l7_endpoint_matches_keycloak(self):
        """AC (b): an endpoint with protocol: rest matches each Keycloak host."""
        l7_endpoints = [e for e in self.endpoints if e.get("protocol") == "rest"]
        for kc_host in KEYCLOAK_HOSTS:
            matched = any(host_pattern_matches(e["host"], kc_host) for e in l7_endpoints)
            self.assertTrue(
                matched,
                f"No L7 (protocol: rest) endpoint matches Keycloak host {kc_host!r}",
            )

    def test_l4_endpoint_matches_gateways(self):
        """AC (c): L4 endpoint matches all gateway hosts."""
        l4_endpoints = [e for e in self.endpoints if "protocol" not in e]
        for gw_host in GATEWAY_HOSTS:
            matched = any(host_pattern_matches(e["host"], gw_host) for e in l4_endpoints)
            self.assertTrue(
                matched,
                f"No L4 endpoint matches gateway host {gw_host!r}",
            )

    def test_no_l7_endpoint_matches_gateways(self):
        """AC (c) cont: no L7 endpoint matches any gateway host."""
        l7_endpoints = [e for e in self.endpoints if "protocol" in e]
        for gw_host in GATEWAY_HOSTS:
            for ep in l7_endpoints:
                self.assertFalse(
                    host_pattern_matches(ep["host"], gw_host),
                    f"L7 endpoint {ep['host']!r} matches gateway host {gw_host!r}",
                )

    def test_partial_wildcard_only_in_first_label(self):
        """AC (d): a label containing * but not exactly * or ** appears only as first label."""
        for ep in self.endpoints:
            labels = ep["host"].split(".")
            for i, label in enumerate(labels):
                if "*" in label and label not in ("*", "**"):
                    self.assertEqual(
                        i, 0,
                        f"Partial wildcard {label!r} in endpoint {ep['host']!r} "
                        f"is at label index {i}, must be 0 (first label only)",
                    )


if __name__ == "__main__":
    unittest.main()
