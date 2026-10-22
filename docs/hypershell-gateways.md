# Gateways, access and service accounts

The parts of the HyperShell configuration that **cannot** live in
`.hypershell/`, because the declarative import would silently drop them or
because they are not a declarative Kind at all.

Everything here is a runbook. None of it is reconciled, and none of it runs
automatically.

This covers the HyperShell management API (`hsctl`): creating gateways,
granting access, minting service accounts. Credential providers live on the
gateway itself and are a separate surface — see
[`hypershell-providers.md`](./hypershell-providers.md), which assumes the
gateway work here is done.

| File | Purpose |
| --- | --- |
| [`hypershell-access.yaml`](./hypershell-access.yaml) | The access policy: tiers, service-account roles, expiry. |
| [`hypershell-providers.md`](./hypershell-providers.md) | Putting provider profiles and providers on a gateway. |
| `hack/check_hypershell.py` | Correctness gate for `.hypershell/`. Run via `make check-hypershell`. |

## Why these steps are manual

- **Access grants** are stored as `RoleBinding`s. The import reconciles seven
  Kinds and `RoleBinding` is not one of them — it renders and is dropped
  without an error, so putting grants in `.hypershell/` would look like it
  worked and do nothing.

  `hsctl apply -k` *does* accept `kind: RoleBinding`, so grants could be
  declared in a directory outside `.hypershell/` and applied that way. Do not.
  A `RoleBinding` is addressed by `role_id`, `user_id` and `gateway_id` —
  three generated ULIDs, with no username anywhere — so the manifest would
  have to hardcode per-instance IDs that are wrong everywhere else. It also
  writes under the access facade rather than through it, losing last-owner
  protection and atomic role changes. `hsctl create gatewayAccess`, which
  takes a directory username, is both safer and portable.
- **Service accounts** are not a Kind at all. The client secret is returned
  exactly once at creation, so there is nowhere for a reconcile loop to put
  it.
- **Repository registration** is excluded from the import deliberately, to
  stop a repository re-registering itself, and requires `platform:admin`
  over REST.

Gateway ULIDs are generated per instance and are deliberately **not** recorded
in this repo: an ID is wrong on the other two environments and goes stale
after any delete and recreate. Every command below starts by looking the ID
up by name.

## Roles

The access policy — which tiers exist and what the service accounts look
like — is recorded in [`hypershell-access.yaml`](./hypershell-access.yaml).

Real usernames are deliberately not committed anywhere in this repository.
It is public, and the grant API takes internal Red Hat directory usernames,
which differ from the GitHub handles in `OWNERS`. `hypershell-access.yaml` carries
`REPLACE_WITH_DIRECTORY_USERNAME` placeholders; substitute the real values
when running the commands below. The authoritative roster is the gateway
itself:

```sh
hsctl list gatewayAccess --gateway-id "$GATEWAY_ID"
```

The gateway itself distinguishes only two tiers, Admin and User, mapping to
the Keycloak client roles `openshell-admin` and `openshell-user`. The
management plane has three: `owner`, `admin` and `user`. Both `owner` and
`admin` confer `openshell-admin` — they differ only in that an owner may
delete the gateway and grant the owner tier.

Service accounts take `openshell-user` or `openshell-admin`. The role is
fixed at creation, so changing it means revoking and recreating, which
rotates the client secret.

Note that `openshell-user` is not the safe default it looks like. A service
account does not inherit its creator's OpenShell workspace memberships, so an
`openshell-user` subject authenticates fine and then has every
workspace-scoped operation denied — including creating a sandbox — until a
gateway administrator adds that subject to each workspace by hand. There is
no `hsctl` command for that, and automatic workspace membership is out of
scope in version 1. `openshell-admin` is permitted to bypass workspace
membership, which is why `hypershell-access.yaml` specifies it.

Expiry is not optional. The server enforces a policy of 1 hour minimum,
90 days default and 365 days maximum, so omitting `--expires-in` silently
buys a 90-day clock rather than no clock. Check the live policy with
`hsctl list serviceAccounts --gateway-id "$GATEWAY_ID"`, which reports it
under `capabilities.expiration_policy`, and record the resulting
`expires_at` somewhere you will actually see it.

## Runbook

### 1. Log in to the target instance

```sh
hsctl login --url https://api.hyp0.infra.hypershell.app \
  --issuer-url https://keycloak.hyp0.infra.hypershell.app/realms/hypershell
```

`hsctl` holds one active context, and nothing in the commands below names the
instance. Confirm which one you are on before every mutating step.

### 2. Create the gateway

```sh
make check-hypershell
hsctl apply -k .hypershell/overlays/integration
```

Use `overlays/staging` for hyp1 and `overlays/prod` for hyp2.

### 3. Find the gateway ID

The default columns omit the name, phase and status even though the API
returns all three:

```sh
hsctl list gateways --columns "id, name, phase, status, route_address, cluster_id"
```

A populated `CLUSTER ID` means placement resolved and the control plane is
provisioning. An empty one means the gateway is awaiting placement because no
cluster advertises both `provider/ibm` and `network/public` — recoverable, it
binds when a matching cluster appears.

**`Running` / `Healthy` does not mean reachable.** It means the workload is
healthy. A gateway with no `route` provisions, reports Running, and has no
endpoint at all. Check `route_address` is populated, not just the phase. If it
is empty on a gateway whose manifest sets `route`, the control plane's
exposure resolver may not be configured on that instance — the address is
published only when both a route is declared and a resolver exists.

For the OIDC values the `openshell` CLI and the CronJobs need:

```sh
hsctl list gateways --output json | jq '.items[] | {name, route_address, oidc}'
```

`oidc` is a JSON-encoded string carrying the issuer, client ID and audience.

Export the ID for the remaining steps:

```sh
GATEWAY_ID=<id from above>
```

### 4. Grant access

Run one command per entry under `owners:` in [`hypershell-access.yaml`](./hypershell-access.yaml):

```sh
hsctl create gatewayAccess --gateway-id "$GATEWAY_ID" --user <username> --role owner
```

For anyone not listed there, `--role user`. Verify:

```sh
hsctl list gatewayAccess --gateway-id "$GATEWAY_ID"
```

To correct a tier afterwards, change it in place rather than revoking and
regranting, which keeps last-owner protection intact. It takes the
`user_id` from the access list, not the username:

```sh
hsctl update gatewayAccess <user_id> --gateway-id "$GATEWAY_ID" --role admin
```

### 5. Create the service account

One per entry under `service_accounts:` in [`hypershell-access.yaml`](./hypershell-access.yaml),
using the `name`, `role` and `expires_in` recorded there:

```sh
hsctl create serviceAccount --gateway-id "$GATEWAY_ID" \
  --name <name> --role <role> --expires-in <expires_in> \
  --output-file ./<name>.json
```

The client secret is in that file and is **shown once**. Move it into the
secret store, then delete the file. Do not commit it.

To change the role of an existing service account, revoke and recreate it —
the role is immutable and this rotates the client secret:

```sh
hsctl revoke serviceAccount <id> --gateway-id "$GATEWAY_ID"
```

Recreating under the same name fails with `409
service_account_name_exists` unless every other account with that name on
the gateway is already `expired` or `revoked`. Those two statuses are the
only ones that release a name; `provisioning`, `ready`, `revoking`,
`deleting` and `error` all keep holding it, and the comparison is
case-insensitive. If a create 409s unexpectedly, list the gateway's accounts
and check for a leftover in one of those states:

```sh
hsctl list serviceAccounts --gateway-id "$GATEWAY_ID"
```

Stale accounts are not free, either: creation is bounded by both a
per-creator and a per-gateway active quota, so abandoned entries eventually
block new ones.

### 6. Repeat per environment

Steps 1–5 are identical for staging (hyp1) and prod (hyp2), changing only the
login URL and the overlay. Each instance has its own database, so each gets
its own gateway, its own grants and its own service account.

## Replacing a gateway on a new version

**Gateways are not upgradeable in place.** A new OpenShell release means
deleting and recreating every gateway, so treat this as a routine operation
rather than an incident.

Nothing gateway-scoped survives. Access grants are `RoleBinding`s carrying a
`gateway_id`, and service accounts are a gateway-nested sub-resource, so both
die with the gateway — and the replacement service account gets a **new client
secret and a new subject**. Anything holding the old credential breaks at the
moment of deletion, so plan the window rather than discovering it.

Order, per instance:

1. Note the current `route_address` and `oidc` — consumers will need the new
   values to compare against.
2. Delete the gateway. This cascades: namespace, workload, grants, service
   accounts.
3. Re-run steps 2–5 of the runbook above: apply the overlay, find the new ID,
   re-grant every entry in [`hypershell-access.yaml`](./hypershell-access.yaml), re-mint the service
   account.
4. Write the new client secret to the secret store, replacing the old one.
5. Repoint consumers at the new `route_address` and OIDC values.

Version is **observed, not declared**: `gateway_version` is polled from the
running gateway and written back by the control plane, and
`provisioning_version` is derived from the cluster's `gateway-version`
capability. So a manifest cannot by itself request a version.

What a manifest *can* do is constrain placement to a cluster advertising a
given version, by adding a selector label:

```yaml
placement:
  selector:
    - provider/ibm
    - network/public
    - gateway-version/v0.1.5-rhaiv.0
```

The `gateway-version` namespace relaxes the usual DNS-label rule to allow the
dotted release tag. Because placement is create-only, a version pin and the
delete-and-recreate cycle fit together: the selector records which version the
gateway was built for, and changing it requires the recreate that was needed
anyway. The cost is that the gateway will not place at all if no cluster
advertises that exact version, so pin only when that is the intent.
