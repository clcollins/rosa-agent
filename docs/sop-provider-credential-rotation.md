# How To: Rotate OpenShell Provider Credentials

Rotate the credential for any credentialed OpenShell provider on the ROSA-Agent
gateways. This procedure is provider-agnostic and covers all three gateway
environments (prod, staging, integration). The procedure is non-disruptive to
running sandboxes when the preferred `provider update` path is used.
Approximate time: 10-15 minutes per environment.

This is the manual, generic procedure: use it to add or rotate a static
credential on a provider that is not (yet) managed by `make providers`, or to
work out the steps before adding a provider to the descriptor.

> [!IMPORTANT]
> **Managed providers.** If the provider is listed in
> `provider-profiles/providers.yaml`, do not follow Step 3 by hand. Patch the
> Vault field named in that file (Step 2), then run, per gateway:
>
> ```bash
> OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
>   make provider PROVIDER="${PROVIDER_NAME}" OPENSHELL_GATEWAY_NAME="${GATEWAY}"
> ```
>
> Patch the field the descriptor names; writing the new value to a different
> field is silently reverted by the next `make providers`. To manage a new
> provider permanently, add it to `providers.yaml` instead of repeating this
> SOP. See `docs/hypershell-providers.md`, "Data-driven provisioning".
>
> **`rosa-agent-openshell` specifically:** this provider's credential env var
> is `OPENSHELL_OIDC_CLIENT_SECRET`, which `openshellctl` consumes for its own
> gateway login. Setting it in `openshellctl`'s environment causes a 401 on
> the gateway itself. After #176, use
> `make provider PROVIDER=rosa-agent-openshell`, which sets `OPENSHELL_TOKEN`
> instead.

Not all providers can be rotated with this procedure:

> [!WARNING]
> **Gateway-refreshed providers are out of scope.** If the provider profile has
> a `refresh:` block (e.g. PagerDuty OAuth, Vertex service-account JWT), the
> gateway mints the live token itself and `provider update --credential` is
> overwritten on the next refresh. Rotating those means replacing the refresh
> material; see `docs/hypershell-providers.md`, "Refresh material is set once".

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

Both columns are needed on every `openshellctl` call. The gateway is selected
by `-g <registration>`; `OPENSHELL_VAULT_FIELD_PREFIX` only selects which Vault
fields `openshellctl` reads its own gateway login from. Setting the prefix
without `-g` targets whichever gateway is active.

All gateway credentials are stored in a single Vault KV v2 secret:

- **KV engine (mount):** `osd-sre`
- **Secret path:** `rosa-agent`

Provider credentials are stored as individual fields within this same secret.
Consult the header comments in the relevant `provider-profiles/*.yaml` file to
identify which Vault field a provider uses.

## Prerequisites

- `vault` CLI authenticated to the Vault instance
- `openshellctl` installed (and `make` + `python3` with PyYAML for managed
  providers)
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

For a managed provider, take `CREDENTIAL_FIELD` (`vault_field`) and
`CREDENTIAL_ENV` (`key`) from its entry in `provider-profiles/providers.yaml`.

## Step 1 -- Authenticate to Vault

Ensure your Vault token is current. The token has a 30-minute TTL; re-run this
command if it expires during the procedure.

```bash
vault login -method=oidc -no-print -non-interactive
```

> [!NOTE]
> This completes automatically if your SSO session is active.

## Step 2 -- Update the Credential in Vault

Record the current secret version for rollback:

```bash
PREV_VERSION="$(vault kv metadata get -mount=osd-sre -format=json rosa-agent \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["current_version"])')"
echo "${PREV_VERSION}"
```

Replace the credential value in the Vault KV v2 secret. This patches only the
specified field; all other fields in the secret are preserved. The value is
read with `read -s` and passed on stdin (`=-`) so it never lands in shell
history or the process list:

```bash
read -rs NEW_CREDENTIAL   # paste the new value, then Enter
printf '%s' "${NEW_CREDENTIAL}" | vault kv patch -mount=osd-sre rosa-agent \
  "${CREDENTIAL_FIELD}=-"
unset NEW_CREDENTIAL
```

Verify the new version was written without printing the secret:

```bash
vault kv metadata get -mount=osd-sre -format=json rosa-agent \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["current_version"])'
```

Expected output: `PREV_VERSION + 1`.

## Step 3 -- Update the Provider on Each Gateway

Managed providers: run the `make provider` command from the note at the top
instead of this step.

> [!WARNING]
> **Never pass `OPENSHELL_OIDC_CLIENT_SECRET` in `openshellctl`'s
> environment.** `openshellctl` reads that variable for its own gateway login,
> so setting it as the credential env var causes a 401 on the gateway itself.
> The `rosa-agent-openshell` provider must be rotated via `make provider`,
> which passes the credential as `OPENSHELL_TOKEN` instead.

Repeat for each environment, setting both values from the reference table:

```bash
PREFIX="<environment-prefix>"
GATEWAY="<gateway-registration>"
```

### Preferred: in-place update (non-disruptive to running sandboxes)

`env` is required: a `${VAR}=value command` prefix is not an assignment in
bash, it fails with `command not found`.

```bash
env "${CREDENTIAL_ENV}=$(vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent)" \
  OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl -g "${GATEWAY}" provider update "${PROVIDER_NAME}" --credential "${CREDENTIAL_ENV}"
```

### Fallback: delete and recreate

> [!WARNING]
> `provider delete` will fail if a sandbox is attached to the provider. Use
> `provider update` instead, or delete the sandbox first.

Use this only if `provider update` does not propagate the credential correctly,
or if a full provider reset is required.

Before deleting, note any `--config` values the provider carries; recreate
does not restore them:

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl -g "${GATEWAY}" provider get "${PROVIDER_NAME}" -o json
```

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl -g "${GATEWAY}" provider delete "${PROVIDER_NAME}"
```

```bash
PROVIDER_TYPE="<provider-type>"
```

```bash
env "${CREDENTIAL_ENV}=$(vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent)" \
  OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
  openshellctl -g "${GATEWAY}" provider create --name "${PROVIDER_NAME}" \
    --type "${PROVIDER_TYPE}" --global-profile --credential "${CREDENTIAL_ENV}"
```

Add `--config KEY=VALUE` for each config value recorded above. For a managed
provider, `make provider PROVIDER="${PROVIDER_NAME}"` recreates it with its
config from the descriptor.

## Step 4 -- Validate in a Sandbox

For each environment, create a test sandbox with the updated provider and
confirm the credential works.

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" openshellctl -g "${GATEWAY}" sandbox create \
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
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" openshellctl -g "${GATEWAY}" sandbox exec cred-test -- \
  <validation-command>
```

> [!NOTE]
> Replace `<validation-command>` with the provider-specific check documented in
> the provider profile header comments.

Verify the validation output shows successful authentication before proceeding.

Clean up after validation:

```bash
OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" openshellctl -g "${GATEWAY}" sandbox delete cred-test
```

## Step 5 -- Confirm Production Cron Jobs (If Applicable)

If the rotated credential is used by scheduled cron jobs, verify the next
scheduled run completes successfully:

```bash
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl -g rosa-agent sandbox list
```

Expected output should show the cron job sandbox in a healthy state with no
authentication errors.

## Rollback

If the new credential causes failures, restore the previous value of this one
field from the version recorded in Step 2 and re-run the provider update.

```bash
printf '%s' "$(vault kv get -mount=osd-sre -version="${PREV_VERSION}" \
    -field="${CREDENTIAL_FIELD}" rosa-agent)" \
  | vault kv patch -mount=osd-sre rosa-agent "${CREDENTIAL_FIELD}=-"
```

> [!CAUTION]
> Do not use `vault kv rollback` here: it restores the whole secret, which
> reverts every provider's credential changed since `PREV_VERSION`.

Then repeat Step 3 (or `make provider` for a managed provider) for each
affected gateway environment to propagate the restored credential.

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
# 2. Write the new credential to Vault (value read silently, passed on stdin)
read -rs NEW_CREDENTIAL
printf '%s' "${NEW_CREDENTIAL}" | vault kv patch -mount=osd-sre rosa-agent \
  "${CREDENTIAL_FIELD}=-"
unset NEW_CREDENTIAL
```

```bash
# 3. Update the provider on all three gateways (prefix:registration pairs)
for PAIR in hypershell:rosa-agent \
            hypershell-staging:rosa-agent-bot-staging \
            hypershell-integration:rosa-agent-bot-integration; do
  PREFIX="${PAIR%%:*}"; GATEWAY="${PAIR#*:}"
  env "${CREDENTIAL_ENV}=$(vault kv get -mount=osd-sre -field="${CREDENTIAL_FIELD}" rosa-agent)" \
    OPENSHELL_VAULT_FIELD_PREFIX="${PREFIX}" \
    openshellctl -g "${GATEWAY}" provider update "${PROVIDER_NAME}" --credential "${CREDENTIAL_ENV}"
done
```

```bash
# 4. Validate on prod
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl -g rosa-agent sandbox create \
  --name cred-test \
  --from quay.io/redhat-services-prod/rosa-tenant/rosa-agent/rosa-agent:latest \
  --no-auto-providers \
  --provider "${PROVIDER_NAME}" \
  --detach -- sleep 600
```

```bash
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl -g rosa-agent sandbox exec cred-test -- \
  <validation-command>
```

```bash
OPENSHELL_VAULT_FIELD_PREFIX=hypershell openshellctl -g rosa-agent sandbox delete cred-test
```

## Troubleshooting

| Symptom | Cause | Resolution |
| --- | --- | --- |
| Provider-specific auth failure (e.g., 401) | Wrong non-secret env vars (email, base URL) at sandbox creation | Check the provider profile header for required `--env` values |
| "token expired" on openshellctl | Gateway token (5-min TTL) expired | Do not use `--no-vault`; use `OPENSHELL_VAULT_FIELD_PREFIX` so openshellctl auto-renews |
| 403 from Vault API | Vault token (30-min TTL) expired | Re-run `vault login -method=oidc -no-print -non-interactive` |
| "provider is attached to sandbox(es)" | Cannot delete provider with active sandboxes | Use `provider update --credential` instead |
| `MY_TOKEN=...: command not found` | `${VAR}=value cmd` is not an assignment in bash | Prefix the command with `env "${CREDENTIAL_ENV}=..."` |
| Credential reverts after a later `make providers` | Provider is managed; new value was written to a field other than the descriptor's `vault_field` | Patch the field named in `provider-profiles/providers.yaml` |
| New credential works briefly, then old/placeholder token returns | Provider has a gateway `refresh:` strategy | Out of scope here; replace refresh material per `docs/hypershell-providers.md` |
| "token exchange failed (HTTP 401)" | `OPENSHELL_OIDC_CLIENT_SECRET` in the command environment overrides `openshellctl`'s own gateway login | Use `make provider PROVIDER=rosa-agent-openshell`, which passes the credential as `OPENSHELL_TOKEN`; never pass `OPENSHELL_OIDC_CLIENT_SECRET` via Step 3 |

## Links

- Vault secret: `osd-sre/rosa-agent`
- Provider profiles: `provider-profiles/*.yaml` (header comments document the
  Vault field, credential env var, and validation commands for each provider)
- Gateways and service accounts: [hypershell-gateways.md](hypershell-gateways.md)
- Managed providers and refresh material: [hypershell-providers.md](hypershell-providers.md)
  and `provider-profiles/providers.yaml` (`make provider PROVIDER=<name>`)

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
- `OPENSHELL_VAULT_FIELD_PREFIX` selects the Vault fields openshellctl authenticates with; it does not select the gateway. Always pass `-g <registration>`.
- Providers listed in `provider-profiles/providers.yaml` are provisioned by `make provider PROVIDER=<name>`; the descriptor's `vault_field` is the source of truth for their credential.
- Providers whose profile has a `refresh:` block are gateway-refreshed; this SOP does not rotate them.
- `rosa-agent-openshell` uses `OPENSHELL_OIDC_CLIENT_SECRET`, which collides with `openshellctl`'s own login. It must be rotated via `make provider`, never via Step 3.
