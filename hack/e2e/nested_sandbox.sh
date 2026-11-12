#!/usr/bin/env bash
# E2E test: nested sandbox creation via the openshell provider profile.
#
# HUMAN-RUN ONLY — not wired into `make test` because Prow/Konflux cannot
# reach the HyperShell OIDC endpoint (see Makefile "Scheduled jobs" comment).
#
# Prerequisites:
#   - VAULT_ADDR is set and a valid Vault token exists (run `make openshell-vault-login` first)
#   - OPENSHELL_VAULT_KV_MOUNT, OPENSHELL_VAULT_KV_PATH, OPENSHELL_VAULT_FIELD_PREFIX are set
#   - openshellctl is on PATH and the gateway is registered (run `make openshell-gateway` first)
#   - The openshell profile and provider are provisioned (run `make openshell-provider` first)
#
# Exit codes:
#   0  all three checks passed
#   1  check 1 did not deny as expected, or check 2 exited nonzero, or check 3 exited nonzero
#
# Cleanup: any e2e-nested-* sandboxes are deleted on EXIT via a trap.
# Inner-sandbox cleanup is NOT assumed — the host tears everything down.
#
# Usage:
#   bash hack/e2e/nested_sandbox.sh
#   # Optional overrides:
#   IMAGE=quay.io/... GATEWAY_NAME=rosa-agent bash hack/e2e/nested_sandbox.sh

set -euo pipefail

VAULT_MOUNT=${OPENSHELL_VAULT_KV_MOUNT:-osd-sre}
VAULT_PATH=${OPENSHELL_VAULT_KV_PATH:-rosa-agent}
VAULT_PREFIX=${OPENSHELL_VAULT_FIELD_PREFIX:-hypershell}
GATEWAY_NAME=${GATEWAY_NAME:-rosa-agent}
IMAGE=${IMAGE:-quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest}

# Read non-secret OIDC/gateway values from Vault so nothing environment-specific
# is hardcoded in this script.
GATEWAY_ENDPOINT=$(vault kv get -mount="$VAULT_MOUNT" -field="${VAULT_PREFIX}-gateway-endpoint" "$VAULT_PATH")
OIDC_ISSUER=$(vault kv get -mount="$VAULT_MOUNT" -field="${VAULT_PREFIX}-oidc-issuer" "$VAULT_PATH")
OIDC_CLIENT_ID=$(vault kv get -mount="$VAULT_MOUNT" -field="${VAULT_PREFIX}-oidc-client-id" "$VAULT_PATH")
OIDC_AUDIENCE=$(vault kv get -mount="$VAULT_MOUNT" -field="${VAULT_PREFIX}-oidc-audience" "$VAULT_PATH")

# Unique run id keeps concurrent E2E runs from colliding.
RUN_ID=$(date +%s)
PASS=0; FAIL=0

_info()  { printf '[e2e] %s\n' "$*"; }
_pass()  { printf '[e2e] PASS: %s\n' "$*"; PASS=$((PASS + 1)); }
_fail()  { printf '[e2e] FAIL: %s\n' "$*" >&2; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Cleanup: remove any leftover e2e-nested-* sandboxes from the host.
# Uses --output json so the name field is unambiguous regardless of table format.
# Does not rely on the inner sandbox cleaning up after itself.
# ---------------------------------------------------------------------------
_cleanup() {
    _info "Cleaning up e2e-nested-* sandboxes..."
    local names
    names=$(openshellctl -g "$GATEWAY_NAME" sandbox list --output json 2>/dev/null \
        | python3 -c "
import sys, json
data = json.load(sys.stdin)
sandboxes = data if isinstance(data, list) else data.get('sandboxes', data.get('items', []))
for s in sandboxes:
    name = s.get('name', s.get('metadata', {}).get('name', ''))
    if name.startswith('e2e-nested-'):
        print(name)
" 2>/dev/null) || true
    if [[ -n "$names" ]]; then
        while IFS= read -r name; do
            [[ -n "$name" ]] || continue
            openshellctl -g "$GATEWAY_NAME" sandbox delete "$name" 2>/dev/null || true
        done <<< "$names"
    fi
    _info "Cleanup done."
}
trap '_cleanup' EXIT

# ---------------------------------------------------------------------------
# Helper: create an outer sandbox, wait for it, return its exit code.
# Extra args (zero or more --provider NAME pairs) are forwarded verbatim.
# ---------------------------------------------------------------------------
_run_sandbox() {
    local name=$1 script=$2
    shift 2
    _info "Starting sandbox: $name"
    openshellctl -g "$GATEWAY_NAME" sandbox create \
        --name "$name" \
        -f "$(dirname "$0")/nested-sandbox.yaml" \
        -p "NAME=$name" \
        -p "IMAGE=$IMAGE" \
        -p "SCRIPT=$script" \
        -p "GATEWAY_ENDPOINT=$GATEWAY_ENDPOINT" \
        -p "OIDC_ISSUER=$OIDC_ISSUER" \
        -p "OIDC_CLIENT_ID=$OIDC_CLIENT_ID" \
        -p "OIDC_AUDIENCE=$OIDC_AUDIENCE" \
        --no-keep --no-tty --no-auto-providers \
        "$@" 2>&1
}

# ---------------------------------------------------------------------------
# Check 1: Negative control — no provider → egress must be denied.
#
# Without the openshell provider, the broker denies the connect() itself
# (EACCES), so the expected inner error is "connect: permission denied",
# not an L7 policy_denied body and not EOF. The Go SDK wraps transport
# failures in oidc.discovery or ErrClientCredentials; checking for specific
# error text is unreliable, so we use structured markers instead.
#
# Instead, SCRIPT1 emits structured markers:
#   SANDBOX_UP          — sandbox ran (not a local failure before create)
#   GATEWAY_ADD_RC=N    — exit code of openshellctl gateway add
#   WHOAMI_OK/FAILED    — whether openshellctl whoami succeeded
#
# PASS iff: SANDBOX_UP present AND GATEWAY_ADD_RC≠0 AND WHOAMI_FAILED (not OK).
# Denial-text patterns are printed as informational context only.
# ---------------------------------------------------------------------------
_info "=== Check 1: negative control (no provider) ==="
NAME1="e2e-nested-${RUN_ID}-1"
# Single-quoted so variable references expand inside the sandbox (not here).
# OPENSHELL_OIDC_CLIENT_SECRET=e2e-dummy causes openshellctl to attempt the
# Keycloak token POST; without the provider, the broker denies the connect()
# itself, so the expected inner error is connect: permission denied.
# ';' instead of '&&' so whoami always runs regardless of gateway add result.
SCRIPT1='echo SANDBOX_UP
OPENSHELL_OIDC_CLIENT_SECRET=e2e-dummy openshellctl \
  --oidc-issuer "$NESTED_OIDC_ISSUER" \
  --oidc-client-id "$NESTED_OIDC_CLIENT_ID" \
  --oidc-audience "$NESTED_OIDC_AUDIENCE" \
  gateway add "$NESTED_GATEWAY_ENDPOINT" --name inner
rc=$?
echo "GATEWAY_ADD_RC=$rc"
openshellctl whoami >/dev/null 2>&1 && echo WHOAMI_OK || echo WHOAMI_FAILED'
set +e
output1=$(_run_sandbox "$NAME1" "$SCRIPT1" 2>&1)
exit1=$?
set -e

# Extract markers
sandbox_up=$(echo "$output1" | grep -c "SANDBOX_UP" || true)
gw_add_rc=$(echo "$output1" | grep "GATEWAY_ADD_RC=" | grep -oE "[0-9]+$" | head -1 || true)
whoami_ok=$(echo "$output1" | grep -c "WHOAMI_OK" || true)
whoami_failed=$(echo "$output1" | grep -c "WHOAMI_FAILED" || true)

if [[ "$sandbox_up" -gt 0 && "${gw_add_rc:-0}" -ne 0 && "$whoami_ok" -eq 0 && "$whoami_failed" -gt 0 ]]; then
    _pass "Check 1: sandbox ran, gateway add denied (rc=$gw_add_rc), whoami failed — provider gate enforced"
else
    _fail "Check 1: expected SANDBOX_UP + GATEWAY_ADD_RC≠0 + WHOAMI_FAILED (not WHOAMI_OK); got sandbox_up=$sandbox_up gw_add_rc=${gw_add_rc:-unset} whoami_ok=$whoami_ok whoami_failed=$whoami_failed (exit=$exit1)"
    # Print any denial text as informational context
    echo "$output1" | grep -iE "policy.denied|policy_denied|credential_endpoint_mismatch|forbidden|denied" || true
    echo "--- full output ---"
    echo "$output1"
fi

# ---------------------------------------------------------------------------
# Check 2: Deterministic — with openshell provider, sandbox creates child.
# ---------------------------------------------------------------------------
_info "=== Check 2: deterministic nested sandbox ==="
NAME2="e2e-nested-${RUN_ID}-2"
CHILD2="${NAME2}-child"
SCRIPT2=$(cat <<'INNERSCRIPT'
set -euo pipefail
openshellctl \
  --oidc-issuer "$NESTED_OIDC_ISSUER" \
  --oidc-client-id "$NESTED_OIDC_CLIENT_ID" \
  --oidc-audience "$NESTED_OIDC_AUDIENCE" \
  gateway add "$NESTED_GATEWAY_ENDPOINT" --name inner
openshellctl -g inner whoami
# Informational only: doctor's Roles check reads raw token claims and
# false-fails for service accounts whose roles are assigned gateway-side.
openshellctl -g inner doctor || echo "DOCTOR_RC=$?"
openshellctl -g inner sandbox create \
    --name "${NAME}-child" \
    --from "$IMAGE" \
    --no-keep --no-tty --no-auto-providers \
    -- echo CHILD_OK
INNERSCRIPT
)

set +e
output2=$(_run_sandbox "$NAME2" "$SCRIPT2" --provider "rosa-agent-openshell" 2>&1)
exit2=$?
set -e

if [[ $exit2 -eq 0 ]] && echo "$output2" | grep -q "CHILD_OK"; then
    _pass "Check 2: child sandbox exited 0 and output contains CHILD_OK"
else
    _fail "Check 2: exit=$exit2, CHILD_OK=$(echo "$output2" | grep -c CHILD_OK || echo 0)"
    echo "--- output ---"
    echo "$output2"
fi

# ---------------------------------------------------------------------------
# Check 3: Agent-driven — Claude creates the child sandbox via openshellctl.
# Resolved values (non-secrets) are substituted directly so the Claude prompt
# has concrete values rather than unexpanded shell variable names.
# ---------------------------------------------------------------------------
_info "=== Check 3: agent-driven nested sandbox ==="
NAME3="e2e-nested-${RUN_ID}-3"
CHILD3="${NAME3}-child"
PROMPT3="Using openshellctl, register the gateway by running exactly: openshellctl --oidc-issuer ${OIDC_ISSUER} --oidc-client-id ${OIDC_CLIENT_ID} --oidc-audience ${OIDC_AUDIENCE} gateway add ${GATEWAY_ENDPOINT} --name inner. Then run: openshellctl -g inner whoami. Then create a sandbox named ${CHILD3} from image ${IMAGE} with --no-keep --no-tty --no-auto-providers whose command is: echo CHILD_OK. Print the output of the child sandbox verbatim and nothing else."
case "$PROMPT3" in
  *\'*|*\`*|*\$*|*\"*) echo "[e2e] FAIL: check 3 prompt contains a shell-active character" >&2; exit 1 ;;
esac
SCRIPT3="claude --dangerously-skip-permissions --print '${PROMPT3}'"

set +e
output3=$(_run_sandbox "$NAME3" "$SCRIPT3" \
    --provider "rosa-agent-openshell" \
    --provider "rosa-agent-vertex" 2>&1)
exit3=$?
set -e

if [[ $exit3 -eq 0 ]] && echo "$output3" | grep -q "CHILD_OK"; then
    _pass "Check 3: agent-driven child sandbox exited 0 and output contains CHILD_OK"
else
    _fail "Check 3: exit=$exit3, CHILD_OK=$(echo "$output3" | grep -c CHILD_OK || echo 0)"
    echo "--- output ---"
    echo "$output3"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
_info "Results: PASS=$PASS FAIL=$FAIL"
if [[ $FAIL -ne 0 ]]; then
    echo "[e2e] FAILED ($FAIL checks failed)" >&2
    exit 1
fi
echo "[e2e] All $PASS checks passed."
