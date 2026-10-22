"""Tests for hack/hypershell/validate.py.

These cover the pure checks only. Rendering is left out deliberately: it
shells out to kustomize, and `make hypershell-validate` already exercises
that path against the real tree on every lint run.

Each case here corresponds to a way the live API rejects, or silently
accepts and then ignores, a manifest -- the point of the validator is to
turn those into offline errors.
"""

import contextlib
import io
import unittest

from hypershell.validate import (
    Findings,
    check_gateway,
    check_gateway_name,
    check_resource,
    check_spec_fields,
)


def gateway(**spec):
    """A minimal valid Gateway, overridable per test."""
    base = {"placement": {"selector": ["provider/ibm", "network/public"]}}
    base.update(spec)
    return {
        "apiVersion": "hypershell.redhat.io/v1",
        "kind": "Gateway",
        "metadata": {"name": "rosa-agent"},
        "spec": base,
    }


class TestCheckResource(unittest.TestCase):
    def test_accepts_a_valid_gateway(self):
        findings = Findings()
        check_resource("root", gateway(), 0, findings)
        self.assertEqual(findings.errors, [])

    def test_rejects_missing_kind(self):
        findings = Findings()
        check_resource("root", {"metadata": {"name": "x"}}, 0, findings)
        self.assertEqual(len(findings.errors), 1)
        self.assertIn("no kind", findings.errors[0])

    def test_rejects_missing_name(self):
        # A nameless importable declaration is a hard import error: the
        # reconciler cannot own or prune it.
        doc = gateway()
        doc["metadata"] = {}
        findings = Findings()
        check_resource("root", doc, 0, findings)
        self.assertTrue(any("metadata.name" in e for e in findings.errors))

    def test_rejects_missing_api_version(self):
        doc = gateway()
        del doc["apiVersion"]
        findings = Findings()
        check_resource("root", doc, 0, findings)
        self.assertTrue(any("apiVersion" in e for e in findings.errors))

    def test_rejects_metadata_description(self):
        # hsctl forwards metadata.description into the create body and the
        # API decodes with DisallowUnknownFields, so this 400s the apply.
        doc = gateway()
        doc["metadata"]["description"] = "a description"
        findings = Findings()
        check_resource("root", doc, 0, findings)
        self.assertTrue(any("description" in e for e in findings.errors))

    def test_rejects_non_importable_kind(self):
        # RoleBinding renders fine and is then silently dropped by the import.
        findings = Findings()
        check_resource(
            "root",
            {"apiVersion": "v1", "kind": "RoleBinding", "metadata": {"name": "rb"}},
            0,
            findings,
        )
        self.assertTrue(any("NOT importable" in e for e in findings.errors))

    def test_rejects_unknown_kind(self):
        findings = Findings()
        check_resource(
            "root",
            {"apiVersion": "v1", "kind": "Wombat", "metadata": {"name": "w"}},
            0,
            findings,
        )
        self.assertTrue(any("not a known HyperShell kind" in e for e in findings.errors))


class TestCheckSpecFields(unittest.TestCase):
    def test_accepts_declared_fields(self):
        findings = Findings()
        check_spec_fields("root", "Gateway", gateway(tls_mode="edge"), findings)
        self.assertEqual(findings.errors, [])

    def test_rejects_unknown_spec_key(self):
        # The API rejects any unrecognised field outright, so a typo fails
        # the whole apply rather than being ignored.
        findings = Findings()
        check_spec_fields("root", "Gateway", gateway(tls_modes="edge"), findings)
        self.assertEqual(len(findings.errors), 1)
        self.assertIn("tls_modes", findings.errors[0])

    def test_reports_every_unknown_key(self):
        findings = Findings()
        check_spec_fields("root", "Gateway", gateway(foo=1, bar=2), findings)
        self.assertEqual(len(findings.errors), 2)

    def test_ignores_kinds_without_a_declared_field_list(self):
        # Kinds not yet taken on are skipped rather than guessed at.
        findings = Findings()
        check_spec_fields("root", "AgentRuntime", {"spec": {"anything": 1}}, findings)
        self.assertEqual(findings.errors, [])

    def test_rejects_non_mapping_spec(self):
        findings = Findings()
        check_spec_fields("root", "Gateway", {"spec": ["not", "a", "map"]}, findings)
        self.assertTrue(any("must be a mapping" in e for e in findings.errors))


class TestCheckGateway(unittest.TestCase):
    def test_accepts_capability_selector(self):
        findings = Findings()
        check_gateway("root", gateway(), findings)
        self.assertEqual(findings.errors, [])

    def test_accepts_local_kind_mode(self):
        findings = Findings()
        check_gateway("root", gateway(placement={"mode": "local-kind"}), findings)
        self.assertEqual(findings.errors, [])

    def test_rejects_cluster_id(self):
        # Pinning defeats a portable tree; placement is the point (MCS-223 AC3).
        findings = Findings()
        check_gateway("root", gateway(cluster_id="01JABC"), findings)
        self.assertTrue(any("cluster_id" in e for e in findings.errors))

    def test_rejects_missing_placement(self):
        doc = gateway()
        del doc["spec"]["placement"]
        findings = Findings()
        check_gateway("root", doc, findings)
        self.assertTrue(any("no placement" in e for e in findings.errors))

    def test_rejects_empty_placement_block(self):
        # A placement block declaring neither selector nor mode makes the
        # import writer error.
        findings = Findings()
        check_gateway("root", gateway(placement={}), findings)
        self.assertTrue(any("neither" in e for e in findings.errors))

    def test_rejects_non_mapping_placement(self):
        findings = Findings()
        check_gateway("root", gateway(placement=["provider/ibm"]), findings)
        self.assertTrue(any("must be a mapping" in e for e in findings.errors))

    def test_rejects_non_string_selector_entries(self):
        findings = Findings()
        check_gateway("root", gateway(placement={"selector": [1, 2]}), findings)
        self.assertTrue(any("list of strings" in e for e in findings.errors))


class TestCapabilityLabels(unittest.TestCase):
    def test_rejects_unknown_namespace(self):
        # The server validates against a closed namespace set.
        findings = Findings()
        check_gateway("root", gateway(placement={"selector": ["cloud/bogus"]}), findings)
        self.assertTrue(any("not recognized" in e for e in findings.errors))

    def test_rejects_label_without_a_namespace(self):
        findings = Findings()
        check_gateway("root", gateway(placement={"selector": ["malformed"]}), findings)
        self.assertTrue(any("namespace/value form" in e for e in findings.errors))

    def test_rejects_empty_value(self):
        findings = Findings()
        check_gateway("root", gateway(placement={"selector": ["provider/"]}), findings)
        self.assertTrue(any("empty value" in e for e in findings.errors))

    def test_rejects_duplicate_namespace(self):
        # The server accepts only one value per namespace.
        findings = Findings()
        check_gateway(
            "root",
            gateway(placement={"selector": ["provider/ibm", "provider/aws"]}),
            findings,
        )
        self.assertTrue(any("more than once" in e for e in findings.errors))

    def test_accepts_every_valid_namespace(self):
        selector = [
            "provider/ibm",
            "network/public",
            "region/us-east",
            "runtime/slim",
            "environment/prod",
            "gateway-version/v1",
        ]
        findings = Findings()
        check_gateway("root", gateway(placement={"selector": selector}), findings)
        self.assertEqual(findings.errors, [])


class TestCheckGatewayName(unittest.TestCase):
    def test_accepts_the_expected_name(self):
        findings = Findings()
        check_gateway_name(".hypershell", [gateway()], findings)
        self.assertEqual(findings.errors, [])

    def test_rejects_an_unexpected_name(self):
        # Each instance gets its own name so a wrong-context apply is visible.
        doc = gateway()
        doc["metadata"]["name"] = "rosa-agent-staging"
        findings = Findings()
        check_gateway_name(".hypershell", [doc], findings)
        self.assertTrue(any("expected" in e for e in findings.errors))

    def test_rejects_zero_gateways(self):
        findings = Findings()
        check_gateway_name(".hypershell", [], findings)
        self.assertTrue(any("rendered 0" in e for e in findings.errors))

    def test_rejects_more_than_one_gateway(self):
        findings = Findings()
        check_gateway_name(".hypershell", [gateway(), gateway()], findings)
        self.assertTrue(any("rendered 2" in e for e in findings.errors))

    def test_each_overlay_expects_its_own_name(self):
        doc = gateway()
        doc["metadata"]["name"] = "rosa-agent-integration"
        findings = Findings()
        check_gateway_name(".hypershell/overlays/integration", [doc], findings)
        self.assertEqual(findings.errors, [])


class TestFindings(unittest.TestCase):
    def test_collects_every_problem_rather_than_stopping_at_the_first(self):
        doc = gateway(cluster_id="01JABC", tls_modes="edge")
        doc["metadata"]["description"] = "nope"
        findings = Findings()
        check_resource("root", doc, 0, findings)
        self.assertGreaterEqual(len(findings.errors), 3)

    def test_report_is_zero_when_clean(self):
        # report() prints its own summary; swallow it so a passing test run
        # does not look like the validator was invoked for real.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(Findings().report(), 0)

    def test_report_is_nonzero_when_problems_were_found(self):
        findings = Findings()
        findings.error("root", "something is wrong")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(findings.report(), 1)


if __name__ == "__main__":
    unittest.main()
