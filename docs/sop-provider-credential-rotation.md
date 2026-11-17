# How To: Rotate OpenShell Provider Credentials

Rotate the credential for any credentialed OpenShell provider on the ROSA-Agent
gateways. This procedure is provider-agnostic and covers all three gateway
environments (prod, staging, integration). The procedure is non-disruptive to
running sandboxes when the preferred `provider update` path is used.
Approximate time: 10-15 minutes per environment.

## Ordering / Rationale

Vault authentication must happen first because every subsequent command reads
from Vault. The credential is written to Vault before updating providers so
the `provider update` command can pull the new value directly via command
substitution. Each gateway environment is updated independently; there is no
cross-environment dependency.

> [!CAUTION]
> The `provider delete` fallback is disruptive -- it fails if any sandbox is
> attached to the provider. Use `provider update` unless a full provider reset
> is required.

## Reference: Gateway Environments

| Environment | Vault field prefix | Gateway registration |
| --- | --- | --- |
| prod | `hypershell` | `rosa-agent` |
| staging | `hypershell-staging` | `rosa-agent-bot-staging` |
| integration | `hypershell-integration` | `rosa-agent-bot-integration` |

All gateway credentials are stored in a single Vault KV v2 secret:

- **KV engine (mount):** `osd-sre`
- **Secret path:** `rosa-agent`

Provider credentials are stored as individual fields within this same secret.
Consult the header comments in the relevant `provider-profiles/*.yaml` file to
identify which Vault field a provider uses.

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

Set the variables for this procedure:

```bash
CREDENTIAL_FIELD="<vault-field-name-for-provider-credential>"
CREDENTIAL_ENV="<env-var-name-from-provider-profile>"
PROVIDER_NAME="<openshell-provider-name>"
```

## Step 1 -- Authenticate to Vault

Ensure your Vault token is current. The token has a 30-minute TTL; re-run this
command if it expires during the procedure.

```bash
vault login -method=oidc -no-print -non-interactive
```

> [!NOTE]
> This completes automatically if your SSO session is active.

## Step 2 -- Update the Credential in Vault

Replace the credential value in the Vault KV v2 secret. This patches only the
specified field; all other fields in the secret are preserved.

```bash
vault kv patch -mount=osd-sre rosa-agent \
  "${CREDENTIAL_FIELD}=<new-credential-value>"
```

Verify the new value was written:

```bash
vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent
```

Expected output:

```text
<the new credential value>
```

## Step 3 -- Update the Provider on Each Gateway

Repeat for each environment prefix: `hypershell`, `hypershell-staging`,
`hypershell-integration`.

```bash
PREFIX="<environment-prefix>"
```

### Preferred: in-place update (non-disruptive to running sandboxes)

```bash
${CREDENTIAL_ENV}="$(vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent)" \
  OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl provider update "${PROVIDER_NAME}" --credential "${CREDENTIAL_ENV}"
```

### Fallback: delete and recreate

> [!WARNING]
> `provider delete` will fail if a sandbox is attached to the provider. Use
> `provider update` instead, or delete the sandbox first.

Use this only if `provider update` does not propagate the credential correctly,
or if a full provider reset is required.

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl provider delete "${PROVIDER_NAME}"
```

```bash
PROVIDER_TYPE="<provider-type>"
```

```bash
${CREDENTIAL_ENV}="$(vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent)" \
  OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl provider create --name "${PROVIDER_NAME}" \
    --type "${PROVIDER_TYPE}" --global-profile --credential "${CREDENTIAL_ENV}"
```

## Step 4 -- Validate in a Sandbox

For each environment, create a test sandbox with the updated provider and
confirm the credential works.

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" openshellctl sandbox create \
  --name cred-test \
  --from quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest \
  --no-auto-providers \
  --provider "${PROVIDER_NAME}" \
  --detach -- sleep 600
```

Run a provider-specific validation command inside the sandbox. The validation
command depends on the provider -- consult the header comments in the provider
profile YAML for examples.

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" openshellctl sandbox exec cred-test -- \
  <validation-command>
```

> [!NOTE]
> Replace `<validation-command>` with the provider-specific check documented in
> the provider profile header comments.

Verify the validation output shows successful authentication before proceeding.

Clean up after validation:

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" openshellctl sandbox delete cred-test
```

## Step 5 -- Confirm Production Cron Jobs (If Applicable)

If the rotated credential is used by scheduled cron jobs, verify the next
scheduled run completes successfully:

```bash
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox list
```

Expected output should show the cron job sandbox in a healthy state with no
authentication errors.

## Rollback

If the new credential causes failures, restore the previous credential value
in Vault and re-run the provider update.

```bash
vault kv patch -mount=osd-sre rosa-agent \
  "${CREDENTIAL_FIELD}=<previous-credential-value>"
```

Then repeat Step 3 (provider update) for each affected gateway environment to
propagate the restored credential.

> [!IMPORTANT]
> If you used the `provider delete` fallback and the original credential is
> lost, you will need to generate a new credential from the upstream provider
> and treat it as a fresh rotation.

## Worked Example

Rotating a credential for a provider named `my-provider` whose credential is
stored in Vault field `my-credential-token` and injected via environment
variable `MY_TOKEN`:

```bash
# Set variables
CREDENTIAL_FIELD="my-credential-token"
CREDENTIAL_ENV="MY_TOKEN"
PROVIDER_NAME="my-provider"
```

```bash
# 1. Authenticate to Vault
vault login -method=oidc -no-print -non-interactive
```

```bash
# 2. Write the new credential to Vault
vault kv patch -mount=osd-sre rosa-agent \
  "${CREDENTIAL_FIELD}=<new-value>"
```

```bash
# 3. Update the provider on all three gateways
for PREFIX in hypershell hypershell-staging hypershell-integration; do
  ${CREDENTIAL_ENV}="$(vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent)" \
    OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
    openshellctl provider update "${PROVIDER_NAME}" --credential "${CREDENTIAL_ENV}"
done
```

```bash
# 4. Validate on prod
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox create \
  --name cred-test \
  --from quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest \
  --no-auto-providers \
  --provider "${PROVIDER_NAME}" \
  --detach -- sleep 600
```

```bash
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox exec cred-test -- \
  <validation-command>
```

```bash
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl sandbox delete cred-test
```

## Troubleshooting

| Symptom | Cause | Resolution |
| --- | --- | --- |
| Provider-specific auth failure (e.g., 401) | Wrong non-secret env vars (email, base URL) at sandbox creation | Check the provider profile header for required `--env` values |
| "token expired" on openshellctl | Gateway token (5-min TTL) expired | Do not use `--no-vault`; use `OPENSHELL_VAULT_FIELD_PREFIX` so openshellctl auto-renews |
| 403 from Vault API | Vault token (30-min TTL) expired | Re-run `vault login -method=oidc -no-print -non-interactive` |
| "provider is attached to sandbox(es)" | Cannot delete provider with active sandboxes | Use `provider update --credential` instead |
| "token exchange failed (HTTP 401)" | `OPENSHELL_OIDC_CLIENT_SECRET` set without matching `OPENSHELL_OIDC_CLIENT_ID` | Do not set `OPENSHELL_OIDC_CLIENT_SECRET` manually; use the `OPENSHELL_VAULT_FIELD_PREFIX` pattern |

## Links

- Vault secret: `osd-sre/rosa-agent`
- Provider profiles: `provider-profiles/*.yaml` (header comments document the
  Vault field, credential env var, and validation commands for each provider)
- Gateway quickstart: [Vault Gateway Quickstart](guides/vault-gateway-quickstart.md)

## Agent Metadata

### Referenced Components

| Component | Type | Validation |
| --- | --- | --- |
| vault | tool | [standard HashiCorp Vault CLI](https://github.com/hashicorp/vault) |
| openshellctl | tool | internal ROSA-Agent gateway CLI |

### Agent Knowledge and Troubleshooting

- The Vault KV v2 `patch` subcommand updates only the specified fields; it does not overwrite the entire secret. This is critical because all provider credentials share a single secret path (`osd-sre/rosa-agent`).
- The `provider update --credential` path is non-disruptive to running sandboxes, while `provider delete` will fail if any sandbox is attached.
- Gateway tokens have a 5-minute TTL (separate from the 30-minute Vault token TTL). Using `OPENSHELL_VAULT_FIELD_PREFIX` enables automatic token renewal.
