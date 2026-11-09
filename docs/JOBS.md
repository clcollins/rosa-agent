# Jobs

Jobs are non-interactive, scheduled tasks the agent runs (currently as plain `batch/v1`
CronJobs applied directly to the `rosa-agent-stage` namespace, bypassing Konflux — see
[Scheduled jobs](#scheduled-jobs) below) using a baked-in skill that scopes the work. Job
skills live under `sandbox/skills/`.

* **`job-sop-improve`** (`sandbox/skills/job-sop-improve/SKILL.md`) — grooms one stale SOP in
    [openshift/ops-sop](https://github.com/openshift/ops-sop) per run. It first fast-forwards the
    agent's fork to upstream's default branch, then selects a single SOP at random from those not
    updated in the last 3 months and not already in an open PR. Stale SOPs (3 months–3 years old) are
    improved by wrapping that repo's own `/sop-improve auto-commit` skill and opening a PR (labeled
    `ROSA-Agent`) from a feature branch. SOPs untouched for over 3 years are not edited — instead the
    job files a GitHub Issue against upstream suggesting the SOP be evaluated as potentially obsolete.
    Any failure that stops the job opens an Issue against this repo (`openshift-online/rosa-agent`).
    Deployed directly to the `rosa-agent-stage` namespace (see `job-sop-improve-cron.yaml`),
    weekdays at 22:00 UTC (`0 22 * * 1-5`).

* **`job-image-vuln-check`** (`sandbox/skills/job-image-vuln-check/SKILL.md`) — checks one or more
    quay.io images for fixable CVEs and remediates them via PR. Retrieval wraps the
    `quay-vuln-report` skill (never reimplemented); the image and its source GitHub repo are always
    given explicitly to the job (or resolved from the image's OCI labels via `skopeo inspect`) —
    nothing is hardcoded to this repo's own image. For each fixable CVE it locates the version pin,
    verifies a real fix exists (never downgrading), runs the repo's tests before and after the bump,
    and opens a fix-only PR — a second, separate PR follows only if new test coverage was needed.
    Any failure opens an Issue against this repo. Deployed directly to the `rosa-agent-stage`
    namespace (see `job-image-vuln-check-cron.yaml`) as a single CronJob fanned out across multiple
    targets — see [Scheduled jobs](#scheduled-jobs) below for how the per-target fan-out works.

* **`job-ops-sop-pr-review`** (`sandbox/skills/job-ops-sop-pr-review/SKILL.md`) — critically
    reviews every open PR on [openshift/ops-sop](https://github.com/openshift/ops-sop) older than
    two weeks. Enumeration, filtering, CI-status classification, and ping-comment text are all
    computed deterministically by the bundled `ops_sop_pr_review` stdlib package (hermetic
    `unittest` coverage under `tests/`); the agent applies that repo's own `sop-improve` skill's
    Section 5 ("Verify referenced tools and operators") to check referenced commands/tools against
    real upstream source, reading existing PR comments/reviews as context so it credits rather than
    repeats prior reviewer concerns. It posts one approve/do-not-approve recommendation comment per
    PR, plus (where applicable) a single comment consolidating every author-directed ping — needs-
    rebase, failing CI (excluding tide's lgtm/approve context), and staleness (>90 days, ccing active
    reviewers) — into one notification, and a separate comment pinging whoever applied a
    `do-not-merge/hold` label to ask for re-review. It never edits, merges, or labels a PR, and never
    reviews `work-in-progress/hold` PRs or PRs it authored itself. It always tries to complete as
    much of the sweep as possible — one PR's failure doesn't stop the rest — and files at most one
    Issue against this repo per run for a genuine technical problem (not for "nothing to review").
    Deployed directly to the `rosa-agent-stage` namespace (see `job-ops-sop-pr-review-cron.yaml`),
    weekly on Fridays at 22:00 UTC (12:00 HST).

* **`pmr-action-item`** (`sandbox/skills/pmr-action-item/SKILL.md`) — enumerates and processes
    PMR (Post-Mortem Review) action items. Uses a two-tier architecture: an enumerator CronJob
    queries Jira for open tickets labeled `pmr-ai` and `pmr-ai-ready` assigned to `rosa-agent`
    with `statusCategory = "To Do"`, then spawns one worker Job per ticket. Each worker reads the
    ticket, identifies the target repo, syncs the rosa-agent fork, implements the described change
    on a feature branch, runs the repo's test suite, executes a mandatory security review (parallel
    subagents for secrets/injection/authz/supply-chain risks plus the `/security-review` skill),
    runs two parallel code reviews (7a quality with Fable, 7b proposal adherence with Opus), and
    opens a PR if clean or comments blockers on the ticket if not. Workers run in parallel but each
    processes exactly one ticket, avoiding fork contention. Any access issue (403, missing
    credential, egress block) or unresolvable blocker goes as a ticket comment, not a silent
    failure. Enumerator CronJob manifest (`job-pmr-action-item-enumerator-cron.yaml`) is not yet
    in the repo — see `SKILL.md` for the authoritative enumerator contract. Target: daily at
    04:00 UTC in the `rosa-agent-stage` namespace.

## Scheduled jobs

Three plain `batch/v1` CronJobs run the agent itself using `openshellctl`
(`gateway add`, `doctor`, then `sandbox create -f sandbox/templates/noninteractive.yaml`),
applied directly to the `rosa-agent-stage` namespace with `oc apply -f` rather than
through Konflux — Konflux's build clusters can't currently reach the
Hypershell OIDC endpoint (see the header comment in each `*-cron.yaml`, and
the `Scheduled jobs` comment in the `Makefile`). Each has a dedicated
`ServiceAccount` scoped to `get` the `openshell-oidc` Secret and
`get`/`list`/`watch` `batch` jobs, and consumes `OPENSHELL_OIDC_CLIENT_SECRET`
(see below) from that Secret.

`image-vuln-check` is the one exception to "one CronJob = one Pod per firing": its
`jobTemplate` uses `completionMode: Indexed` with `completions`/`parallelism` set to the
number of targets, so each firing creates one Job with one Pod per target — each Pod gets
its own `JOB_COMPLETION_INDEX` (injected automatically by the Job controller, not a
Downward API field the manifest wires up itself), its own sandbox, and its own `claude`
invocation. `backoffLimitPerIndex: 0` scopes retries to a single target, so one target's
pod failing doesn't terminate a sibling target's still-running pod. The target list itself
lives in the `image-vuln-check-targets` ConfigMap (also defined in
`job-image-vuln-check-cron.yaml`) as a JSON array — array position `N` is read by the Pod
whose `JOB_COMPLETION_INDEX` is `N`. Adding a target means appending one entry to that
array and bumping `completions`/`parallelism` to match its new length; no script changes
are needed.

| Job | Schedule | Invocation | ServiceAccount |
| --- | --- | --- | --- |
| `sop-improve` | weekdays, 22:00 UTC (`0 22 * * 1-5`) | `/job-sop-improve` | `rosa-agent-bot-0` |
| `image-vuln-check` | nightly, 03:07 UTC (`7 3 * * *`) | `/job-image-vuln-check --image ... --repository ...`, once per target in `image-vuln-check-targets` (currently `openshift-online/rosa-agent` and `openshift/ocm-container`) | `rosa-agent-bot-1` |
| `ops-sop-pr-review` | weekly, Fridays 22:00 UTC (`0 22 * * 5`) | `/job-ops-sop-pr-review` | `rosa-agent-bot-2` |
| `pmr-action-item` (enumerator + workers) | daily, 04:00 UTC (`0 4 * * *`) | Enumerator queries Jira, spawns worker Jobs; each worker runs `/pmr-action-item <ISSUE-KEY>` | `rosa-agent-bot-3` |

* [`job-sop-improve-cron.yaml`](../sandbox/skills/job-sop-improve/job-sop-improve-cron.yaml)
* [`job-image-vuln-check-cron.yaml`](../sandbox/skills/job-image-vuln-check/job-image-vuln-check-cron.yaml)
* [`job-ops-sop-pr-review-cron.yaml`](../sandbox/skills/job-ops-sop-pr-review/job-ops-sop-pr-review-cron.yaml)
* `job-pmr-action-item-enumerator-cron.yaml` — not yet in the repo (see `pmr-action-item/SKILL.md` for the enumerator contract)

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
