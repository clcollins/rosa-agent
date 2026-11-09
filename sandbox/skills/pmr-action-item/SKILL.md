---
name: pmr-action-item
description: Investigate and resolve a PMR (Post-Mortem Review) action-item Jira ticket (labels pmr-ai and pmr-ai-ready) assigned to the rosa-agent user - read the ticket, fork the repo it concerns, implement the described work on a feature branch, run security review, then critical code review with a different model, implement review findings, re-run security check, and open a PR. Built for per-ticket invocation (one issue key per run); a future job/cron could enumerate pmr-ai-ready tickets and invoke this once per key, the way job-image-vuln-check fans out over targets. Use when asked to investigate, work, or action a PMR ticket/action item, or when given a Jira issue key labeled pmr-ai or pmr-ai-ready. Trigger keywords - PMR, PMR-AI, PMR action item, pmr-ai, pmr-ai-ready, post-mortem action item, investigate ticket, action item ticket.
---

# PMR action item: investigate a ticket, fix it, PR it

This skill handles one Jira ticket per run - a PMR (Post-Mortem Review)
action item labeled `pmr-ai` and `pmr-ai-ready`, assigned to the `rosa-agent` user. It is not
itself a scheduled job: it takes a single issue key and works that one
ticket end to end. A future enumerator/CronJob could discover every open
`pmr-ai-ready` ticket assigned to `rosa-agent` and invoke this skill once per key
(mirroring how `job-image-vuln-check` fans out over its target list), but
that enumeration is out of scope here.

**The `pmr-ai-ready` label indicates:**

- The ticket has been through the proposal stage (`pmr-remediation-analysis` skill)
- A structured remediation proposal exists as a Jira comment
- Human grooming gate has approved (or is pending)
- The ticket is ready for implementation

**For cron/enumerator use:** any script that feeds issue keys to this skill MUST
filter by `statusCategory = "To Do"` in its JQL. Tickets already In Progress or
Done must never be handed to this skill — the status transition in step 2b is the
distributed lock that makes this work across concurrent runs.

Example enumerator JQL:

```text
labels = "pmr-ai" AND labels = "pmr-ai-ready" AND assignee = "<rosa-agent accountId>"
  AND statusCategory = "To Do" ORDER BY created ASC
```

Retrieval/mechanics that are genuinely deterministic (fork sync, failure
reporting) are scripted below, exactly like the other job skills in this
repo. Everything else - reading the ticket, finding the right repo, writing
the fix, deciding whether it's safe to ship - is your own judgment, not
scripted.

**This entire run is non-interactive.** It's invoked as `claude
--dangerously-skip-permissions --print "<prompt>"` (directly by a human, or
later by a cron-driven sandbox) with no one watching and no terminal to type
into. There is no human to answer a question or approve a step at any point
before step 8 - the PR itself is the first and only place a human reviews
this run's work. Never pause expecting input, and never phrase anything as a
question you expect a reply to. Every "I don't know X" case below has exactly
two non-interactive outs: a missing/invalid invocation (no issue key, an
unrecognized key, etc.) is a usage error - stop and `file_failure_issue`
(the Failures section below); anything ticket-specific you can't resolve on your own (which
repo, an unsafe finding, failing tests, a blocked endpoint) is a blocker -
stop and comment on the ticket (step 7). Never guess past either case just to
keep moving.

## 1. Get your target

You need a single Jira issue key (e.g. `/pmr-action-item ROSAENG-62312`, or
named in the invoking prompt). **Never guess a key.** If none was given, this
is a usage error, not a blocker to negotiate with the ticket - there's no
ticket yet to comment on. Stop and report it via `file_failure_issue` (see
the Failures section below): a human reads that later, not a live prompt.

**Validate the key format** before using it anywhere. It must match
`^[A-Z][A-Z0-9]+-[0-9]+$` (e.g. `ROSAENG-62312`, `MCS-207`). If it doesn't,
this is a usage error — stop and `file_failure_issue`. Never interpolate an
unvalidated key into shell commands or Jira API paths.

## 2. Read the ticket

Per `AGENTS.md`, any Jira call MUST go through the `jira` skill - load it now
and run its auth-detection block once. Then:

`GET $JB/rest/api/3/issue/<ISSUE-KEY>`

- Confirm BOTH the `pmr-ai` and `pmr-ai-ready` labels are present on the ticket.
  - If `pmr-ai` is present but `pmr-ai-ready` is missing → the ticket hasn't
    completed the proposal stage (`pmr-remediation-analysis` skill). This is a
    ticket-specific condition, not a job failure — comment on the ticket (step 10
    style) that it's missing `pmr-ai-ready` and stop. Do **not** call
    `file_failure_issue`; the enumerator JQL includes `pmr-ai-ready` so cron
    can't hit this path, making `file_failure_issue` spam-on-every-run.
  - If neither label is present → stop and report via `file_failure_issue`
    (bad invocation — this skill only handles PMR action items, and there's no
    ticket to comment on).
- Read the full description and every comment - the work to do is described
  there, not inferred from the summary line alone.
- **Check for the remediation proposal comment now.** Search the ticket's
  comments for one beginning with the exact marker:
  `PMR Remediation Analysis: Proposal complete — pmr-ai-ready tagged.`
  This comment is posted by `pmr-remediation-analysis` step 8 before the
  `pmr-ai-ready` tag is applied. If no such comment exists, this is a step 10
  blocker — comment on the ticket that the proposal comment is missing and stop.
  Do not proceed to implementation: the ticket may be misconfigured, or the
  skill may have posted to stdout only. Checking here (not at step 7) avoids
  wasting a full implementation run on a misconfigured ticket.
- **Do not self-assign the ticket.** Assignment to `rosa-agent` is expected
  to already be set by whatever triggered this run - never call `PUT
  .../assignee` yourself. If the ticket is assigned to someone else, stop
  there - note it in the final output and do not touch their ticket; don't
  `file_failure_issue` for this, it isn't a failure.

## 2b. Transition ticket to In Progress

Immediately transition the Jira ticket to **In Progress**. This is the
distributed lock: a second cron run that picks up the same ticket from the "To
Do" queue will find it already In Progress and skip it (because the enumerator
filters by `statusCategory = "To Do"`).

**Get the available transitions:**

```shell
jira -H "Accept: application/json" \
  "$JB/rest/api/3/issue/<ISSUE-KEY>/transitions"
```

Find the transition whose `name` is `"In Progress"` (or the nearest equivalent
in this project's workflow — look for `to.statusCategory.key = "indeterminate"`).
Extract its numeric `id`.

**Apply the transition:**

```shell
jira -H "Content-Type: application/json" \
  -X POST "$JB/rest/api/3/issue/<ISSUE-KEY>/transitions" \
  -d '{"transition":{"id":"<id from above>"}}'
```

**Interpret the response by status:**

- **2xx** → transition succeeded; continue.
- **400 or 409** → likely a workflow constraint or race (the ticket left "To Do"
  before this POST arrived). Stop quietly; do not call `file_failure_issue` —
  this is the race-condition guard working as designed.
- **401 or 403** → access error. This is a step 10 blocker: comment on the
  ticket and stop. Do not mistake a proxy denial for a race loss.

**Race condition note:** The Jira transition is a fast-path filter, not a hard
mutex. If "In Progress" is a global transition in this Jira workflow (reachable
from any status), a second run's POST will also succeed and both runs will
proceed past 2b. Jira provides no compare-and-swap for transitions. The hard,
atomic lock comes one step later in git (step 4):

```shell
git push --force-with-lease=refs/heads/${BRANCH}: origin "HEAD:refs/heads/${BRANCH}"
```

The empty expected value after the colon tells the server to reject the push
unless the remote ref does not yet exist. This is evaluated atomically by the
git server — the second run's push is rejected even when both runs are on
identical commits. **A rejected push means this run lost the race: transition
the ticket back to "To Do" and stop.** No ticket comment is needed; the other
run holds the lock and will comment if it hits a blocker.

**After a successful 2xx transition,** continue to step 3.

## 3. Identify the target repository

This is inference, not a script: look for an explicit `owner/repo` or GitHub
URL in the description/comments, the Jira project/component naming
conventions, or linked issues. If you genuinely cannot determine which repo
the ticket concerns, that's a blocker - go to step 10 (comment on the ticket
asking for the repo) rather than guessing one.

From this point on, `$UPSTREAM` is the owner/repo identified above (e.g.
`openshift-online/some-repo`) and `$FORK` is `rosa-agent/<repo-name>`.

## 3a. Check for an existing PR or branch (deduplication)

Now that you know `$UPSTREAM` and `$FORK`, check whether a prior run already
handled this ticket. This step runs after step 3 (not before) because PMR PRs
are opened from the fork against the upstream repo for the ticket's own target —
not against `openshift-online/rosa-agent`.

**Single API call covers all states** — open, merged, and closed-unmerged:

```shell
BRANCH="pmr-action-item/${ISSUE_KEY}"
PR_RESULT=$(gh api \
  "repos/${UPSTREAM}/pulls?head=rosa-agent:${BRANCH}&state=all" \
  --jq '.[0] | {number, html_url, state, merged_at}')
```

**Check for an existing branch** on the fork:

```shell
gh api "repos/${FORK}/branches/${BRANCH}" --jq '.name' >/dev/null 2>&1
BRANCH_EXISTS=$?  # 0 = branch exists; non-zero = 404 / not found
```

**Decision logic — and what to do with the In Progress lock in each case:**

- **PR found, `state=open`** — work is already in review. Leave the ticket In
  Progress (it correctly reflects active work). Ensure the remote link exists on
  the ticket (`POST .../remotelink` if not already there). Stop — not a failure.
- **PR found, `merged_at` not null** — ticket is done. Transition the ticket to
  Done (find a "Done" or "Closed" transition id and POST it), or if that
  transition is unavailable, leave a comment noting the PR is merged and the
  ticket should be closed. Stop.
- **PR found, `state=closed`, `merged_at` null** — prior run was abandoned; this
  is a step 10 blocker. Comment on the ticket noting the closed-unmerged PR
  (include URL), transition back to "To Do" (unlock), and stop. A human must
  clean up the branch and PR before retrying.
- **No PR, `$BRANCH_EXISTS=0`** (branch on fork, no PR) — a prior run may have
  crashed mid-push. This is a step 10 blocker: comment on the ticket noting the
  orphaned branch, transition back to "To Do" (unlock), and stop. Do not
  silently discard — nobody reads cron stdout.
- **No PR, no branch** → clear to proceed.

## 4. Fork and sync, then branch

Before touching anything in the resolved repo, sync your fork to its
upstream default branch (this never assumes `main` - it discovers the real
default branch, and never force-merges a divergence):

```shell
FORK=rosa-agent/<repo-name> UPSTREAM=<owner>/<repo> \
  bash /sandbox/.claude/skills/pmr-action-item/scripts/sync_fork.sh
```

- Exit code 2 means the fork has diverged from upstream; a failure Issue is
  already filed against `openshift-online/rosa-agent`. Stop - don't force a
  merge.
- Do this fresh every run, even against a repo you've synced before.

Then check out a feature branch named from the issue key, and immediately
push it with the atomic lease (the hard mutex against concurrent runs):

```shell
git checkout -b "pmr-action-item/<ISSUE-KEY>"
git push --force-with-lease=refs/heads/pmr-action-item/<ISSUE-KEY>: \
  origin "HEAD:refs/heads/pmr-action-item/<ISSUE-KEY>"
```

If the push is **rejected** (exit code non-zero, remote says the ref already
exists), another run beat you to this branch. Transition the ticket back to
"To Do" and stop — no comment needed, the other run holds the lock.
If the push succeeds, this run owns the branch for the lifetime of the ticket.
(Step 11 does the real content push; this push establishes the lease.)

## 5. Implement the work described in the ticket

Your own inference - there is no script for this step. Before changing
anything, run the target repo's own test suite (however it's invoked there -
`make test`, `go test ./...`, etc.) so you know the baseline is green. Make
the change the ticket describes. Run the same test suite again - it must
still pass. Commit, with a message that references the issue key.

For how the code itself is written and tested - TDD per task, pure functions
behind thin I/O shells, hermetic tests, typed errors, security defaults -
follow sections 2 and 3 of the `implement-ticket` skill. This skill owns the
run, so its mechanics win wherever the two differ: the branch name above,
the step 6 fan-out and its CONFIRMED/PLAUSIBLE finding format, the
non-interactive rules, blockers going on the ticket, and how the PR is
opened in step 8.

## 6. Security fan-out - mandatory, both of the following

Before opening any PR, vet the diff two ways:

1. **Fan out parallel subagents** (the Agent/Task tool) over the diff, each
   with a distinct, narrow focus:
   - secrets or credential leakage (anything that could expose a token,
     password, or the credential placeholders (openshell resolve-env) themselves);
   - injection or unsafe shell/eval/deserialization;
   - authorization or permission widening (e.g. a new endpoint, RBAC rule,
     or policy change that grants more than the ticket called for);
   - supply-chain/dependency risk (a new or bumped dependency, where it
     comes from, whether it's pinned).

   Each subagent reports findings as CONFIRMED or PLAUSIBLE, with an exact
   file/line and a concrete failure scenario - not vague suspicion.

2. **Separately, invoke this harness's own `security-review` skill** against
   the same diff, as a second, independent pass.

Any CONFIRMED finding from either pass means: **do not open the PR.** Either
fix it and re-run both passes until clean, or - if you can't safely fix it
yourself - treat it as a blocker (step 10): comment on the ticket describing
the exact finding, and stop. Never open a PR carrying an unresolved CONFIRMED
finding.

## 7. Two parallel code reviews with independent models - mandatory

After the security fan-out passes, run **two parallel reviews** using different
models. Fan both out simultaneously using the Agent tool so they run concurrently;
collect both sets of findings before proceeding.

### 7a. Code quality review

Spawn a subagent with **`model="fable"`** (or `model="opus"` if Fable is
unavailable, `model="haiku"` as a last fallback) to review code quality,
correctness, and best practices.

**Model availability:** If the preferred model (`fable`) is unavailable and you
fall back, do **not** silently downgrade. If 7a falls to `haiku` and 7b also
falls to `haiku`, both reviewers are the same model and the "independent model"
guarantee collapses — treat that as a step 10 blocker: comment on the ticket
stating the model availability issue and stop.

Give the reviewing agent:

- The full diff (`git diff main...HEAD` or equivalent)
- The original Jira ticket context (what was requested)
- The target repository's conventions (language, style, patterns observed)

Ask it to critically assess:

1. **Correctness** - does the code actually solve the ticket's requirement?
   Does it handle edge cases? Are there logic errors or off-by-one mistakes?
2. **Robustness** - error handling, nil/null checks, boundary conditions
3. **Maintainability** - code clarity, naming, structure, unnecessary complexity
4. **Testing** - are the existing tests sufficient? Are new tests needed for
   new code paths or edge cases?
5. **Integration** - does this fit the existing codebase patterns and
   conventions? Does it break encapsulation or introduce coupling?
6. **Performance** - obvious inefficiencies, unnecessary allocations, N+1
   queries

The reviewer must return **structured findings** with:

- **Severity**: CRITICAL (blocks PR), IMPORTANT (should fix), MINOR (nice-to-have)
- **File and line number**
- **Specific issue** - not vague ("improve error handling") but concrete
  ("function X doesn't check for nil Y before calling Y.Method()")
- **Suggested fix** - what specific change resolves it

### 7b. Proposal adherence review (PMR-specific) - mandatory

Spawn a **second, separate subagent** with **`model="opus"`** (or `model="haiku"`
if Opus is unavailable) to verify the implementation matches the approved
remediation proposal. This reviewer is scoped entirely to proposal adherence —
it does not re-do the quality review above.

**Retrieve the proposal** from the comment you verified in step 2 (the one
starting with `PMR Remediation Analysis: Proposal complete — pmr-ai-ready tagged.`).
By the time you reach step 7b, the proposal is already confirmed to exist.

Give the reviewing agent:

- The approved remediation proposal (the full comment text retrieved from Jira)
- The full diff (`git diff main...HEAD` or equivalent)
- The target repository's conventions (language, style, patterns observed in the codebase)

Ask it to assess:

1. **Failure mechanism addressed** - does the implementation actually prevent
   the failure mechanism identified in the proposal? Or does it treat a symptom
   while leaving the root cause intact?
2. **Scope adherence** - does the diff stay within the files and components
   named in the proposal? Any unexplained scope creep is a finding.
3. **Approach fidelity** - does the implementation follow the "How" described in
   the proposal, or did it take a materially different approach? If different,
   is the deviation justified and at least as sound?
4. **Verification implemented** - does the diff include the tests and
   observability work the proposal specified in its "Verification Approach"?
5. **Workaround check** - if the proposal called for a code/architectural fix,
   does the implementation deliver that (not a workaround)?

The reviewer must return structured findings with:

- **Severity**: CRITICAL (blocks PR), IMPORTANT (should fix), MINOR (noted only)
- **Proposal section** the finding relates to (Failure Mechanism / Proposed
  Change / Verification / Scope)
- **Specific discrepancy** — exact quote from proposal vs what the diff does

**CRITICAL findings from either 7a or 7b block the PR.**

### Handling findings from steps 7a and 7b

Collect all findings from both reviewers before acting. Then:

- **CRITICAL findings from 7a** → proceed to step 8 to implement (code fix).
  If you cannot safely implement, treat it as a step 10 blocker.
- **CRITICAL findings from 7b (adherence)** — two explicit cases:
  - The *implementation* can be changed to match the proposal (e.g. missing a
    test the proposal specified, or a scope-creep file) → proceed to step 8 to
    fix the implementation.
  - The *proposal itself appears wrong* (the approved approach is unsound,
    contradicts the codebase, or is impossible to implement correctly) →
    **do not try to fix the code**. This is a step 10 blocker: comment on the
    ticket with the exact discrepancy (quote the proposal section, explain why
    it is wrong), and stop. A human must update the proposal before this ticket
    can be retried.
- **IMPORTANT findings** should be implemented if straightforward.
- **MINOR findings** can be noted but don't block.

## 8. Implement review findings

For each CRITICAL and IMPORTANT finding from steps 7a and 7b:

1. Read the specific file and line (or proposal section for 7b findings)
2. Implement the fix (or a better alternative if you have one)
3. Run the test suite again - it must still pass
4. Commit with a message like: `Address review: <brief description>`

If you cannot safely implement a CRITICAL finding (it requires domain knowledge
you don't have, or the suggested fix itself seems wrong), treat it as a blocker
(step 10): comment on the ticket with the specific finding and why you can't
resolve it, then stop.

## 9. Re-run security fan-out after implementing review findings

After implementing review changes, **re-run the full security fan-out from
step 6** (parallel subagents + security-review skill) against the updated diff.

The review fixes may have introduced new code that needs security vetting. Any
CONFIRMED security finding at this stage means: fix it and loop back through
steps 7-9 until both security and both code reviews are clean, or treat it as a
blocker (step 10) if you can't safely fix it.

**Only proceed to step 11 (open PR) when security review AND both code reviews
(7a and 7b) are clean with no unresolved CONFIRMED/CRITICAL findings.**

## 10. Blockers - especially access issues - go on the ticket

You are running in an OpenShell sandbox behind a proxy-enforced,
allow-listed egress policy (`policies/default.yaml`) with the credential and
skill constraints in `AGENTS.md`. **Any access issue that stops you doing
the work is a blocker, not something to route around:**

- a `403` / `policy_denied` / `credential_endpoint_mismatch` response from
  anywhere (Jira, GitHub, the target repo's own CI/registry/etc.);
- a host or endpoint you'd need that isn't allow-listed;
- missing permissions on the target repo or fork (e.g. can't push, can't
  open a PR, can't attach a label - compare `job-image-vuln-check`'s and
  `github-labels`' own "report and stop on 403" convention);
- an expired/invalid credential placeholder;

and so are these, non-access blockers that equally stop forward progress:

- an ambiguous or underspecified requirement in the ticket (including "I
  can't tell which repo this is about" from step 3);
- a security finding from step 6 or 9 you can't safely resolve yourself;
- a critical code review finding from step 7 you can't safely resolve yourself;
- failing tests you can't get green;
- anything else that stops you from completing the ticket.

**Do not retry around it, guess past it, or silently stop.** Instead:

1. **Comment on the ticket** (via the `jira` skill) stating plainly what you were
   doing and the exact cause of the issue — the literal error, HTTP status, and
   endpoint/host where relevant — so engineers can triage and unblock.
2. **Transition the ticket back to "To Do"** (or a "Blocked" status if the
   workflow has one). The ticket is currently In Progress (locked by step 2b);
   leaving it In Progress with no PR and no comment means it will never be
   retried by the enumerator and no one will notice. Unlock it:

   ```shell
   # Get transitions again and find the "To Do" (or "Blocked") transition id
   jira -H "Accept: application/json" \
     "$JB/rest/api/3/issue/<ISSUE-KEY>/transitions"
   jira -H "Content-Type: application/json" \
     -X POST "$JB/rest/api/3/issue/<ISSUE-KEY>/transitions" \
     -d '{"transition":{"id":"<to-do transition id>"}}'
   ```

   If the transition back also fails, log it — but do not stop reporting the
   original blocker just because the unlock failed.

This is a normal, expected outcome of hitting the edge of what this sandbox can
reach — not a failure to hide or apologize for.

## 11. Open the PR

Push the branch, then open the PR with this skill's attribution label via
the `github-labels` skill (never construct the label-attach calls yourself):

```shell
git push origin "pmr-action-item/<ISSUE-KEY>"
FORK_OWNER=rosa-agent UPSTREAM=<owner>/<repo> \
  BRANCH="pmr-action-item/<ISSUE-KEY>" \
  TITLE="<ISSUE-KEY>: <short description>" \
  BODY="<summary of the change, referencing <ISSUE-KEY>>" \
  bash /sandbox/.claude/skills/github-labels/open-labeled-pr.sh
```

## 12. Link back to the ticket

If step 10 didn't already fire for this run: add a Jira remote (web) link on
the ticket pointing at the new PR (`POST .../remotelink`), and post a short
comment summarizing what was changed and why.

## Guardrails

- Never force-push, and never force a fork-sync past a divergence.
- Never skip or weaken the security fan-out in step 6 or 9 to save time.
- Never skip either code review in step 7 (7a quality review and 7b proposal
  adherence review) — both are mandatory. Running them in parallel is efficient
  but sequential execution is also valid; what is not valid is skipping one.
- Never skip the deduplication check (step 3a) or the In Progress transition
  (step 2b) — these are what prevent duplicate PRs on cron-driven runs.
- Never leave a ticket In Progress on exit without either a PR link (step 12)
  or a blocker comment (step 10). If you are stopping for any reason after step
  2b, always comment on the ticket and transition it back to "To Do".
- Never invent a target repository, a Jira host/email, or any credential -
  if you don't know it, that's a blocker (step 10), not something to guess.
- Never retry past a `403` or any other access issue - report the exact
  cause (via a ticket comment if it's ticket-specific per step 10, via
  `file_failure_issue` if it's this job's own mechanics) and stop.
- One ticket, one PR per run.

## Failures

A genuine technical failure in this job's own mechanics (not a ticket-specific
blocker - see step 10 for those) gets reported via `file_failure_issue`
(sourced from `scripts/common.sh`) against `openshift-online/rosa-agent` -
never retried, never routed around. `sync_fork.sh` already calls it on its
own failure; call it directly for anything else:

```shell
source /sandbox/.claude/skills/pmr-action-item/scripts/common.sh
file_failure_issue "<stage>" "<exact captured error>"
```

If the failure occurs after step 2b (the ticket is In Progress), also
transition the ticket back to "To Do" and add a blocker comment per step 10
before calling `file_failure_issue`. The GitHub issue documents the job
failure; the ticket comment documents what needs unblocking.
