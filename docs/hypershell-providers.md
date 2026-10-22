# Provisioning provider profiles and providers on a gateway

How to get the credential providers onto a rosa-agent gateway, by hand.

This is the `openshell` side of the platform.
[`hypershell-gateways.md`](./hypershell-gateways.md) covers the other half —
creating the gateway itself, access grants and service accounts, via `hsctl`.
Do that first: this runbook assumes a gateway exists and a service account for
it has been minted.

Nothing here is reconciled. OpenShell providers are not a HyperShell Kind, so
they cannot live in `.hypershell/`; the profile YAML in `provider-profiles/` is
the source of truth and these commands put it on a gateway.

## Three CLIs, and which one to reach for

The single biggest source of confusion. They are not interchangeable.

| CLI | Talks to | How it authenticates |
| --- | --- | --- |
| `hsctl` | the HyperShell management API | browser OIDC, per instance |
| `openshell` | the gateway (Rust) | the registration under `~/.config/openshell/gateways/` |
| `openshellctl` | the gateway (Go) | Vault, unless explicitly overridden |

Gateways, access and service accounts are `hsctl`. Profiles, providers and
sandboxes are `openshell`. `openshellctl` is a second gateway client with its
own auth behaviour — see below before using it.

## Authenticating as a service account

Only needed for gateways you cannot log into interactively. For a gateway where
your own account has access, `openshell gateway login <registration>` opens a
browser and you can skip this section.

Three facts make this less obvious than it looks.

**A service account cannot use browser login.** Service-account OIDC clients
use the `client_credentials` grant and have no registered redirect URIs, so
`openshell gateway login` fails with `invalid_parameter: redirect_uri`. This is
by construction, not a misconfiguration.

**`openshellctl` resolves auth from Vault by default.** It reads the rosa-agent
secret in Vault using a field prefix, which means it authenticates as whichever
environment that secret describes — in practice, production. Passing `-g` does
*not* change this. It will cheerfully mint a production token and report the
gateway you asked for:

```sh
openshellctl -g <registration> token show
```

Check `Subject`, `Issuer` and `Audience` in that output against the environment
you intended. A mismatch here is the failure mode; it is silent otherwise.

**Service-account tokens are short-lived.** Expect a few minutes. Any sequence
of commands will need re-minting partway through; a `401` or "token expired"
mid-run usually means exactly that and not a credential problem.

### Minting a token

The service-account credential file produced when the account was created is
self-describing. Inspect its field names without printing any value:

```sh
jq '.credential | keys' <service-account-file>.json
```

It carries the client ID, client secret, issuer, audience and token endpoint —
everything needed. Nothing has to be looked up in `hsctl`.

`--client-secret-file` takes a path, so write the secret to a mode-0600 file
rather than passing it on a command line where it would reach your shell
history and the process table:

```sh
umask 077
jq --raw-output --join-output '.credential.client_secret' \
  <service-account-file>.json > /tmp/sa-secret
```

Then mint, overriding every value Vault would otherwise supply:

```sh
openshellctl -g <registration> token refresh --write --no-browser \
  --client-secret-file /tmp/sa-secret \
  --oidc-issuer    "$(jq -r '.credential.issuer'    <service-account-file>.json)" \
  --oidc-client-id "$(jq -r '.credential.client_id' <service-account-file>.json)" \
  --oidc-audience  "$(jq -r '.credential.audience'  <service-account-file>.json)"
```

```sh
rm /tmp/sa-secret
```

`--write` stores the token in the schema `openshell` reads, so you authenticate
with `openshellctl` and then operate with `openshell`. Confirm it worked, and
confirm *which* identity you got:

```sh
openshellctl -g <registration> token show
```

The `Gateway view (whoami)` section should list `openshell-admin` or
`openshell-user`. A warning that the token's own roles claim is empty is
expected — the gateway resolves roles itself.

### If the registration has the wrong instance baked in

A local registration stores the endpoint *and* the OIDC issuer, client ID and
audience. These can disagree — pointing at one instance's endpoint with another
instance's auth — which produces confusing failures against the right host.

```sh
jq '{gateway_endpoint, oidc_issuer, oidc_client_id, oidc_audience}' \
  ~/.config/openshell/gateways/<registration>/metadata.json
```

Every value should name the same instance. Correct them from the
service-account file if not; the file is authoritative.

## Importing profiles

OpenShell 0.1.x ships **no** built-in provider profiles, including for common
providers. Without an import there is no provider type to create against, and
`provider create` fails with "provider profile not found".

Check what a gateway already has:

```sh
openshell -g <registration> profile list
```

On a new gateway this returns nothing. **Use this as a guard** before importing
— if it lists profiles you did not expect, you are pointed at the wrong
gateway. That check is cheaper than discovering it afterwards.

Validate before writing. Note this contacts the gateway despite the help text
saying otherwise, so it needs a live token:

```sh
openshell -g <registration> profile lint --from provider-profiles
```

Then import. `--global` makes them platform-scoped, available to every
workspace on that gateway, which is what you want when no workspaces exist yet:

```sh
openshell -g <registration> profile import --global --from provider-profiles
```

`openshell provider profile ...` is a registered alias for `openshell profile
...`; both work, the short form is canonical.

## Creating providers

A profile describes a capability. A provider binds a credential to it.

Pass `--credential KEY` with a bare key and OpenShell reads the value from the
environment, so the secret never reaches a command line or your shell history.
Supply it by command substitution from Vault so it is never stored anywhere:

```sh
SOME_TOKEN="$(vault kv get -field=<field> <rosa-agent-secret-path>)" \
  openshell -g <registration> provider create --name <provider-name> \
    --type <profile-id> --global-profile --credential SOME_TOKEN
```

`--global-profile` is required whenever the profile was imported with
`--global`. Without it the CLI looks for a workspace-scoped profile and fails.

### Finding the right credential in Vault

The credentials live in the rosa-agent secret in Vault. List the field names
without printing any value:

```sh
vault kv get -format=json <rosa-agent-secret-path> | jq -r '.data.data | keys[]'
```

**Verify a credential before wiring it in.** A wrong or expired one fails only
at request time, inside a sandbox, with a generic authentication error — a long
way from where you set it. One read-only call rules that out:

```sh
SOME_TOKEN="$(vault kv get -field=<field> <rosa-agent-secret-path>)" \
  gh api user --jq '.login'
```

The identity it returns should be the agent's bot account, not a person.

**Some fields are nested objects, not strings.** A service-account keyfile is
stored as an object, so `vault kv get -field=` renders Go map syntax rather than
JSON. Reach into it with `-format=json` instead:

```sh
vault kv get -format=json <rosa-agent-secret-path> \
  | jq -r '.data.data["<field>"] | keys[]'
```

### Providers needing a refresh strategy

A provider whose credential is derived rather than static takes two further
steps: configure the refresh material, then perform the first rotation. The
profile header in `provider-profiles/` documents the exact strategy, credential
key and material for each. In outline:

```sh
openshell -g <registration> provider create --name <provider-name> \
  --type <profile-id> --global-profile \
  --credential <CREDENTIAL_KEY>=placeholder \
  --config <KEY>=<value>
```

```sh
SECRET_MATERIAL="$(...)" \
  openshell -g <registration> provider refresh configure <provider-name> \
    --credential-key <CREDENTIAL_KEY> \
    --strategy <strategy> \
    --material <key>=<value> \
    --secret-material-env <key>=SECRET_MATERIAL
```

```sh
openshell -g <registration> provider refresh rotate <provider-name> \
  --credential-key <CREDENTIAL_KEY>
```

The placeholder credential at create time is replaced by the first rotation.

## Validating

Creating a provider proves nothing about whether its credential works. Run a
read-only call per provider inside a sandbox on the real agent image:

```sh
openshell -g <registration> sandbox create \
  --name provider-check \
  --from <agent-image> \
  --provider <provider-1> --provider <provider-2> --provider <provider-3> \
  --env <NON_SECRET_KEY>=<value> \
  --no-keep --no-tty --no-auto-providers \
  -- bash -lc '<one read-only call per provider, echoing OK or FAIL>'
```

`--no-auto-providers` makes a wrong provider name an error rather than silently
creating a new empty provider. `--no-keep` deletes the sandbox afterwards.

Read the failures carefully, because the status code localises the fault:

- **401 from the upstream service** — the request reached it, so egress was
  permitted and the *credential* is wrong.
- **403 from the proxy** — the request never left, so the credential is
  untested and something denied the egress.

That distinction alone turns a vague failure into a one-step diagnosis.

On a 403, note there are **two** things that can deny it, because egress is
governed by the provider profile *and* by the sandbox policy the image carries
at `/etc/openshell/policy.yaml` (built from `policies/default.yaml`). Not
passing `--policy` does not mean no policy applied — it means the image
default did. Precedence is:

```text
/etc/openshell/policy.yaml  <  OPENSHELL_SANDBOX_POLICY  <  --policy
```

Both layers pin which binaries may reach which hosts, and both match binaries
by **exact path**, so a path that does not exist in the image denies every
call silently. That is the usual cause. Check the profile's `binaries` list
first, since it is the one being changed; then the policy block for that host.

The two layers are deliberately not redundant. The policy omits some hosts
entirely so the profile can compose narrower method- and path-level rules for
them — a coarse allow in the policy would union over the profile's restriction
and defeat it. So "add it to the policy" is not a safe default fix.

## Known rough edges

- `openshellctl` has reported `CREDENTIAL_KEYS 0` for providers that
  demonstrably hold credentials, where `openshell` reports them correctly
  against the same gateway. Prefer `openshell` for that check.
- `profile lint` requires a live authenticated gateway despite its help saying
  it validates "without registering them".
- The gateway's own profile categories are not the same vocabulary as
  HyperShell `ProviderSpec` categories. Do not map one onto the other.
