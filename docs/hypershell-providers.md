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

Use `mktemp` rather than a fixed path. A predictable name in a world-writable
directory invites squatting and symlink games on a shared host, and costs
nothing to avoid:

```sh
secret_file="$(mktemp)"
chmod 600 "$secret_file"
jq --raw-output --join-output '.credential.client_secret' \
  <service-account-file>.json > "$secret_file"
```

Then mint. Note the `env -u`: openshellctl resolves auth from Vault whenever
`OPENSHELL_VAULT_KV_MOUNT` and `OPENSHELL_VAULT_KV_PATH` are set, and those
values describe **one** environment. Left set, they override what you pass
here, and a Vault outage or expired Vault token fails the command outright
even though every value it needs was supplied by flag.

```sh
env -u OPENSHELL_VAULT_KV_MOUNT -u OPENSHELL_VAULT_KV_PATH \
    -u OPENSHELL_VAULT_FIELD_PREFIX \
  openshellctl -g <registration> token refresh --write --no-browser \
    --client-secret-file "$secret_file" \
    --oidc-issuer    "$(jq -r '.credential.issuer'    <service-account-file>.json)" \
    --oidc-client-id "$(jq -r '.credential.client_id' <service-account-file>.json)" \
    --oidc-audience  "$(jq -r '.credential.audience'  <service-account-file>.json)"
```

```sh
rm --force "$secret_file"
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

### Updating a profile that is already imported

`profile import` is for the first write. Changing a profile afterwards needs
`profile update`, which requires a non-zero `resource_version` — a value the
server assigns and the repo file deliberately does not carry, because it would
go stale on every update and make the file wrong the moment it was committed.

So read the current version off the gateway and splice it into a throwaway copy
rather than editing the repo file:

```sh
openshell -g <registration> profile export <profile-id> --global \
  | grep '^resource_version:'
```

Add that one line to a temp copy of the repo file, then:

```sh
openshell -g <registration> profile update <profile-id> --file <temp-file> --global
```

**Editing the repo file does not change the gateway.** Lint and review act on
the file; the gateway keeps serving what was last imported. A validation run
after editing but before updating tests the *old* profile and will look like
the change did not work.

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

**Verify the capability you need, not just the identity.** A wrong credential
fails only at request time, inside a sandbox, with a generic authentication
error — a long way from where you set it.

Confirming the identity is the easy half:

```sh
SOME_TOKEN="$(vault kv get -field=<field> <rosa-agent-secret-path>)" \
  gh api user --jq '.login'
```

It should return the agent's bot account, not a person. But passing this
proves very little. A secret can hold several credentials for one service
that differ only in *permission*: one that authenticates and reads, another
that can also write. The read-only one passes every identity check, and the
service's own permissions API may still report write access — because that
describes what the **account** may do, not what the **token** is scoped to.

So exercise the operation the agent actually performs. For a credential that
has to push, a dry-run push reaches the remote and returns its verdict
without changing anything:

```sh
git push --dry-run <remote> HEAD:refs/heads/probe
```

This is not hypothetical. A provider was bound to a credential that passed
the identity check, reported write access, and could not push; nothing
surfaced it until a scheduled job failed.

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

## Rotating a credential under a running sandbox

Rebinding a provider does **not** change already-running processes. The
credential is installed into the sandbox's *launch* environment, so only
processes started afterwards pick it up. `provider update --wait` says as much:
it waits until the sandbox applies credentials, policy and environment "for new
processes".

This is worth understanding before you hit it, because every signal says
healthy while a workload keeps failing:

```sh
openshell -g <registration> sandbox provider status <sandbox> <provider> --output json
```

reports `credentials_installed: true`, `policy_active: true` and `observed`
matching `desired`. All of that is accurate — the *sandbox* has the new
credential. A process that started before the rotation still does not.

### Telling which credential a process holds

You cannot read the credential, and neither can the workload. What
`provider update` installs in the environment is an opaque handle, not the
secret; the supervisor proxy exchanges it for the real value per request. So
nothing inside the sandbox reveals which version it is holding, and any
conclusion a workload draws about "its token" is inference from a status code.

The handle tracks the credential **version**, not the sandbox, so its digest is
comparable across sandboxes on the same gateway. Compare digests rather than
values:

```sh
printf %s "$<CREDENTIAL_KEY>" | sha256sum | cut -c1-16
```

Run it in a fresh `sandbox exec` to get the current version, then in the
affected session. Matching digests mean the session is current and the fault
lies elsewhere. Differing digests mean the session is stale.

### Fixing it

Start a **new session** and launch the workload there:

```sh
openshell -g <registration> sandbox connect <sandbox>
```

Restarting the process inside its existing shell does not work — a child
inherits its parent's environment, so it picks up the same stale handle and
reproduces the identical failure. If a new session still shows the old digest,
the entrypoint is replaying a captured value; `sandbox stop` followed by
`sandbox start` preserves the workspace and relaunches everything against the
current environment. Deleting and recreating the sandbox is the last resort.

### Do not bake the handle into config

A push that fails before `gh auth setup-git` has run invites the workaround of
writing the credential into a git remote URL. Avoid it. The value embedded is
only the handle, so this is not a secret-disclosure problem — but the handle is
version-pinned, and writing it into `.git/config` freezes one credential
version into repo state.

At the next rotation that remote keeps presenting a stale handle, pushes start
failing with upstream 403s, and the usual fix no longer helps: a new session
cannot clear it, because the stale value is in the repository rather than the
environment. Use the credential helper instead, which resolves the handle from
the live environment on every request and therefore follows rotations:

```sh
gh auth setup-git
```

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

Both layers return 403, so tell them apart by shape rather than by code. A
proxy denial is structured JSON naming the policy that refused it:

```json
{"error":"policy_denied","policy":"_provider_<name>","rule":"DELETE /…",
 "layer":"l7","protocol":"rest","method":"DELETE","host":"…",
 "binary":"/usr/local/bin/gh","rule_missing":{…}}
```

`rule_missing` spells out the rule that would have allowed it, which is the
fastest route to a fix. An upstream denial looks nothing like this — for git
operations it arrives prefixed with `remote:`, and for REST it is the
service's own error body.

Note this means **cleanup cannot run inside the sandbox** if the policy does
not allow the cleanup verb. Anything a validation run creates has to be
removed from the host afterwards.

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

### The opposite failure: a rule that should deny, and does not

Every endpoint block must set `enforcement: enforce` explicitly. The parser
matches that one exact string and falls back to `audit` for everything else:

```rust
Some("enforce") => EnforcementMode::Enforce,
_               => EnforcementMode::Audit,
```

Audit **allows the traffic** and merely logs the violation. So the default is
fail-open, and it is reached by omitting the field, leaving it empty, or
miscasing it as `Enforce`. None of those are errors at lint or import time.

The symptom is a request you believe is denied succeeding anyway. A policy
reviewed only by reading the YAML will look correct. Treat any block without a
literal `enforcement: enforce` as permitting whatever it describes, and prove
denials by exercising them rather than by inspection.

Relatedly, `access: read-write` does not mean every verb. For REST it expands
to exactly `GET`, `HEAD`, `OPTIONS`, `POST`, `PUT`, `PATCH` — **`DELETE` is
not included**, and needs either its own rule or `access: full`. A delete
failing while reads and writes succeed is this, not a credential problem.

The shorthands expand differently per protocol, so do not carry the REST
intuition across. For `graphql`, `read-write` is `query` plus `mutation`; for
`websocket` it is `GET` plus `WEBSOCKET_TEXT`. Only `full` is `*` everywhere.

## Known rough edges

- `openshellctl` has reported `CREDENTIAL_KEYS 0` for providers that
  demonstrably hold credentials, where `openshell` reports them correctly
  against the same gateway. Prefer `openshell` for that check.
- `profile lint` requires a live authenticated gateway despite its help saying
  it validates "without registering them".
- The gateway's own profile categories are not the same vocabulary as
  HyperShell `ProviderSpec` categories. Do not map one onto the other.
