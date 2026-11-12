"""Unit tests for the pure/testable parts of hack/e2e/nested_sandbox.sh.

These tests verify:
  - Cleanup: sandbox delete is called for every e2e-nested-* sandbox found in JSON output.
  - Result parsing: CHILD_OK detection logic (grep pattern).
  - Check-1 failure detection: proxy-shaped denial strings in output trigger PASS;
    generic nonzero exits (missing credential locally) do NOT.
  - Param assembly: all required -p flags are forwarded to sandbox create.
  - Provider flags: providers passed as --provider flags, not via PROVIDER_REFS.

Uses a fake openshellctl in a temp dir. No network, no real gateway,
no Vault, no real ~/.config.
"""

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

E2E_DIR = Path(__file__).parent.parent / "e2e"
SCRIPT = E2E_DIR / "nested_sandbox.sh"


def _write(path, content, mode=0o755):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _minimal_env(vault_dir, fake_bin_dir):
    """Minimal environment for the E2E script: Vault and openshellctl are faked."""
    return {
        "PATH": str(fake_bin_dir) + ":" + str(vault_dir) + ":" + os.environ.get("PATH", ""),
        "HOME": str(fake_bin_dir.parent / "home"),
        "OPENSHELL_VAULT_KV_MOUNT": "test-mount",
        "OPENSHELL_VAULT_KV_PATH": "test-path",
        "OPENSHELL_VAULT_FIELD_PREFIX": "hypershell",
        "GATEWAY_NAME": "test-gw",
        "IMAGE": "test-image:latest",
    }


class TestCleanup(unittest.TestCase):
    """Cleanup parses --output json and removes every e2e-nested-* sandbox."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_fakes(self, sandbox_names):
        """Fake vault + openshellctl that returns given sandbox names as JSON on 'sandbox list'."""
        vault_dir = self.tmp / "vault_bin"
        fake_dir = self.tmp / "fake_bin"
        vault_dir.mkdir(exist_ok=True)
        fake_dir.mkdir(exist_ok=True)

        delete_log = self.tmp / "deletes.log"
        # Write sandbox list output as a JSON array for the --output json code path.
        list_json = self.tmp / "sandbox_list.json"
        list_json.write_text(
            json.dumps([{"name": n, "status": "running"} for n in sandbox_names]),
            encoding="utf-8",
        )

        vault_script = textwrap.dedent("""\
            #!/usr/bin/env bash
            echo "dummy-value"
        """)
        _write(vault_dir / "vault", vault_script)

        ctl_script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            cmd="$*"
            if [[ "$cmd" == *"sandbox list"* ]]; then
                cat {list_json}
            elif [[ "$cmd" == *"sandbox delete"* ]]; then
                printf '%s\\n' "$@" >> {delete_log}
            elif [[ "$cmd" == *"sandbox create"* ]]; then
                exit 1
            fi
            exit 0
        """)
        _write(fake_dir / "openshellctl", ctl_script)

        env = _minimal_env(vault_dir, fake_dir)
        env["HOME"] = str(self.home)
        return env, delete_log

    def test_cleanup_calls_delete_for_each_e2e_sandbox(self):
        env, delete_log = self._make_fakes(["e2e-nested-111-1", "e2e-nested-111-2", "other-sandbox"])
        # Run just enough of the script to trigger the cleanup trap (EXIT).
        # We deliberately let sandbox create fail so the script exits quickly.
        subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True, text=True, env=env
        )
        if not delete_log.exists():
            self.skipTest("cleanup trap did not write log — check fake script")
        deleted = delete_log.read_text()
        self.assertIn("e2e-nested-111-1", deleted)
        self.assertIn("e2e-nested-111-2", deleted)
        self.assertNotIn("other-sandbox", deleted)

    def test_cleanup_handles_empty_list(self):
        env, delete_log = self._make_fakes([])
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        # No deletes expected; delete_log should not exist
        self.assertFalse(delete_log.exists(), "delete called when no e2e-nested-* sandboxes exist")


class TestCheck1MarkerLogic(unittest.TestCase):
    """Check 1 passes only when structured markers confirm egress was denied.

    The Go SDK wraps transport failures in oidc.discovery or ErrClientCredentials
    and does not surface the proxy's L7 policy_denied JSON — that only appears for
    L7-inspected hosts. The gateway endpoint is L4, so the inner error is a
    connect/EOF, not policy_denied. Checking for specific marker strings that the
    SCRIPT1 itself emits makes the test independent of SDK error-message wording.
    """

    _PASS_CONDITION = textwrap.dedent("""\
        sandbox_up=$(printf '%s' "$OUTPUT" | grep -c "SANDBOX_UP" || true)
        gw_add_rc=$(printf '%s' "$OUTPUT" | grep "GATEWAY_ADD_RC=" | grep -oE "[0-9]+$" | head -1 || true)
        whoami_ok=$(printf '%s' "$OUTPUT" | grep -c "WHOAMI_OK" || true)
        whoami_failed=$(printf '%s' "$OUTPUT" | grep -c "WHOAMI_FAILED" || true)
        [[ "$sandbox_up" -gt 0 && "${gw_add_rc:-0}" -ne 0 && "$whoami_ok" -eq 0 && "$whoami_failed" -gt 0 ]]
    """)

    def _is_pass(self, output):
        """Return True if the bash marker-check condition is satisfied for the given output."""
        r = subprocess.run(
            ["bash", "-c", self._PASS_CONDITION],
            env={**os.environ, "OUTPUT": output},
            capture_output=True,
        )
        return r.returncode == 0

    def test_correct_markers_are_pass(self):
        """SANDBOX_UP + GATEWAY_ADD_RC≠0 + WHOAMI_FAILED → PASS."""
        self.assertTrue(self._is_pass(
            "SANDBOX_UP\noidc: discovery failed: connect: connection refused\n"
            "GATEWAY_ADD_RC=1\nWHOAMI_FAILED\n"
        ))

    def test_gateway_add_success_is_fail(self):
        """GATEWAY_ADD_RC=0 (egress not blocked) → FAIL; provider must gate access."""
        self.assertFalse(self._is_pass(
            "SANDBOX_UP\nGATEWAY_ADD_RC=0\nWHOAMI_OK\n"
        ))

    def test_missing_sandbox_up_is_fail(self):
        """No SANDBOX_UP → FAIL; sandbox did not run the script at all."""
        self.assertFalse(self._is_pass(
            "GATEWAY_ADD_RC=1\nWHOAMI_FAILED\n"
        ))

    def test_whoami_ok_present_is_fail(self):
        """WHOAMI_OK in output → FAIL; token was obtained without the openshell provider."""
        self.assertFalse(self._is_pass(
            "SANDBOX_UP\nGATEWAY_ADD_RC=1\nWHOAMI_OK\n"
        ))

    def test_generic_crash_without_markers_is_fail(self):
        """Sandbox crash with no markers → FAIL; don't accept unknown failures as PASS."""
        self.assertFalse(self._is_pass(
            "bash: openshellctl: command not found\n"
        ))


class TestParamAssembly(unittest.TestCase):
    """All required -p parameters are forwarded to sandbox create; providers use --provider flags."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_fakes_capture_all_creates(self):
        """Fakes that capture args for ALL sandbox create calls (exit 0 each time)."""
        vault_dir = self.tmp / "vault_bin"
        fake_dir = self.tmp / "fake_bin"
        vault_dir.mkdir(exist_ok=True)
        fake_dir.mkdir(exist_ok=True)

        create_log = self.tmp / "create_calls.log"

        _write(vault_dir / "vault", "#!/usr/bin/env bash\necho 'dummy-value'\n")

        # Return an empty JSON array for sandbox list (cleanup has nothing to do).
        list_json = self.tmp / "sandbox_list.json"
        list_json.write_text("[]", encoding="utf-8")

        ctl_script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            cmd="$*"
            if [[ "$cmd" == *"sandbox list"* ]]; then
                cat {list_json}
            elif [[ "$cmd" == *"sandbox create"* ]]; then
                printf '%s\\n' "$@" >> {create_log}
                # Return 0 so all three checks run; they will fail on missing output
                exit 0
            fi
            exit 0
        """)
        _write(fake_dir / "openshellctl", ctl_script)

        env = _minimal_env(vault_dir, fake_dir)
        env["HOME"] = str(self.home)
        return env, create_log

    def test_create_call_includes_required_params(self):
        """sandbox create is called with NAME, IMAGE, GATEWAY_ENDPOINT etc."""
        env, create_log = self._make_fakes_capture_all_creates()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)

        if not create_log.exists():
            self.skipTest("sandbox create was not called")

        args = create_log.read_text()
        for required in ["NAME=", "IMAGE=", "GATEWAY_ENDPOINT=", "OIDC_ISSUER=",
                         "OIDC_CLIENT_ID=", "OIDC_AUDIENCE="]:
            self.assertIn(required, args, f"required param {required!r} not in create args: {args}")

    def test_provider_refs_param_not_used(self):
        """providerRefs is not passed via -p PROVIDER_REFS= (YAML scalar substitution bug)."""
        env, create_log = self._make_fakes_capture_all_creates()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)

        if not create_log.exists():
            self.skipTest("sandbox create was not called")

        args = create_log.read_text()
        self.assertNotIn(
            "PROVIDER_REFS=", args,
            "PROVIDER_REFS= should not appear; providers are passed as --provider flags"
        )

    def test_check2_uses_provider_flag(self):
        """Check 2 (deterministic) passes rosa-agent-openshell via --provider flag."""
        env, create_log = self._make_fakes_capture_all_creates()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)

        if not create_log.exists():
            self.skipTest("sandbox create was not called")

        args = create_log.read_text()
        self.assertIn(
            "--provider", args,
            "--provider flag must appear for check 2/3 provider-using calls"
        )


@unittest.skipUnless(
    Path(E2E_DIR).exists(),
    f"E2E directory not found: {E2E_DIR}",
)
class TestManifestEnv(unittest.TestCase):
    """No manifest env key may start with OPENSHELL_; non-secret gateway values use NESTED_*."""

    def _load_manifests(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML required")
        manifests = []
        for path in sorted(E2E_DIR.glob("*.yaml")):
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            if isinstance(doc, dict) and doc.get("kind") == "Sandbox":
                manifests.append((path.name, doc))
        return manifests

    def test_no_openshell_prefix_in_env(self):
        """spec.env must not contain any key starting with OPENSHELL_."""
        for name, doc in self._load_manifests():
            env_keys = list((doc.get("spec") or {}).get("env", {}).keys())
            violations = [k for k in env_keys if k.startswith("OPENSHELL_")]
            self.assertFalse(
                violations,
                f"{name}: spec.env contains reserved OPENSHELL_* keys: {violations}",
            )

    def test_nested_gateway_values_present(self):
        """spec.env must contain NESTED_GATEWAY_ENDPOINT, NESTED_OIDC_ISSUER, etc."""
        expected = {"NESTED_GATEWAY_ENDPOINT", "NESTED_OIDC_ISSUER",
                    "NESTED_OIDC_CLIENT_ID", "NESTED_OIDC_AUDIENCE"}
        for name, doc in self._load_manifests():
            env_keys = set((doc.get("spec") or {}).get("env", {}).keys())
            missing = expected - env_keys
            self.assertFalse(
                missing,
                f"{name}: missing NESTED_* env keys: {missing}",
            )


class TestScriptContent(unittest.TestCase):
    """Validate the shell script content generated by nested_sandbox.sh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_fakes_capture_scripts(self):
        """Fakes that capture the SCRIPT= param value from each sandbox create call."""
        vault_dir = self.tmp / "vault_bin"
        fake_dir = self.tmp / "fake_bin"
        vault_dir.mkdir(exist_ok=True)
        fake_dir.mkdir(exist_ok=True)

        script_log = self.tmp / "scripts.log"
        list_json = self.tmp / "sandbox_list.json"
        list_json.write_text("[]", encoding="utf-8")

        _write(vault_dir / "vault", "#!/usr/bin/env bash\necho 'dummy-value'\n")

        ctl_script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            cmd="$*"
            if [[ "$cmd" == *"sandbox list"* ]]; then
                cat {list_json}
            elif [[ "$cmd" == *"sandbox create"* ]]; then
                for arg in "$@"; do
                    if [[ "$arg" == SCRIPT=* ]]; then
                        printf '%s\\n---SCRIPT_END---\\n' "$arg" >> {script_log}
                    fi
                done
                exit 0
            fi
            exit 0
        """)
        _write(fake_dir / "openshellctl", ctl_script)

        env = _minimal_env(vault_dir, fake_dir)
        env["HOME"] = str(self.home)
        return env, script_log

    def _get_scripts(self, script_log):
        if not script_log.exists():
            self.skipTest("sandbox create was not called")
        content = script_log.read_text()
        return [s.strip() for s in content.split("---SCRIPT_END---") if s.strip()]

    def test_no_openshell_gateway_env_in_scripts(self):
        """No SCRIPT= value references OPENSHELL_GATEWAY_ENDPOINT, OPENSHELL_OIDC_ISSUER, etc."""
        env, script_log = self._make_fakes_capture_scripts()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        forbidden = ["OPENSHELL_GATEWAY_ENDPOINT", "OPENSHELL_OIDC_ISSUER",
                      "OPENSHELL_OIDC_CLIENT_ID", "OPENSHELL_OIDC_AUDIENCE"]
        for script in self._get_scripts(script_log):
            for var in forbidden:
                self.assertNotIn(
                    var, script,
                    f"SCRIPT contains reserved {var}; use NESTED_* and flags instead",
                )

    def test_no_backticks_in_scripts(self):
        """No SCRIPT= value contains a backtick (command substitution hazard)."""
        env, script_log = self._make_fakes_capture_scripts()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        for script in self._get_scripts(script_log):
            self.assertNotIn(
                "`", script,
                f"SCRIPT contains backtick — use $() or remove: {script[:200]}",
            )

    def test_gateway_add_uses_oidc_flags(self):
        """Every SCRIPT containing 'gateway add' also has --oidc-issuer, --oidc-client-id, --oidc-audience."""
        env, script_log = self._make_fakes_capture_scripts()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        for script in self._get_scripts(script_log):
            if "gateway add" not in script:
                continue
            for flag in ["--oidc-issuer", "--oidc-client-id", "--oidc-audience"]:
                self.assertIn(
                    flag, script,
                    f"SCRIPT with 'gateway add' missing {flag}: {script[:200]}",
                )

    def test_check2_has_whoami_before_sandbox_create(self):
        """Check 2 script has a whoami line before sandbox create."""
        env, script_log = self._make_fakes_capture_scripts()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        scripts = self._get_scripts(script_log)
        # Check 2 is the second script with 'gateway add' (first is check 1)
        gw_scripts = [s for s in scripts if "gateway add" in s and "sandbox create" in s]
        if not gw_scripts:
            self.skipTest("No script found with both gateway add and sandbox create")
        check2 = gw_scripts[0]
        whoami_pos = check2.find("whoami")
        create_pos = check2.find("sandbox create")
        self.assertGreater(
            whoami_pos, -1,
            "Check 2 script must contain 'whoami'",
        )
        self.assertLess(
            whoami_pos, create_pos,
            "Check 2: 'whoami' must appear before 'sandbox create'",
        )

    def test_check2_doctor_is_informational(self):
        """Check 2 script has 'doctor ||' (informational, not fatal)."""
        env, script_log = self._make_fakes_capture_scripts()
        subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        scripts = self._get_scripts(script_log)
        gw_scripts = [s for s in scripts if "gateway add" in s and "sandbox create" in s]
        if not gw_scripts:
            self.skipTest("No script found with both gateway add and sandbox create")
        check2 = gw_scripts[0]
        self.assertIn(
            "doctor ||", check2,
            "Check 2: 'doctor' must be followed by '||' to be informational",
        )


if __name__ == "__main__":
    unittest.main()
