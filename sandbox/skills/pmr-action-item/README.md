# PMR Action Item Job

Automated processing of PMR (Post-Mortem Review) action items labeled `PMR-AI-PILOT` in Jira.

## Architecture

Two-tier job pattern:

### 1. Enumerator (CronJob)

**Runs**: Daily at 04:00 UTC  
**File**: `job-pmr-action-item-enumerator-cron.yaml`  
**ServiceAccount**: `rosa-agent-bot-3`

The enumerator:

1. Queries Jira for open PMR-AI-PILOT tickets assigned to `rosa-agent`
   - JQL: `project = ROSAENG AND labels = PMR-AI-PILOT AND assignee = rosa-agent AND status not in (Done, Closed, Resolved)`
2. Creates one Kubernetes Job per ticket found
3. Exits (worker Jobs run independently)

**Timeout**: 10 minutes (enumeration only)

### 2. Worker Jobs (created dynamically)

**Created by**: Enumerator  
**Naming**: `pmr-worker-<lowercase-issue-key>` (e.g., `pmr-worker-rosaeng-12345`)  
**ServiceAccount**: `rosa-agent-bot-3` (same as enumerator)

Each worker Job:

1. Processes exactly one Jira ticket (`/pmr-action-item <ISSUE-KEY>`)
2. Reads the ticket, identifies the repo, forks and syncs
3. Implements the change, runs tests
4. Runs security review (parallel subagents + /security-review skill)
5. Opens a PR if clean, or comments blockers on the ticket

**Timeout**: 1 hour per ticket  
**Parallelism**: Workers run in parallel (Kubernetes schedules them)  
**Isolation**: Each worker gets its own sandbox, avoiding fork contention

## RBAC

The `rosa-agent-bot-3` ServiceAccount needs:

- `get` on Secret `openshell-oidc` (OIDC client secret from Vault)
- `get`, `list`, `watch`, `create`, `delete` on `batch/jobs` (enumerator spawns workers)

## Deployment

Applied directly to `rosa-agent-stage` namespace with `oc apply`:

```bash
oc apply -f sandbox/skills/pmr-action-item/job-pmr-action-item-enumerator-cron.yaml
```

Not deployed via Konflux (Konflux build clusters cannot reach the HyperShell OIDC endpoint).

## Monitoring

**Enumerator logs** show:

- How many tickets were found
- Which worker Jobs were created

**Worker Job logs** (one per ticket):

- Which ticket is being processed
- Fork sync, implementation, security review, PR creation
- Any blockers or failures

List active workers:

```bash
kubectl get jobs -n rosa-agent-stage -l app=pmr-action-item-worker
```

View a specific worker's logs:

```bash
kubectl logs -n rosa-agent-stage job/pmr-worker-rosaeng-12345
```

## Cleanup

Worker Jobs are **automatically deleted 24 hours after completion** via `ttlSecondsAfterFinished: 86400`. This gives enough time to review logs while preventing indefinite accumulation.

To clean up old workers manually (before the TTL expires):

```bash
kubectl delete jobs -n rosa-agent-stage -l app=pmr-action-item-worker --field-selector status.successful=1
```

Or delete all (successful and failed):

```bash
kubectl delete jobs -n rosa-agent-stage -l app=pmr-action-item-worker
```

## Why This Pattern?

**vs. Sequential processing in one Pod**:

- Avoids single point of failure (one ticket's 403/blocker doesn't stop others)
- Parallel execution (faster wall-clock time)
- Better observability (one Job/log per ticket)

**vs. Indexed Job fan-out** (like `job-image-vuln-check`):

- Dynamic: ticket count varies day to day (can't pre-size `completions`)
- Clearer mapping: Job name contains the issue key

**Why not ArgoCD Workflows / Tekton**:

- Keeps the same primitives as other rosa-agent jobs (plain CronJob + Job)
- No additional controller dependencies
