# `.hypershell/`

Declarative HyperShell configuration for this repository, in the same spirit
as `.github/` for GitHub Actions: repo-local automation config that is not
part of the platform itself.

This directory is a Kustomize root. Two different things render it, with the
same library and the same result:

- **`hsctl apply -k`** — renders locally and writes over the REST API. This is
  how the tree is applied today.
- **The HyperShell `RepositoryReconciler`** — reads this subtree from GitHub,
  renders it in-process and reconciles it into the API. Enabled on `hyp0`
  only at the time of writing, and requires the repo to be registered as a
  `Repository` by a `platform:admin`.

Both paths render the same `spec`, so the tree is authored once and the
driver is largely a deployment detail — with one known exception.

`hsctl apply` copies `metadata.description` into the create body, but no
importable Kind's create request accepts that field, and the API decodes with
`DisallowUnknownFields`. So a manifest carrying `metadata.description` is
ignored by the import and **rejected outright** by `hsctl apply`:

```text
400 ... Invalid request format: json: unknown field "description"
```

The same applies to any unrecognised key under `spec`. Describe resources in
YAML comments, not in `metadata`. `make check-hypershell` enforces both.

## Layout

```text
.hypershell/
├── kustomization.yaml          # validation root only — never apply this
├── base/
│   └── platform/
│       ├── kustomization.yaml
│       └── gateway.yaml        # the Gateway, prod identity
└── overlays/
    ├── integration/            # hyp0  → rosa-agent-integration
    ├── staging/                # hyp1  → rosa-agent-staging
    └── prod/                   # hyp2  → rosa-agent
```

## Environments

Each HyperShell instance is a separate deployment with its own database, so
the three gateways are independent resources that happen to be described by
one tree. Always apply an overlay, never the root:

```sh
hsctl apply -k .hypershell/overlays/integration   # hyp0
hsctl apply -k .hypershell/overlays/staging       # hyp1
hsctl apply -k .hypershell/overlays/prod          # hyp2
```

The gateways are deliberately named differently per instance even though
identical names would be legal. It keeps fleet-wide logs and dashboards
unambiguous, and it makes an apply against the wrong context fail visibly
instead of quietly succeeding.

The bare root renders the prod identity. It exists so `kustomize build
.hypershell/` is known-good; it is not an apply target and must not be used
as a `Repository.import_path`.

## What belongs here, and what does not

The import reconciles exactly seven Kinds:

`AgentRuntime`, `Gateway`, `InferenceRoute`, `ProviderBinding`,
`ProviderSpec`, `SandboxTemplate`, `SecretSource`

Anything else renders cleanly and is then **silently dropped**. In
particular `Repository`, `ManagedCluster`, `Role` and `RoleBinding` are
skipped without an error — `Repository` specifically to prevent recursive
self-registration.

That silence is why configuration which cannot be imported is applied by hand
instead, following the runbooks in `docs/`:

| Concern | Where | Why |
| --- | --- | --- |
| Gateway | here | importable |
| Access grants (owner/admin/user) | `docs/hypershell-gateways.md` | `RoleBinding` is not importable |
| Service accounts | `docs/hypershell-gateways.md` | not a declarative Kind at all; the client secret is returned once |
| Credential providers | `docs/hypershell-providers.md` | an OpenShell concept, not a HyperShell Kind |
| Repository registration | MCS-224 | not importable; needs `platform:admin`, REST only |

## Validation

```sh
make check-hypershell
```

This renders every build root and checks that each rendered resource is an
importable Kind with a `metadata.name` and no `metadata.description`, that
every `spec` key is one its create request actually accepts, that the Gateway
uses placement selectors rather than a `cluster_id`, that its capability
labels are well-formed, and that each overlay produces the gateway name its
instance expects.

Run it before every apply. It matters more than a normal lint because both
consumers of this tree fail quietly: non-importable Kinds are skipped without
error, and nothing ever writes `Repository.status`, so import health is
visible only in `hyp0` controller logs. A broken tree looks identical to a
working one from the outside.

`hsctl apply -k --dry-run` is **not** a substitute. It prints kind and name
only, validates nothing server-side, and still needs a live authenticated
connection.

## Gateway placement

The Gateway declares the *kind* of cluster it wants as capability-label
selectors rather than a `cluster_id`. The api-server resolves them to a
concrete `ManagedCluster` and assigns `cluster_id` itself. If nothing matches
yet, the gateway is created in a pending, awaiting-placement state and binds
when a matching cluster appears — so a wrong selector is recoverable.

Capability labels are `namespace/value`, drawn from a closed set of
namespaces (`provider`, `network`, `region`, `runtime`, `environment`,
`gateway-version`), one value per namespace.

Placement is applied at **create only** and is not exposed on the Gateway
read surface, so a later apply or import cannot change it. Changing placement
means deleting the gateway and recreating it.

## Foreign keys

Nothing here currently references another resource, which is deliberate:
`Gateway`, `SandboxTemplate` and `ProviderSpec` are the only importable Kinds
with no outbound foreign keys, and they are the only ones that can be
authored correctly today.

Foreign-key fields (`gateway_id`, `sandbox_template_id`, `provider_spec_id`,
`workspace_id`, `agent_runtime_id`, `secret_source_id`) are **not** resolved
by name. Both `hsctl apply` and the import writer pass those strings through
verbatim, and the API validates only that they are non-empty, while real
resource IDs are generated ULIDs. A name-valued foreign key is therefore
accepted and stored, and the resource then silently never works — an
`AgentRuntime` whose `gateway_id` does not resolve is simply excluded from
its cluster's scope and never scheduled.

Any resource added here that carries a foreign key must use the concrete
ULID, and the convention must be documented in this section when that
happens.
