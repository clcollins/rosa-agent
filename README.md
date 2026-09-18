# ROSA-Agent

![ROSA-Agent architecture: how the pieces come together](img/rosa-agent-architecture.png)

ROSA-Agent is a HyperShell gateway and a collection of service accounts and provider credentials for automating ROSA agentic tasks, including recurring scheduled repository maintenance, automated feature implementation and human-interactive sessions. It is owned and maintained by the [ROSA Agentic DevX](https://github.com/openshift-online/rosa-agentic-devx) team.

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

## Jobs

Jobs are non-interactive, scheduled tasks the agent runs (typically as Konflux cron jobs) using a
baked-in skill that scopes the work. Job skills live under `sandbox/skills/`.

* **`job-sop-improve`** (`sandbox/skills/job-sop-improve/SKILL.md`) — grooms one stale SOP in
    [openshift/ops-sop](https://github.com/openshift/ops-sop) per run. It first fast-forwards the
    agent's fork to upstream's default branch, then selects a single SOP at random from those not
    updated in the last 3 months and not already in an open PR. Stale SOPs (3 months–3 years old) are
    improved by wrapping that repo's own `/sop-improve auto-commit` skill and opening a PR (labeled
    `ROSA-Agent`) from a feature branch. SOPs untouched for over 3 years are not edited — instead the
    job files a GitHub Issue against upstream suggesting the SOP be evaluated as potentially obsolete.
    Any failure that stops the job opens an Issue against this repo (`openshift-online/rosa-agent`).
    This job is scheduled as a Konflux cron job in the `rosa-tenant` tenant.

## Hypershell Gateway
  
  <https://hypershell.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/gateways/3I94YwZezpdI4AEzuxtJnsVYGVt>

* This gateway is owned by Chris Collins right now - there's no "shared" gateway at the moment.
* Service accounts are supported and created using the "Service Account" tab at the top
* Service accounts expire every 90 days

### Policies

Sandbox behavior is governed by a HyperShell policy file (`/etc/openshell/policy.yaml` in the
image). The default policy shipped in this repo lives at `policies/default.yaml`.

The default policy is a deny-by-default egress and filesystem policy — the sandbox can only reach
the hosts explicitly listed, and each rule pins the specific binaries allowed to make those calls.
It defines two things:

* **`filesystem_policy`** — includes the workdir, and grants read-write to `/tmp` and `/dev/null`.
    `/dev/null` must be declared explicitly, otherwise bash login shells fail silently when Landlock
    blocks `/etc/profile` redirections. Baseline paths (`/usr`, `/lib`, `/etc`, `/proc`, `/tmp`,
    `/dev/urandom`) are added automatically by the supervisor.
* **`network_policies`** — per-service egress allow-lists, each scoped to specific binaries
    (`/usr/bin/claude`, `git`, `gh`, `glab`, `go`, `curl`) with `enforce` enforcement:
  * **Model inference** — Bedrock (`bedrock-runtime.us-east-2`, SigV4-signed), Anthropic API
      (`api.anthropic.com`, for Claude Code WebFetch/WebSearch), and Google Vertex AI
      (`oauth2.googleapis.com`, `aiplatform.googleapis.com`).
  * **GitHub** — read-write to `api.github.com` and `github.com`; read-only to
      `codeload.github.com`, `objects.githubusercontent.com`, and GitHub Actions hosts (CI status).
  * **GitLab** — read-write to `gitlab.cee.redhat.com` (git + `glab`).
  * **Go tooling** — read-only to `proxy.golang.org`, `sum.golang.org`, and `pkg.go.dev`.
  * **Reference / CI** — read-only to Red Hat docs, Konflux, Codecov, and Prow.

Note: Jira egress is intentionally **not** in this baked policy. The `atlassian-jira` provider
profile composes its own endpoints with two-writes-only (comment + remotelink) method/path
enforcement; a coarse read-write block here would union over and defeat that restriction. Attach
the Jira provider to grant Jira access rather than adding it to the policy. WDYT?

### Provider Profiles

Provider profiles are custom OpenShell provider definitions that declare how a credential is
injected into a sandbox and, for `rest`-type providers, exactly which endpoints/methods/paths the
sandbox proxy will allow. They are stored in this repo under `provider-profiles/` and imported with:

```bash
openshell provider profile lint -f provider-profiles/<profile>.yaml
openshell provider profile import -f provider-profiles/<profile>.yaml
```

Currently there is one profile:

* **`atlassian-jira.yaml`** — Jira Cloud access for sandboxed agents, with a deliberately narrow
    allow-list:
  * **Reads** — everything under `GET /rest/api/3/**` and `GET /rest/agile/1.0/**` (get issue,
      comments, transitions, editmeta/createmeta, field IDs, projects, and `/myself`), plus
      unauthenticated `GET /_edge/tenant_info` for cloudId discovery.
  * **Writes** — enumerated explicitly and narrowly: JQL search (`POST .../search/jql`), create
      issue, edit issue, comment, add remote (web) link, transition, and self-assign. Deletes, bulk
      operations, attachments, worklogs, watchers/votes, issue links, and admin endpoints are
      intentionally excluded — add them as explicit rules only if a real need arises, rather than
      widening POST/PUT with a catch-all.
  * Rules are declared twice: once for the classic site host (`redhat.atlassian.net`) and once for
      the API-gateway host (`api.atlassian.com`, prefixed with `/ex/jira/{cloudId}`) required by
      scoped tokens.
  * **Credentials/config** — only `JIRA_API_TOKEN` is a secret managed by the profile; it becomes
      an opaque, proxy-resolved placeholder inside the sandbox. `JIRA_EMAIL` and `JIRA_BASE_URL` are
      **not** secrets and must be passed as plain `--env` values at sandbox creation time (custom
      profiles have no mechanism to expose `--config` values as sandbox env vars).

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

### Troubleshooting Service Account Issues

`Error:   × No such file or directory (os error 2)` when running a sandbox

This is likely the `openssh-clients` package missing from the execution environment.  Openshell uses ssh with a `ProxyCommand=/usr/bin/openshell` to securely communicate with a sandbox after creation, even when they are non-interactive (`--no-tty`).  No other SSH configuration is necessary other than installing the SSH Clients package.

`Error:   × ssh exited with status exit status: 255`

Ensure your JWT has refreshed right before running the command.  Theoretically it's a 300s TTL, but in practice it doesn't seem to behave like this.  Need more data.

### Refreshing your gateway token (interactive use)

Once a gateway is registered (see above), you interact with it directly through
the `openshell` CLI, `-g <gateway_name>`:

```bash
GW_NAME='ROSA Agentic Devx-rosa-agent'

openshell -g "$GW_NAME" whoami
openshell -g "$GW_NAME" sandbox create --name demo
```

The `client_credentials` access token in `oidc_token.json` is short-lived and
there is no refresh token — it must be re-minted. The
[`hack/refresh_openshell_token.py`](hack/refresh_openshell_token.py) helper
re-mints the token in place from the OIDC client secret (read from
`OPENSHELL_OIDC_CLIENT_SECRET`, falling back to Vault) so you don't hit the
`ssh exited with status 255` auth failure above:

```bash
# Re-mint the gateway's token file:
hack/refresh_openshell_token.py -g "$GW_NAME"

# Only re-mint if fewer than 90s of life remain:
hack/refresh_openshell_token.py -g "$GW_NAME" --if-expiring 90

# Refresh the token, then run — this is the reliable pattern. The script
# re-mints as needed and retries once on auth failure, then execs the
# openshell command after `--exec --`:
export OPENSHELL_OIDC_CLIENT_SECRET=...   # or let the script read Vault
hack/refresh_openshell_token.py -g "ROSA Agentic Devx" \
  --exec -- sandbox create --name "${USER}-$(date +%s)" \
    --from quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest \
    --provider rosa-general-vertex \
    --env=ANTHROPIC_BASE_URL=https://inference.local \
    --env=ANTHROPIC_API_KEY=unused \
    --provider rosa-agent-github --provider rosa-agent-jira \
    --env JIRA_EMAIL="sd-sre-platform+rosa-agent@redhat.com" \
    --env=JIRA_BASE_URL="https://redhat.atlassian.net" \
    --no-keep --no-tty \
    -- claude --print "Ping"
```

It reads config (issuer, client id) from the gateway's `metadata.json`, so no
flags beyond `-g` are needed for a gateway registered as above. Run
`hack/refresh_openshell_token.py --help` for the full flag list (Vault
mount/path/field overrides, `--no-browser`, `--no-vault-login`, etc.).

## Github

* ROSA-Agent is a real user in Github: <https://github.com/rosa-agent>  
* Credentials, including 2fa, are stored in Vault: <https://vault.devshift.net/ui/vault/secrets/osd-sre>
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

Note: The source for that skill lives in this repo at `sandbox/skills/jira/SKILL.md`. It teaches the
sandbox agent to detect the token type (scoped Bearer against `api.atlassian.com` vs. classic Basic
against `redhat.atlassian.net`), derive the tenant cloudId, and call the Jira Cloud REST API through
a `jira` curl helper — staying within the reads/comment/remotelink operations the
[`atlassian-jira` provider profile](#provider-profiles) permits.

* ROSA-Agent is a real user in Jira: <https://home.atlassian.com/o/4k7c08c0-9kb0-1aca-k606-d1417cc24104/people/712020:866a7c2a-31c4-45eb-bcc5-7edceb696a97?cloudId=2b9e35e3-6bd3-4cec-b838-f4249ee02432>
* Credentials, including 2fa, are stored in Vault: <https://vault.devshift.net/ui/vault/secrets/osd-sre>
* Github 2fa code generation is done with: `vault read totp/osd-sre/code/rosa-agent-jira-2fa`
* The API Token expires every 90 days

Hypershell Provider config:

```bash
export JIRA_API_TOKEN=$(vault kv get -mount=osd-sre -field="jira-token" rosa-agent)
openshell provider create --name "rosa-agent-jira" \
  --type atlassian-jira \
  --credential JIRA_API_TOKEN \
  --config JIRA_EMAI="sd-sre-platform+rosa-agent@redhat.com" \
  --config JIRA_BASE_URL="https://redhat.atlassian.net"
```

Note: The `atlassian-jira` Provider Profile is a custom profile, defined in this repo at
`provider-profiles/atlassian-jira.yaml`. See the [Provider Profiles](#provider-profiles) section
for a summary of what it allows and how to import it.

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
* Credentials are stored in Vault: <https://vault.devshift.net/ui/vault/secrets/osd-sre>
  
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
export GOOGLE_SERVICE_ACCOUNT_KEY=$(vault kv get -mount=osd-sre -field="google-rosa-general-service-account-keyfile" rosa-agent)
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

## Konflux configuration

ROSA-Agent's Konflux resources live in the `rosa-tenant` namespace on the
`kflux-prd-rh02` cluster. They are managed via GitOps in the
[`releng/konflux-release-data`](https://gitlab.cee.redhat.com/releng/konflux-release-data)
repository and deployed by ArgoCD once merged to `main`. The links below point
at the files as they will exist on `main` after the onboarding MR merges.

### Image build

A Konflux `Application` + `Component` builds the ROSA-Agent container image from
this repository (`Containerfile` on the `main` branch), publishing to
`quay.io/redhat-user-workloads/rosa-tenant/rosa-agent`. The build itself is set
up in this repo via Pipelines-as-Code (`.tekton/`); the tenant config only
declares the Application/Component/ImageRepository and release wiring.

Built from a shared base via a kustomize overlay:

* [`overlay/rosa-agent/main/kustomization.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/overlay/rosa-agent/main/kustomization.yaml)
* [`application-patch.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/overlay/rosa-agent/main/application-patch.yaml)
* [`component-patch.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/overlay/rosa-agent/main/component-patch.yaml)
  — git URL, `Containerfile`, `main` revision
* [`image-repository.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/overlay/rosa-agent/main/image-repository.yaml)
  — `rosa-tenant/rosa-agent`, public
* [`releaseplan-patch.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/overlay/rosa-agent/main/releaseplan-patch.yaml)
* [`integrationtestscenario-patch.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/overlay/rosa-agent/main/integrationtestscenario-patch.yaml)

### Scheduled jobs

Two plain `batch/v1` CronJobs run `make` targets from this repository. Each has
a dedicated `ServiceAccount` bound to `konflux-maintainer-user-actions`, and
consumes `OPENSHELL_OIDC_CLIENT_SECRET` (see below) as an environment variable.

| Job | Schedule | Command | ServiceAccount |
| --- | --- | --- | --- |
| `sop-improve` | nightly (`0 3 * * *`) | `make sop-improve` | `rosa-agent-bot-0` |
| `sdlc-maturity` | weekly, Sun (`0 4 * * 0`) | `make sdlc-maturity` | `rosa-agent-bot-1` |

* [`sop-improve-cronjob.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/sop-improve-cronjob.yaml)
  / [`sop-improve-rbac.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/sop-improve-rbac.yaml)
* [`sdlc-maturity-cronjob.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/sdlc-maturity-cronjob.yaml)
  / [`sdlc-maturity-rbac.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/sdlc-maturity-rbac.yaml)

### Vault-injected credential

`OPENSHELL_OIDC_CLIENT_SECRET` is pulled from Vault by the External Secrets
Operator using an AppRole, and materialized as a Kubernetes Secret named
`openshell-oidc` that the CronJobs mount. It reads the `hypershell-oidc-client-secret`
field of `rosa-agent-konflux` on the `osd-sre` Vault mount (`vault kv get -mount=osd-sre
-field=hypershell-oidc-client-secret rosa-agent-konflux`), via a dedicated
`osd-sre-vault` `SecretStore`.

* [`secretstore/osd-sre-vault.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/secretstore/osd-sre-vault.yaml)
  — `SecretStore` for the `osd-sre` mount (AppRole `rosa-agent`)
* [`externalsecret/openshell-oidc.yaml`](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/tenants-config/cluster/kflux-prd-rh02/tenants/rosa-tenant/externalsecret/openshell-oidc.yaml)
  — `ExternalSecret` producing the `openshell-oidc` Secret

Bootstrapping still required outside the GitOps repo: create the `rosa-agent`
Vault AppRole and set its `roleId` in `osd-sre-vault.yaml`, and create the
`osd-sre-vault-app-role-secret` Kubernetes Secret (key `secret-id`) in the
`rosa-tenant` namespace.

The Vault side of that AppRole is managed in app-interface. A
[replication policy](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/vault.devshift.net/config/ci-ext/policies/replication-policies/osd-sre-rosa-agent-replication-policy.yml)
copies just the `osd-sre/rosa-agent-konflux` secret from the primary
`vault.devshift.net` to `vault.ci.ext.devshift.net`, where Konflux authenticates.
The [`rosa-agent` AppRole](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/vault.devshift.net/config/ci-ext/roles/approles/rosa-agent-approle.yml)
is granted read on that replicated secret by its
[access policy](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/vault.devshift.net/config/ci-ext/policies/rosa-agent-policy.yml),
and its generated `secret_id` is published to `app-sre/approles/rosa-agent-approle`.
A [creds policy](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/vault.devshift.net/config/ci-ext/policies/rosa-agent-approle-creds-policy.yml)
plus [OIDC permission](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/dependencies/vault/permissions/oidc/ci-ext/rosa-agent.yml)
let the `team-rosa-act-members` team read that `secret_id` to seed the
`osd-sre-vault-app-role-secret` Kubernetes Secret above.
