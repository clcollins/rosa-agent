# ---------------------------------------------------------------------------
# `make sop-improve` — registers the OpenShell gateway, mints a short-lived
# OIDC access token via the client_credentials grant, and runs the
# `job-sop-improve` skill (job-sop-improve/SKILL.md, next to this file) in a
# one-shot sandbox (--no-keep --no-tty).
#
# Scheduled nightly by the `sop-improve` Konflux CronJob (see README's
# "Konflux configuration > Scheduled jobs" section), whose pod runs exactly
# `make sop-improve` with OPENSHELL_OIDC_CLIENT_SECRET injected from Vault as
# an environment variable. This file — not the CronJob YAML — is the source
# of truth for the gateway-registration/token-mint/sandbox-create steps.
#
# Every variable below is overridable from the environment or the `make`
# command line (e.g. `NAME=sop-improve-test make sop-improve`), with one
# deliberate exception: OPENSHELL_OIDC_CLIENT_SECRET has NO default here. It
# is a secret and must already be exported by the caller (Konflux; or a
# developer running this by hand — see README's "Vault-injected credential"
# and "HyperShell Service Account" sections). The
# require-openshell-oidc-client-secret prerequisite below fails the target
# immediately if it's missing rather than minting with an empty secret or
# silently falling back to anything else.
# ---------------------------------------------------------------------------

GW_NAME ?= ROSA Agentic Devx-rosa-agent
GW_ENDPOINT ?= https://gw-openshell-46b2dff2b7232948.openshell.stage.devshift.net:443
OIDC_ISSUER ?= https://keycloak-ambient-keycloak.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/realms/ambient-code
OIDC_CLIENT_ID ?= hs-sa-3I94YwZezpdI4AEzuxtJnsVYGVt-3J3ccZA4F2wyrrGaIHorqtywaiL
OIDC_AUDIENCE ?= ROSA Agentic Devx-3I94YwZezpdI4AEzuxtJnsVYGVt
NAME ?= sop-improve
PROMPT ?= /job-sop-improve
IMAGE ?= quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest
JIRA_EMAIL ?= sd-sre-platform+rosa-agent@redhat.com
JIRA_BASE_URL ?= https://redhat.atlassian.net

# Deliberately NOT given a `?=` default: OPENSHELL_OIDC_CLIENT_SECRET must
# come from the environment. See the comment block above.

GW_DIR = $(HOME)/.config/openshell/gateways/$(GW_NAME)

.PHONY: sop-improve require-openshell-oidc-client-secret

require-openshell-oidc-client-secret:
	@test -n "$$OPENSHELL_OIDC_CLIENT_SECRET" || { \
		echo "ERROR: OPENSHELL_OIDC_CLIENT_SECRET is not set; refusing to mint an OIDC token."; \
		echo "Export it from the environment (Konflux injects it from Vault -- see README's 'Vault-injected credential' section)."; \
		exit 1; \
	}

sop-improve: require-openshell-oidc-client-secret
	@echo "--- Registering the OpenShell gateway ($(GW_NAME)) ---"
	mkdir -p "$(GW_DIR)"
	printf '%s\n' \
	  '{' \
	  '  "name": "$(GW_NAME)",' \
	  '  "gateway_endpoint": "$(GW_ENDPOINT)",' \
	  '  "is_remote": true,' \
	  '  "gateway_port": 0,' \
	  '  "auth_mode": "oidc",' \
	  '  "oidc_issuer": "$(OIDC_ISSUER)",' \
	  '  "oidc_client_id": "$(OIDC_CLIENT_ID)",' \
	  '  "oidc_audience": "$(OIDC_AUDIENCE)"' \
	  '}' > "$(GW_DIR)/metadata.json"
	chmod 600 "$(GW_DIR)/metadata.json"
	@echo "--- Minting an OIDC access token via client_credentials ---"
	@# The client secret never appears in process args or shell expansion:
	@# printf pipes it to curl's stdin, and curl reads it via
	@# --data-urlencode 'client_secret@-'. The resulting access token is
	@# written to oidc_token.json by python3 (not a shell heredoc), so
	@# neither value is visible in /proc/*/cmdline, pod logs, or
	@# `kubectl describe` output.
	printf '%s' "$$OPENSHELL_OIDC_CLIENT_SECRET" | \
	  curl --fail --silent --show-error \
	    --request POST "$(OIDC_ISSUER)/protocol/openid-connect/token" \
	    --header 'Content-Type: application/x-www-form-urlencoded' \
	    --data-urlencode 'grant_type=client_credentials' \
	    --data-urlencode "client_id=$(OIDC_CLIENT_ID)" \
	    --data-urlencode 'client_secret@-' | \
	  python3 -c "import json, os, sys; d = json.load(sys.stdin); path = os.path.join('$(GW_DIR)', 'oidc_token.json'); open(path, 'w').write(json.dumps({'access_token': d['access_token'], 'issuer': '$(OIDC_ISSUER)', 'client_id': '$(OIDC_CLIENT_ID)'})); os.chmod(path, 0o600); print('token minted successfully', file=sys.stderr)"
	@echo "--- Verifying authentication ---"
	openshell -g "$(GW_NAME)" whoami
	@echo "--- Cleaning up any stale sandbox named $(NAME) ---"
	openshell -g "$(GW_NAME)" sandbox delete "$(NAME)" || true
	@echo "--- Creating and running the sandbox ---"
	openshell -g "$(GW_NAME)" sandbox create \
	  --name "$(NAME)" \
	  --from "$(IMAGE)" \
	  --provider rosa-agent-github \
	  --provider rosa-agent-jira \
	  --provider rosa-general-vertex \
	  --env ANTHROPIC_API_KEY=unused \
	  --env ANTHROPIC_BASE_URL=https://inference.local \
	  --env JIRA_EMAIL="$(JIRA_EMAIL)" \
	  --env JIRA_BASE_URL="$(JIRA_BASE_URL)" \
	  --no-keep \
	  --no-tty \
	  -- claude --dangerously-skip-permissions --print "$(PROMPT)"
