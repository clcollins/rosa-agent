---
name: github-labels
description: Deterministic script that opens a PR, best-effort-creates a label on upstream if it doesn't already exist, and attaches it to the PR. Use whenever a job needs to open a PR that must carry a specific label (e.g. ROSA-Agent) against a repo that may never have seen that label before. Trigger keywords - github label, create label, PR label, ROSA-Agent label, label does not exist, PR with label.
---

# github-labels: deterministic PR -> create-label -> attach-label

This skill is a single script, not instructions to interpret. Every step in
`open-labeled-pr.sh` is a fixed `gh api` (REST-only) call with explicit
success/failure handling — there is no judgment call left for the agent
once the required inputs are set.

It replaces the ad hoc English fallback previously used inline in jobs (e.g.
`job-sop-improve`'s "if `--label` fails because the label does not yet
exist, add it after" note, and its `gh pr create --label` /
`gh pr edit --add-label` calls) with one reusable, generalized script.
**Do not use `gh pr create --label` or `gh pr edit --add-label`** — both
resolve the label through an internal GraphQL lookup, which the sandbox's
GraphQL block denies regardless of what REST access you have.

## Precondition

Your fork of `$UPSTREAM` must already exist, and your feature branch must
already be committed and pushed to it, **before** calling this script. It
does not fork, create branches, commit, or push — only opens the PR,
best-effort-creates the label if missing, and attaches it.

Fork creation is deliberately not part of this script: `POST
repos/$UPSTREAM/forks` returns `202` and the fork isn't reliably
clonable/pushable for a few seconds afterward, so "fork, then immediately
push" can't be a dependable step inside one synchronous script. Fork once,
up front, e.g. `gh api -X POST repos/$UPSTREAM/forks`, then wait for it to
be ready before you branch and push.

## Usage

The image copies this repo's `sandbox/skills/` into `/sandbox/.agents/skills/`
and symlinks `/sandbox/.claude/skills` to it (see the Containerfile) — the
agent's cwd is `/sandbox`, so the repo-relative path does not exist at
runtime. Call it by its installed path:

```shell
FORK_OWNER=<your-account> \
UPSTREAM=<org>/<repo> \
BRANCH=<branch-already-pushed-to-your-fork> \
TITLE="<pr title>" \
BODY="<pr body>" \
bash /sandbox/.claude/skills/github-labels/open-labeled-pr.sh
```

Prints the new PR number on stdout. `LABEL`, `LABEL_COLOR`, and `LABEL_DESC`
are optional and default to the `ROSA-Agent` label convention already used
on `openshift/ops-sop` and `openshift-online/rosa-agent` (color `793B90`,
description `ROSA-Agent-involvement label`) — override them for a different
label:

```shell
LABEL=needs-review LABEL_COLOR=fbca04 LABEL_DESC="Needs a maintainer look" \
  ... bash /sandbox/.claude/skills/github-labels/open-labeled-pr.sh
```

## What it does, in order

1. `GET repos/$UPSTREAM` then `POST repos/$UPSTREAM/pulls` — discover
   upstream's actual default branch (never hard-code `main` vs `master`),
   then open the PR from `$FORK_OWNER:$BRANCH` against it.
2. `GET repos/$UPSTREAM/labels/$LABEL`, then `POST repos/$UPSTREAM/labels`
   only if that GET returned a genuine `404` — **best-effort** creation of
   `$LABEL` on upstream. An existing label's color/description is never
   overwritten. If the GET fails for any other reason (a sandbox proxy
   `policy_denied`, a permissions `403`, etc.), or the create call fails for
   a reason other than "already exists", the captured error is printed to
   stderr and the script continues — it does not assume the label problem
   is fatal, since step 3 is the real test of whether labeling actually
   works.
3. `POST repos/$UPSTREAM/issues/$PR_NUMBER/labels` — attach `$LABEL` to the
   PR (the issues endpoint works for PRs too). This is where a real
   label-creation problem from step 2 will surface as a hard failure.

## Permissions

Steps 2 and 3 need the injected GitHub account to have at least **triage**
access on `$UPSTREAM` — plain read is not enough to create or attach
labels. If it's missing, expect a GitHub `403` (visible in the captured
error, distinguishable from a sandbox proxy `policy_denied` by its body) on
every run. The fix is to ask `$UPSTREAM`'s maintainers for triage access,
not to change the sandbox policy.

## On failure

Any failed step (default-branch lookup, PR creation, or label attachment —
step 2's best-effort label creation is never fatal on its own) files a
GitHub Issue against `openshift-online/rosa-agent` — this job's own repo —
via `POST repos/openshift-online/rosa-agent/issues`, including the full
captured `gh api` error output, then exits non-zero. This mirrors
`job-sop-improve`'s failure-reporting convention: failures are always
surfaced against `openshift-online/rosa-agent`, never against the upstream
repo being contributed to.

## What this skill does NOT do

- Does not fork, create the branch, commit, or push — do that first (see
  *Precondition* above).
- Does not decide whether a PR needs a label or what it should be — that is
  the caller's decision.
- Does not retry on policy-denied (`403`) responses from the sandbox proxy.
  A `403` is treated the same as any other failure: report it (via stderr
  and, for the hard-failure stages, an Issue), stop. Do not retry
  variations or attempt to bypass the proxy.
