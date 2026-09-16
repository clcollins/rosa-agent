---
name: job-sop-improve
description: Scheduled job that improves one randomly-selected, stale SOP in openshift/ops-sop by wrapping that repo's own /sop-improve skill and opening a PR (or files an obsolescence Issue for very old SOPs). Use when asked to run the SOP improvement job, groom/refresh ops-sops, pick a stale SOP to improve, or when triggered by a cron/scheduled invocation referencing ops-sop or sop-improve. Trigger keywords - sop-improve, ops-sop, SOP grooming, stale SOP, refresh runbook, scheduled SOP job.
---

# Job: improve one stale SOP in openshift/ops-sop

This is a **wrapper job**. It does NOT contain the SOP-editing logic. The actual
improvement is performed by the `/sop-improve` skill that lives **inside the
`openshift/ops-sop` repository itself**. Your job is to:

1. clone your fork and fast-forward it to upstream **first** (see the
   precondition below — do this before anything else),
2. pick exactly one eligible SOP at random,
3. decide whether it is merely *stale* (improve it) or *potentially obsolete*
   (do not touch it — file an Issue instead),
4. and, when improving, hand off to the repo's own `/sop-improve` skill and open
   a PR.

Because `/sop-improve` is loaded from the repo you just cloned, it is ALWAYS the
current upstream version on every run. Never cache, copy, vendor, or reimplement
`/sop-improve` here — always invoke the copy present in the freshly-cloned repo.

Credentials are pre-injected. Do NOT run `gh auth login` or any interactive
login; use the `GITHUB_TOKEN` placeholder verbatim (see the `github` skill).

## Non-negotiable precondition: your fork MUST be up to date with upstream

> **STOP. Read this before doing anything else.**
> You operate on **your fork** of `openshift/ops-sop`. Your fork can be many
> commits behind upstream. If you branch from a stale fork you will edit an old
> SOP, compute freshness from stale dates, and open a PR full of conflicts.
>
> **Before selecting or editing any SOP, your fork's default branch MUST be
> fast-forwarded to upstream's default branch.** This is mandatory every run —
> not just the first time.

Upstream is the parent repo `openshift/ops-sop`. Determine the default branch
from upstream (do not assume `master` vs `main`) and sync your fork to it:

```shell
UPSTREAM="openshift/ops-sop"

# Your fork's full name (org/repo). The github skill / injected identity tells
# you which account owns the fork; do not guess it.
FORK="<your-account>/ops-sop"

# 1. Discover upstream's default branch — never hard-code it.
DEFAULT_BRANCH="$(gh repo view "$UPSTREAM" --json defaultBranchRef \
  --jq '.defaultBranchRef.name')"

# 2. Clone your fork (or your existing clone) and wire up the upstream remote.
gh repo clone "$FORK" ops-sop -- --origin origin
cd ops-sop
git remote add upstream "https://github.com/$UPSTREAM.git" 2>/dev/null || true
git fetch upstream "$DEFAULT_BRANCH"

# 3. Fast-forward your fork's default branch to upstream and push it back so the
#    fork itself is current (not just this local checkout).
git checkout "$DEFAULT_BRANCH"
git merge --ff-only "upstream/$DEFAULT_BRANCH"
git push origin "$DEFAULT_BRANCH"
```

If the fast-forward fails (your fork's default branch has diverged), do NOT
force anything and do NOT proceed with a merge commit. Report the divergence and
stop — a diverged default branch is a fork-hygiene problem a human must resolve.

Every feature branch you create in this job MUST be based on
`upstream/$DEFAULT_BRANCH` (equivalently, the just-synced default branch), never
on an old local branch.

## Selecting the SOP (as random as possible)

Build the candidate set, then pick one uniformly at random. "Random" here means
genuinely unpredictable across runs — do not sort-and-take-first, do not always
pick the oldest, do not bias toward a directory.

1. **Enumerate SOP files** in the repo (the SOP content lives as tracked
   markdown files; use the repo's own layout — check `/sop-improve` /
   `CONTRIBUTING` docs in the clone for where SOPs live rather than guessing a
   path).

2. **Compute each SOP's last-substantive-update date** from git history of that
   file on the synced default branch:

   ```shell
   git log -1 --format=%cI -- "<path/to/sop.md>"
   ```

3. **Apply the eligibility filters** — a SOP is a candidate only if BOTH hold:
   * **Not updated in the last 3 months** (last-update date is older than 90
     days from today), AND
   * **Not touched by any open PR.** List open upstream PRs and the files they
     change, and exclude any SOP that appears in one:

     ```shell
     # Files changed by each open PR against upstream; exclude SOPs that appear.
     for pr in $(gh pr list --repo "$UPSTREAM" --state open --json number --jq '.[].number'); do
       gh pr view "$pr" --repo "$UPSTREAM" --json files --jq '.files[].path'
     done | sort -u
     ```

4. **Pick one candidate uniformly at random.** Example:

   ```shell
   # candidates.txt = eligible SOP paths, one per line
   shuf -n 1 candidates.txt
   ```

   If `shuf` is unavailable, use another unbiased source of randomness (e.g.
   `$RANDOM`/`$$` to index into the list) — the key property is that repeated
   runs do not converge on the same SOP.

If **no** SOP is eligible (all were updated within 3 months, or every stale one
is already in an open PR), do nothing and report that there was no eligible SOP
this run. This is a normal, successful outcome — not an error.

## Decision gate: improve, or flag as obsolete?

Once you have your single randomly-selected SOP, check its age:

* **Stale (older than 3 months, but updated within the last 3 years):**
  → **Improve it.** Proceed to *Improving the SOP* below.

* **Very old (NOT updated in the last 3 years):**
  → **Do NOT invoke `/sop-improve` and do NOT edit the file.** Instead, file a
  GitHub **Issue against upstream** (`openshift/ops-sop`) suggesting the SOP be
  evaluated as potentially obsolete:

  ```shell
  gh issue create --repo "$UPSTREAM" \
    --title "Evaluate potentially-obsolete SOP: <path/to/sop.md>" \
    --body "This SOP has not been updated in over 3 years (last update: <date>).
  It was selected by the automated SOP grooming job. Rather than auto-improving
  stale content that may no longer reflect current practice, this issue asks a
  maintainer to evaluate whether the SOP is still relevant, needs a rewrite, or
  should be retired.

  - File: <path/to/sop.md>
  - Last substantive update: <date>
  - Selected by: automated job-sop-improve run

  🤖 Filed by the automated SOP grooming job."
  ```

  Then stop — filing the Issue is the complete, successful outcome for a
  potentially-obsolete SOP. Do not also open a PR for it.

The 3-year threshold is a hard cutoff: 3 months ≤ age ≤ 3 years → improve;
age > 3 years → Issue only.

## Improving the SOP

For a stale-but-not-obsolete SOP:

1. **Create the feature branch from the synced upstream default branch:**

   ```shell
   git checkout -b "sop-improve/<short-sop-slug>" "upstream/$DEFAULT_BRANCH"
   ```

2. **Invoke the repo's own `/sop-improve` skill in auto-commit mode**, targeting
   the selected SOP. Use the invocation the freshly-cloned repo documents for
   `/sop-improve` — it is the source of truth for arguments and behavior:

   ```text
   /sop-improve auto-commit <path/to/sop.md>
   ```

   `auto-commit` means the skill makes its own commit(s); do not hand-edit the
   SOP yourself and do not re-implement what `/sop-improve` does. If
   `/sop-improve` is not present in the clone, stop and report it — do not
   substitute your own editing logic.

3. **Push the feature branch to your fork, then open and label the PR using
   the `github-labels` skill's deterministic script.** Do NOT use
   `gh pr create --label` or `gh pr edit --add-label` — both resolve the
   label through an internal GraphQL lookup, which the sandbox's GraphQL
   block denies regardless of REST permissions, so they fail every time
   regardless of whether the label exists:

   ```shell
   git push origin "sop-improve/<short-sop-slug>"

   PR_NUMBER="$(FORK_OWNER="<your-account>" \
     UPSTREAM="$UPSTREAM" \
     BRANCH="sop-improve/<short-sop-slug>" \
     TITLE="Improve SOP: <path/to/sop.md>" \
     BODY="Automated improvement of a stale SOP (last updated <date>),
   selected at random by the SOP grooming job and improved via this repo's
   /sop-improve skill.

   🤖 Opened by the automated job-sop-improve run." \
     bash /sandbox/.claude/skills/github-labels/open-labeled-pr.sh)"
   ```

   This discovers `$UPSTREAM`'s default branch, opens the PR, creates the
   `ROSA-Agent` label on `$UPSTREAM` if it doesn't exist yet, and attaches
   it — all via REST. The PR MUST carry the `ROSA-Agent` label so automated
   work is distinguishable from human contributions.

   `open-labeled-pr.sh` needs the injected account to have at least
   **triage** access on `$UPSTREAM` for label creation/attachment to
   succeed — if that's missing, it fails with a GitHub `403` and files its
   own failure Issue against `openshift-online/rosa-agent` (see that
   skill's *On failure* section); do not treat that as a proxy problem to
   debug, ask for triage access on `$UPSTREAM` instead. On any failure from
   this step, `open-labeled-pr.sh` has already filed the failure Issue
   itself — do not also file a second one from the *On any failure* section
   below for this specific step.

## On any failure or error: open an Issue against this job's repo

This is a scheduled, non-interactive job — there is no human watching it run, so
failures must be surfaced somewhere durable. If **any** step fails or errors in
a way that stops the job — the fork fast-forward fails / diverges, a required
`gh`/`git` command errors, `/sop-improve` is missing or fails, a request is
policy-denied (HTTP 403), or anything else prevents a clean completion —
open a GitHub **Issue against this job's own repository,
`openshift-online/rosa-agent`**, describing what happened. (The exception is
the PR-open-and-label step: `open-labeled-pr.sh` already files its own
failure Issue for that step — see above — so don't duplicate it here.)

```shell
gh issue create --repo "openshift-online/rosa-agent" \
  --title "job-sop-improve failure: <one-line summary>" \
  --body "The automated SOP grooming job failed.

- Stage: <sync | selection | decision-gate | sop-improve | pr | ...>
- Selected SOP (if any): <path/to/sop.md>
- Command that failed: <command>
- Error output (exact): <verbatim error, including any HTTP status / policy code>

🤖 Filed automatically by a failed job-sop-improve run."
```

Then stop. Do NOT retry variations, do NOT attempt to bypass the proxy, and do
NOT open the SOP PR or the obsolescence Issue against `openshift/ops-sop` for a
run that failed partway. The failure Issue always goes to
`openshift-online/rosa-agent`, never to the SOP upstream.

A run that legitimately finds **no eligible SOP** is NOT a failure — report it
normally and do not file a failure Issue.

## Guardrails

* One SOP per run. Never batch-edit multiple SOPs.
* Never push to upstream directly and never push to your fork's default branch
  except the fast-forward sync in the precondition. All work goes on a feature
  branch → PR.
* Never force-push. Never bypass `--ff-only` with a merge/rebase to "fix" a
  diverged fork — file a failure Issue (see *On any failure*) and stop.
* Never invent SOP paths, dates, or the default branch name — derive them from
  the synced clone and from `gh` against upstream.
* If any required step is blocked by policy (HTTP 403 / `policy_denied`), do not
  retry variations or attempt to bypass the proxy — file a failure Issue against
  `openshift-online/rosa-agent` (see *On any failure*) and stop.
