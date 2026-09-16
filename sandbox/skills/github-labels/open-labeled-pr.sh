#!/usr/bin/env bash
# open-labeled-pr.sh — deterministic PR -> create-label-if-missing ->
# attach-label workflow. Every step below is a fixed gh api (REST-only)
# call with explicit success/failure handling — there is no judgment call
# for the caller to make once the required inputs are set.
#
# This generalizes the ad hoc "if --label fails because the label doesn't
# exist, create it" fallback previously used inline in job-sop-improve into
# a reusable script, and codifies the standard PR -> label -> failure-issue
# pattern used across ROSA-Agent jobs.
#
# Precondition: your fork of $UPSTREAM must already exist, and the feature
# branch must already be committed and pushed to it, BEFORE calling this
# script. This script does NOT fork, create branches, commit, or push — it
# only opens the PR, best-effort-creates the label if missing, and attaches
# it. (Deliberately not automated here: fork creation is async — GitHub
# returns 202 and the fork isn't clonable/pushable for a few seconds
# afterward — so a "fork then immediately push" step can't be a reliable
# part of a single synchronous script. Fork once, e.g.
# `gh api -X POST repos/$UPSTREAM/forks`, before you start working.)
#
# Usage:
#   FORK_OWNER=<your-account> \
#   UPSTREAM=<org>/<repo> \
#   BRANCH=<branch-already-pushed-to-your-fork> \
#   TITLE="<pr title>" \
#   BODY="<pr body>" \
#   [LABEL=ROSA-Agent] \
#   [LABEL_COLOR=793B90] \
#   [LABEL_DESC="ROSA-Agent-involvement label"] \
#   ./open-labeled-pr.sh
#
# Prints the new PR number on stdout on success.
#
# Exit codes:
#   0  PR opened; label attached (or a label-creation problem was reported
#      to stderr but attachment still succeeded, e.g. the label already
#      existed with different metadata)
#   1  usage error (a required env var is unset)
#   2  a required gh api call failed (default-branch lookup, PR creation, or
#      label attachment); a failure Issue was filed against
#      openshift-online/rosa-agent with the full captured error. Exception:
#      if filing that Issue itself also fails, both failures go to stderr.
#
# Permissions: the injected GitHub account needs at least "triage" access
# on $UPSTREAM for label creation and attachment (steps 2 and 3 below) to
# succeed — plain "read" is not enough. If it's missing, those calls fail
# with a GitHub 403 (distinguishable in the captured output from a sandbox
# proxy policy_denied — see the Issue body / stderr on failure), and the
# fix is to ask upstream's maintainers for triage access, not to touch the
# sandbox policy.

set -euo pipefail

: "${FORK_OWNER:?set FORK_OWNER (the account whose fork to use)}"
: "${UPSTREAM:?set UPSTREAM (org/repo to open the PR against)}"
: "${BRANCH:?set BRANCH (branch already pushed to your fork)}"
: "${TITLE:?set TITLE (PR title)}"
: "${BODY:?set BODY (PR body)}"
: "${LABEL:=ROSA-Agent}"
: "${LABEL_COLOR:=793B90}"
: "${LABEL_DESC:=ROSA-Agent-involvement label}"

fail() {
  local stage="$1" detail="$2"
  gh api -X POST "repos/openshift-online/rosa-agent/issues" \
    -f title="open-labeled-pr failure: $stage" \
    -f body="Automated PR-with-label workflow failed.

- Stage: $stage
- Upstream: $UPSTREAM
- Branch: $FORK_OWNER:$BRANCH

Captured error output:

\`\`\`
$detail
\`\`\`

🤖 Filed automatically by open-labeled-pr.sh." \
    >/dev/null 2>&1 \
    || echo "ALSO FAILED to file the failure Issue against openshift-online/rosa-agent" >&2
  echo "FAILED at stage '$stage':" >&2
  echo "$detail" >&2
  exit 2
}

# 1. Discover upstream's actual default branch — never hard-code main vs
#    master — then open the PR from the fork's branch against it.
DEFAULT_BRANCH_OUT="$(gh api "repos/$UPSTREAM" --jq '.default_branch' 2>&1)" \
  || fail "default-branch" "$DEFAULT_BRANCH_OUT"
DEFAULT_BRANCH="$DEFAULT_BRANCH_OUT"

PR_OUT="$(gh api -X POST "repos/$UPSTREAM/pulls" \
  -f title="$TITLE" -f body="$BODY" \
  -f head="$FORK_OWNER:$BRANCH" -f base="$DEFAULT_BRANCH" \
  --jq '.number' 2>&1)" \
  || fail "pr-create" "$PR_OUT"
PR_NUMBER="$PR_OUT"

# 2. Best-effort: create the label on upstream if it doesn't exist yet.
#    Check-then-create rather than create-and-swallow-422, so a policy
#    denial or permissions problem is visible on stderr instead of hidden
#    behind a blanket "|| true". This step never hard-fails the script —
#    if the label truly can't be created, step 3 (attach) will fail loudly
#    and go through the normal failure-Issue path instead.
GET_LABEL_OUT="$(gh api "repos/$UPSTREAM/labels/$LABEL" 2>&1)" && GET_LABEL_STATUS=0 || GET_LABEL_STATUS=$?
if [ "$GET_LABEL_STATUS" -ne 0 ]; then
  if echo "$GET_LABEL_OUT" | grep -qi "HTTP 404"; then
    CREATE_LABEL_OUT="$(gh api -X POST "repos/$UPSTREAM/labels" \
      -f name="$LABEL" -f color="$LABEL_COLOR" -f description="$LABEL_DESC" 2>&1)" \
      && CREATE_LABEL_STATUS=0 || CREATE_LABEL_STATUS=$?
    if [ "$CREATE_LABEL_STATUS" -ne 0 ] \
       && ! echo "$CREATE_LABEL_OUT" | grep -qi "already_exists\|HTTP 422"; then
      echo "WARNING: could not create label '$LABEL' on $UPSTREAM (continuing; step 3 may fail as a result):" >&2
      echo "$CREATE_LABEL_OUT" >&2
    fi
  else
    # GET failed for a reason OTHER than "not found" — e.g. a sandbox proxy
    # policy_denied, or a 403 because the account can't even read labels.
    # Do not assume "missing" and attempt to create; surface it instead.
    echo "WARNING: could not check whether label '$LABEL' exists on $UPSTREAM (continuing; step 3 may fail as a result):" >&2
    echo "$GET_LABEL_OUT" >&2
  fi
fi

# 3. Attach the label to the PR (the issues endpoint works for PRs too).
ATTACH_OUT="$(gh api -X POST "repos/$UPSTREAM/issues/$PR_NUMBER/labels" \
  -f "labels[]=$LABEL" 2>&1)" \
  || fail "label-attach" "PR #$PR_NUMBER: $ATTACH_OUT"

echo "$PR_NUMBER"
