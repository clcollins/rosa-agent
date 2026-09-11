# ROSA-Agent

ROSA-Agent is a HyperShell gateway and a collection of service accounts and provider credentials for automating ROSA agentic tasks, including recurring scheduled repository maintenance, automated feature implementation and human-interactive sessions.

  * Scheduled jobs are Konflux cron jobs in the `rosa-tenant`
  * Automated feature implementation are one-shot, non-interactive ("Read this Jira and implement it")
 
## Open Questions

What triggers are available?

Currently:

  * Konflux cron jobs with a HyperShell Service Account credentials
  * Human interactive and non-interactive with OpenShell CLI

Future:

  * Jira NEW card creation?
  * Webhook?
  * Chai-bot/Slack?

## UBI9 Base Image

The sandbox image (`Containerfile`) is a multi-stage build on the Red Hat `registry.access.redhat.com/ubi9/ubi:9.8` base image rather than the Ubuntu-based NVIDIA OpenShell-Community image. Even so, it inherits the OpenShell scaffold from the NVIDIA base image — the agent skills (e.g. `github/SKILL.md`) are extracted via `COPY --from` and merged with our own skills under `/sandbox/.agents/skills/`, and the build mirrors the NVIDIA user model (supervisor + sandbox users, `HOME=/sandbox`, `/etc/openshell/policy.yaml`). On top of UBI9 it layers the `go-toolset` RPM and pinned Go tooling, Claude Code from Anthropic's signed RPM repo, and supporting CLIs (gh, golangci-lint, staticcheck, shellcheck), then pre-seeds Claude Code trust state and Bedrock/Jira runtime defaults so the sandbox runs non-interactively. WDYT?

Future consideations should include perhaps a non-Golang/language Boilerplate [openshift/boilerplate](https://github.com/openshift/boilerplate) image scaffolding for building/maintaining.

## Hypershell Gateway
  
  https://hypershell.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/gateways/3I94YwZezpdI4AEzuxtJnsVYGVt

  * This gateway is owned by Chris Collins right now - there's no "shared" gateway at the moment.
  * Service accounts are supported and created using the "Service Account" tab at the top
  * Service accounts expire every 90 days

### HyperShell Service Account

Service account can be created with:

```bash
hypershell service-account create \
  --gateway-id <gateway_id> \
  --name <account_name> \
  --description <description> \
  --role <openshell-user | openshell-admin> \
  --expiration <time>
```

Service Account CLI:

```bash
if [ -z "${OPENSHELL_OIDC_CLIENT_SECRET:-}" ]; then
  OPENSHELL_OIDC_CLIENT_SECRET=$(vault kv get -mount=osd-sre -field="hypershell-oidc-client-secret" rosa-agent)
  export OPENSHELL_OIDC_CLIENT_SECRET
fi

openshell gateway add \
  --name 'ROSA Agentic Devx-rosa-agent' \
  --oidc-issuer https://keycloak-ambient-keycloak.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/realms/ambient-code \
  --oidc-client-id hs-sa-3I94YwZezpdI4AEzuxtJnsVYGVt-3J3ccZA4F2wyrrGaIHorqtywaiL \
  --oidc-audience 'ROSA Agentic Devx-3I94YwZezpdI4AEzuxtJnsVYGVt' \
  https://gw-openshell-46b2dff2b7232948.openshell.stage.devshift.net:443

openshell -g 'ROSA Agentic Devx-rosa-agent' whoami --output json
```

```bash
if [ -z "${OPENSHELL_OIDC_CLIENT_SECRET:-}" ]; then
  OPENSHELL_OIDC_CLIENT_SECRET=$(vault kv get -mount=osd-sre -field="hypershell-oidc-client-secret" rosa-agent)
  export OPENSHELL_OIDC_CLIENT_SECRET
fi

ACCESS_TOKEN=$(printf '%s' "$OPENSHELL_OIDC_CLIENT_SECRET" | \
  curl --fail --silent --show-error --request POST https://keycloak-ambient-keycloak.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/realms/ambient-code/protocol/openid-connect/token \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode client_id=<client_id> $(vault kv get -mount=osd-sre -field="hypershell-oidc-client-id" rosa-agent)\
    --data-urlencode client_secret@- | \
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Service account config:

*NOTE:* This will overwrite your personal credentials for that gateway if you perform this task in the same 
environment you use the `openshell` cli tool.  This is an OpenShell limitation - the paths are hard-coded.
You may prefer to work with service accounts in a container instead.

```bash
# Register gateway config for non-interactive use
GW_NAME="<gateway_name>"
GW_DIR="$HOME/.config/openshell/gateways/${GW_NAME}"

ISSUER='https://keycloak-ambient-keycloak.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/realms/ambient-code' # HyperShell

ENDPOINT="<gateway_endpoint>" # From HyperShell Gateway-specific "details" tab
CLIENT_ID="<service_account_client_id>" # From HyperShell Gateway-specifc "service accounts" tab

mkdir -p "$GW_DIR"

# Write gateway metadata
cat > "$GW_DIR/metadata.json" <<EOF
{
  "name": "${GW_NAME}",
  "gateway_endpoint": "${ENDPOINT}",
  "is_remote": true,
  "gateway_port": 0,
  "auth_mode": "oidc",
  "oidc_issuer": "${ISSURER}",
  "oidc_client_id": "${CLIENT_ID}"
}
EOF
chmod 600 "$GW_DIR/metadata.json"

# Write the token
cat > "$GW_DIR/oidc_token.json" <<EOF
{
  "access_token": "${ACCESS_TOKEN}",
  "issuer": "${ISSURER}",
  "client_id": "${CLIENT_ID}"
}
EOF
chmod 600 "$GW_DIR/oidc_token.json"
```

Configuration can be validated with:

```bash
openshell -g $GW_NAME whoami
```

A working service account does NOT contain a `Name` field in the output.

```txt
Current User

  Subject: 4da987ed-f980-49ae-99f4-3d374126c4d6
  Provider: oidc
  Roles: openshell-user, openshell-admin
  Scopes: 
```

*Troubleshooting Service Account Issues*

`Error:   × No such file or directory (os error 2)` when running a sandbox

This is likely the `openssh-clients` package missing from the execution environment.  Openshell uses ssh with a `ProxyCommand=/usr/bin/openshell` to securely communicate with a sandbox after creation, even when they are non-interactive (`--no-tty`).  No other SSH configuration is necessary other than installing the SSH Clients package.

`Error:   × ssh exited with status exit status: 255`

Ensure your JWT has refreshed right before running the command.  Theoretically it's a 300s TTL, but in practice it doesn't seem to behave like this.  Need more data.




## Github

  * ROSA-Agent is a real user in Github: https://github.com/rosa-agent  
  * Credentials, including 2fa, are stored in Vault: https://vault.devshift.net/ui/vault/secrets/osd-sre
  * Github 2fa code generation is done with: `vault read totp/osd-sre/code/rosa-agent-github-2fa`
  * OpenShift org membership is managed by DPP
  * Openshift-Online org membership is managed in the `hybrid-platforms/org` repo in Gitlab.

### Token Considerations

  * Fine-grainted tokens are preferred and need only `contents:read`, `issues:read&write` and `pull_request:read&write` but require approval by the ORG owners, and must be created in that org
  * Classic PAT tokens work out-of the box with `repo` permissions (top-level check-box).  We're using this as a workaround until a fine-grained token is approved
  
Hypershell Provider config:

```bash
openshell provider create --name rosa-agent-github \
  --type github \
  --credential GITHUB_TOKEN
```

## Jira

Custom `Jira` skill is baked into the image to tell the sanbox agents how to read and comment on Jira cards.

  * ROSA-Agent is a real user in Jira: https://home.atlassian.com/o/4k7c08c0-9kb0-1aca-k606-d1417cc24104/people/712020:866a7c2a-31c4-45eb-bcc5-7edceb696a97?cloudId=2b9e35e3-6bd3-4cec-b838-f4249ee02432
  * Credentials, including 2fa, are stored in Vault: https://vault.devshift.net/ui/vault/secrets/osd-sre
  * Github 2fa code generation is done with: `vault read totp/osd-sre/code/rosa-agent-jira-2fa`
  * The API Token expires every 90 days

Hypershell Provider config:

```bash
export JIRA_API_TOKEN=$(vault)
openshell provider create --name "rosa-agent-jira" \
  --type atlassian-jira \
  --credential JIRA_API_TOKEN \
  --config JIRA_EMAI="sd-sre-platform+rosa-agent@redhat.com" \
  --config JIRA_BASE_URL="https://redhat.atlassian.net"
```

Note: The `atlassian-jira` Provider Profile is a custom profile.

```txt
Provider:

  Id: dc02f090-6396-4187-ae5a-df0436beac2b
  Name: rosa-agent-jira
  Type: atlassian-jira
  Resource version: 3
  Credential keys: JIRA_API_TOKEN
  Config keys: JIRA_EMAIL, JIRA_BASE_URL
```

Note: Sandboxes must be run with the Config keys as `ENV` variables.  There is no injection mechanism for Config keys in the Atlassian Jira provider config.

```bash
  --provider rosa-agent-jira \
  --env JIRA_EMAIL="sd-sre-platform+rosa-agent@redhat.com" \                                                          
  --env JIRA_BASE_URL="https://redhat.atlassian.net"      
```

## Vertex

  * ROSA-Agent has a service account under the `rosa-general` GCP account.
  * Credentials are stored in Vault: https://vault.devshift.net/ui/vault/secrets/osd-sre
  

Hypershell Provider config:

```txt
Provider:

  Id: a6959ff9-5277-4d9b-b547-f875c75a2cc2
  Name: rosa-general-vertex
  Type: google-vertex-ai
  Resource version: 131
  Credential keys: GOOGLE_SERVICE_ACCOUNT_KEY, GOOGLE_VERTEX_AI_SERVICE_ACCOUNT_TOKEN
  Config keys: VERTEX_AI_REGION, VERTEX_AI_PROJECT_ID
```

```bash
export GOOGLE_SERVICE_ACCOUNT_KEY=$(vault ) 
openshell provider create --name "rosa-general-vertex" \
  --type google-vertex-ai \
  --credential GOOGLE_SERVICE_ACCOUNT_KEY \
  --config VERTEX_AI_PROJECT_ID=hypershell-976970 \
  --config VERTEX_AI_REGION=global
```

Note: Sandboxes must be run with the `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` set as follows to work with Vertex:

```bash
  --provider rosa-general-vertex \
  --env=ANTHROPIC_BASE_URL=https://inference.local \
  --env=ANTHROPIC_API_KEY=unused
```

