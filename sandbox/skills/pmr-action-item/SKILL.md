---
name: pmr-action-item
description: Investigate and resolve a PMR (Post-Mortem Review) action-item Jira ticket (label PMR-AI) assigned to the rosa-agent user - read the ticket, fork the repo it concerns, implement the described work on a feature branch, fan out subagents plus the security-review skill to vet the diff, then open a PR. Built for per-ticket invocation (one issue key per run); a future job/cron could enumerate PMR-AI tickets and invoke this once per key, the way job-image-vuln-check fans out over targets. Use when asked to investigate, work, or action a PMR ticket/action item, or when given a Jira issue key labeled PMR-AI. Trigger keywords - PMR, PMR action item, PMR-AI, post-mortem action item, investigate ticket, action item ticket.
---

# PMR action item: investigate a ticket, fix it, PR it

This skill handles one Jira ticket per run - a PMR (Post-Mortem Review)
action item labeled `PMR-AI` and assigned to the `rosa-agent` user. It is not
itself a scheduled job: it takes a single issue key and works that one
ticket end to end. A future enumerator/CronJob could discover every open
`PMR-AI` ticket assigned to `rosa-agent` and invoke this skill once per key
(mirroring how `job-image-vuln-check` fans out over its target list), but
that enumeration is out of scope here.

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

## 2. Read the ticket

Per `AGENTS.md`, any Jira call MUST go through the `jira` skill - load it now
and run its auth-detection block once. Then:

`GET $JB/rest/api/3/issue/<ISSUE-KEY>`

- Confirm the `PMR-AI` label is actually present on the ticket. If it isn't,
  stop and report it via `file_failure_issue` (a bad invocation, not a
  ticket-specific blocker) - don't act on a ticket this skill wasn't meant
  for.
- Read the full description and every comment - the work to do is described
  there, not inferred from the summary line alone.
- **Do not self-assign the ticket.** Assignment to `rosa-agent` is expected
  to already be set by whatever triggered this run - never call `PUT
  .../assignee` yourself. If the ticket is assigned to someone else, stop
  there - note it in the final output and do not touch their ticket; don't
  `file_failure_issue` for this, it isn't a failure.

## 3. Identify the target repository

This is inference, not a script: look for an explicit `owner/repo` or GitHub
URL in the description/comments, the Jira project/component naming
conventions, or linked issues. If you genuinely cannot determine which repo
the ticket concerns, that's a blocker - go to step 7 (comment on the ticket
asking for the repo) rather than guessing one.

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

Then check out a feature branch named from the issue key, e.g.:

```shell
git checkout -b "pmr-action-item/<ISSUE-KEY>"
```

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
yourself - treat it as a blocker (step 7): comment on the ticket describing
the exact finding, and stop. Never open a PR carrying an unresolved CONFIRMED
finding.

## 7. Blockers - especially access issues - go on the ticket

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
- a security finding from step 6 you can't safely resolve yourself;
- failing tests you can't get green;
- anything else that stops you from completing the ticket.

**Do not retry around it, guess past it, or silently stop.** Instead, add a
comment to the ticket (via the `jira` skill) stating plainly what you were
doing and the exact cause of the issue - the literal error, HTTP status, and
endpoint/host where relevant - so the engineers watching the ticket can
triage it and figure out what to unblock, and the work can resume on a later
run. This is a normal, expected outcome of hitting the edge of what this
sandbox can reach - not a failure to hide or apologize for.

## 8. Open the PR

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

## 9. Link back to the ticket

If step 7 didn't already fire for this run: add a Jira remote (web) link on
the ticket pointing at the new PR (`POST .../remotelink`), and post a short
comment summarizing what was changed and why.

## Guardrails

- Never force-push, and never force a fork-sync past a divergence.
- Never skip or weaken the security fan-out in step 6 to save time.
- Never invent a target repository, a Jira host/email, or any credential -
  if you don't know it, that's a blocker (step 7), not something to guess.
- Never retry past a `403` or any other access issue - report the exact
  cause (via a ticket comment if it's ticket-specific per step 7, via
  `file_failure_issue` if it's this job's own mechanics) and stop.
- One ticket, one PR per run.

## Failures

A genuine technical failure in this job's own mechanics (not a ticket-specific
blocker - see step 7 for those) gets reported via `file_failure_issue`
(sourced from `scripts/common.sh`) against `openshift-online/rosa-agent` -
never retried, never routed around. `sync_fork.sh` already calls it on its
own failure; call it directly for anything else:

```shell
source /sandbox/.claude/skills/pmr-action-item/scripts/common.sh
file_failure_issue "<stage>" "<exact captured error>"
```
