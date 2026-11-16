# SOP: OpenShell Provider Credential Rotation

## Purpose

Rotate the credential for any credentialed OpenShell provider on the ROSA-Agent
gateways. This procedure is provider-agnostic and covers all three gateway
environments (prod, staging, integration).

## Prerequisites

- `vault` CLI authenticated to the Vault instance
- `openshellctl` installed
- At least one gateway registered locally with Vault coordinates
  (`ls ~/.config/openshell/gateways/`)
- Knowledge of:
  - The provider name (`openshellctl provider list` on the target gateway)
  - The Vault field that holds the provider's credential
  - The environment variable name the provider profile expects (declared in
    `credentials[*].env_vars` in the profile YAML under `provider-profiles/`)

## Reference: gateway environments

| Environment | Vault field prefix | Gateway registration |
|---|---|---|
| prod | `hypershell` | `rosa-agent` |
| staging | `hypershell-staging` | `rosa-agent-bot-staging` |
| integration | `hypershell-integration` | `rosa-agent-bot-integration` |

All gateway credentials are stored in a single Vault KV v2 secret:

- **KV engine (mount):** `osd-sre`
- **Secret path:** `rosa-agent`

Provider credentials are stored as individual fields within this same secret.
Consult the header comments in the relevant `provider-profiles/*.yaml` file to
identify which Vault field a provider uses.

## Procedure

Throughout this procedure:

- `<CREDENTIAL_FIELD>` — the Vault field name holding the credential
- `<CREDENTIAL_ENV>` — the environment variable name the provider expects; must
  match the `env_vars` declared in the provider profile
- `<PROVIDER_NAME>` — the OpenShell provider name
- `<PREFIX>` — the Vault field prefix for the target environment (`hypershell`,
  `hypershell-staging`, or `hypershell-integration`)

### Step 1 — Update the credential in Vault

Replace the credential value in the Vault KV v2 secret. This patches only the
specified field; all other fields in the secret are preserved.

```sh
vault kv patch -mount=osd-sre rosa-agent \
  <CREDENTIAL_FIELD>="<new-credential-value>"
```

Verify the new value was written:

```sh
vault kv get -mount=osd-sre -field=<CREDENTIAL_FIELD> rosa-agent
```

### Step 2 — Authenticate to Vault

If your Vault token has expired, re-authenticate:

```sh
vault login -method=oidc -no-print -non-interactive
```

This completes automatically if your SSO session is active. The Vault token has
a 30-minute TTL; re-run this command if it expires during the procedure.

### Step 3 — Update the provider on each gateway

Repeat for each `<PREFIX>`: `hypershell`, `hypershell-staging`,
`hypershell-integration`.

**Preferred: in-place update (non-disruptive to running sandboxes)**

```sh
<CREDENTIAL_ENV>="$(vault kv get -mount=osd-sre -field=<CREDENTIAL_FIELD> rosa-agent)" \
  OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX> \
  openshellctl provider update <PROVIDER_NAME> --credential <CREDENTIAL_ENV>
```

**Fallback: delete and recreate**

Use this only if `provider update` does not propagate the credential correctly,
or if a full provider reset is required.

```sh
OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX> \
  openshellctl provider delete <PROVIDER_NAME>

<CREDENTIAL_ENV>="$(vault kv get -mount=osd-sre -field=<CREDENTIAL_FIELD> rosa-agent)" \
  OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX> \
  openshellctl provider create --name <PROVIDER_NAME> \
    --type <PROVIDER_TYPE> --global-profile --credential <CREDENTIAL_ENV>
```

`provider delete` will fail if a sandbox is attached to the provider. Use
`provider update` instead, or delete the sandbox first.

### Step 4 — Validate in a sandbox

For each environment, create a test sandbox with the updated provider and
confirm the credential works.

```sh
OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX> openshellctl sandbox create \
  --name cred-test \
  --from quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest \
  --no-auto-providers \
  --provider <PROVIDER_NAME> \
  --env <any required non-secret env vars for the provider> \
  --detach -- sleep 600
```

Run a provider-specific validation command inside the sandbox. The validation
command depends on the provider — consult the header comments in the provider
profile YAML for examples.

```sh
OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX> openshellctl sandbox exec cred-test -- \
  <validation command>
```

Clean up after validation:

```sh
OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX> openshellctl sandbox delete cred-test
```

### Step 5 — Confirm production cron jobs (if applicable)

If the rotated credential is used by scheduled cron jobs, verify the next
scheduled run completes successfully:

```sh
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox list
```

## Worked example

Rotating a credential for a provider named `my-provider` whose credential is
stored in Vault field `my-credential-token` and injected via environment
variable `MY_TOKEN`:

```sh
# 1. Authenticate to Vault
vault login -method=oidc -no-print -non-interactive

# 2. Write the new credential to Vault
vault kv patch -mount=osd-sre rosa-agent \
  my-credential-token="<new-value>"

# 3. Update the provider on all three gateways
for PREFIX in hypershell hypershell-staging hypershell-integration; do
  MY_TOKEN="$(vault kv get -mount=osd-sre -field=my-credential-token rosa-agent)" \
    OPENSHELL_VAULT_FIELD_PREFIX=$PREFIX \
    openshellctl provider update my-provider --credential MY_TOKEN
done

# 4. Validate on prod
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox create \
  --name cred-test \
  --from quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest \
  --no-auto-providers \
  --provider my-provider \
  --detach -- sleep 600

OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox exec cred-test -- \
  <validation command>

OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox delete cred-test
```

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| Provider-specific auth failure (e.g., 401) | Wrong non-secret env vars (email, base URL) at sandbox creation | Check the provider profile header for required `--env` values |
| "token expired" on openshellctl | Gateway token (5-min TTL) expired | Do not use `--no-vault`; use `OPENSHELL_VAULT_FIELD_PREFIX=<PREFIX>` so openshellctl auto-renews |
| 403 from Vault API | Vault token (30-min TTL) expired | Re-run `vault login -method=oidc -no-print -non-interactive` |
| "provider is attached to sandbox(es)" | Cannot delete provider with active sandboxes | Use `provider update --credential` instead |
| "token exchange failed (HTTP 401)" | `OPENSHELL_OIDC_CLIENT_SECRET` set without matching `OPENSHELL_OIDC_CLIENT_ID` | Do not set `OPENSHELL_OIDC_CLIENT_SECRET` manually; use the `OPENSHELL_VAULT_FIELD_PREFIX` pattern |

## References

- Vault secret: `osd-sre/rosa-agent`
- Provider profiles: `provider-profiles/*.yaml` (header comments document the
  Vault field, credential env var, and validation commands for each provider)
- Gateway quickstart: `guides/vault-gateway-quickstart.md`
