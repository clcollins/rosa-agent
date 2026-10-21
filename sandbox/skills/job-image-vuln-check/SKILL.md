---
name: job-image-vuln-check
description: Nightly job that checks a quay.io image for fixable CVEs and remediates them via PR. Retrieval is deterministic (wraps quay-vuln-report); remediation is agent-driven inference (locate the pin, verify a real fix exists, never downgrade, test before/after, split fix and new-tests into separate PRs). Before remediating, triages by package ecosystem: RPM/dnf packages (base-image-inherited or explicitly dnf-installed) are never fixed via a versionless `dnf update` forward-pin PR - only a base-image bump fixes those, tracked via a persistent issue past a grace period; everything else (go install/go.mod, pip, npm, pinned binary downloads) remains a normal version-bump PR target. Before fixing each such bucket, checks for an already-open PR covering it: exits with no action if it's the same CVE set, supersedes (new PR, close+reference old) if new CVEs joined the same still-open bucket. Also closes any already-open PR for an image whose CVEs are no longer present in the latest scan at all (e.g. an unrelated base-image bump already fixed them), so stale fix PRs don't linger. Use when asked to run the image vuln-check job, check an image for CVEs and fix them, or when triggered by a cron/scheduled invocation referencing job-image-vuln-check. Trigger keywords - image vuln check, nightly CVE job, fixable vulnerabilities, remediate CVE, job-image-vuln-check.
---

# Job: check a quay.io image for fixable CVEs and remediate them

Wrapper job. Retrieval is `quay-vuln-report`, invoked by the bundled
`job_image_vuln_check` package (never its internals - the CLI, exactly as
that skill's own SKILL.md documents). Remediation below is your own
inference, not scripted - the scripts only handle the deterministic parts.

## 1. Get your target

You need an image reference and (ideally) the GitHub repo that produced it.
Both come from the invocation (e.g. `/job-image-vuln-check --image
quay.io/ns/path:tag --repository owner/repo`, or a `--targets` JSON map for
several images). **Never hardcode an image or repo** - if none was given,
ask.

## 2. Retrieve

```shell
cd /sandbox/.claude/skills/job-image-vuln-check
python3 -m job_image_vuln_check --image <REF> [--repository <owner/repo>]
# or: --targets '{"<image-ref>": "<owner/repo-or-null>", ...}'
```

Output is a JSON array, one report per target, each with the CVE list
(`cve`, `severity`, `package`, `installed_version`, `fixed_in_version`,
`layer_introduced_in`, `cve_link`) plus `source_repository`.

- `vulnerability_count == 0` for a target → nothing to do there. If every
  target is 0, report success and stop. Normal outcome, not a failure.
- `source_repository: null` → a `note` explains why (no source label, or a
  skopeo error - e.g. quay.io's blob storage redirecting to a host this
  sandbox doesn't allow-list is a known, reportable case, see *Failures*).
  Use your own judgment (image name, quay.io repo description, web search)
  to identify the repo before acting on that target; skip it and say so in
  your summary if you genuinely can't.

## 3. Sync the target repo's fork

Before touching anything in a resolved repo:

```shell
FORK=<your-account>/<repo-name> UPSTREAM=<owner/repo> \
  bash /sandbox/.claude/skills/job-image-vuln-check/scripts/sync_fork.sh
```

Discovers the actual default branch itself - never assume `main`. Exit code
2 means the fork has diverged; a failure Issue is already filed, stop, don't
force a merge. Do this fresh for every run, even against a repo you synced
before - don't reuse a stale conclusion about what's still outstanding.

## 4. Close obsolete PRs

Before remediating anything new, check whether this run's scan has already
made any currently-open `job-image-vuln-check` PR for this image moot - e.g.
an unrelated base-image bump happened to carry a fix in before this job got
to it. This has real precedent: PR #48 forward-pinned `libxml2`, and a later
scan of the rebuilt image shows zero `libxml2` CVEs at all - if that fix had
instead landed some other way while #48 was still open, #48 itself would
have been exactly this kind of obsolete PR.

Build `CURRENT_CVES`: the comma-separated list of every CVE ID in this run's
step-2 retrieval for this image, across *all* packages (not just one
bucket), then:

```shell
UPSTREAM=<owner/repo> FORK_OWNER=<your-account> IMAGE=<image-ref> \
  CURRENT_CVES="<comma-separated CVE IDs from this run's retrieval, or empty>" \
  bash /sandbox/.claude/skills/job-image-vuln-check/scripts/close_obsolete_prs.sh
```

- Closes (with an explanatory comment) any open, marker-carrying PR for this
  exact image where **none** of its CVEs appear in `CURRENT_CVES` anymore -
  every CVE that PR exists to fix has already been resolved some other way.
  Prints `<pr-number><TAB><cves>` for each one it closes; prints nothing if
  none were obsolete (the normal case, including "no open marked PRs for
  this image at all").
- A PR with a **partial** overlap (some of its CVEs still present, some
  gone) is left untouched here - that bucket is still live. It goes through
  the step-0 dedup/supersede check in step 6 when it's reprocessed, not this
  script. Never close a PR on a partial match.
- `CURRENT_CVES` may legitimately be empty - that means this run found zero
  fixable CVEs on the image at all, so every open marked PR for it is
  obsolete.
- **Exit 2**: a failure Issue was already filed by the script for this
  target - stop, don't retry.

## 5. Triage: which ecosystem owns this fix?

Before remediating any bucket, classify it by **package ecosystem**, not by
whether something happens to pin a version already. This distinction exists
because of real precedent (see
[openshift-online/rosa-agent#91](https://github.com/openshift-online/rosa-agent/issues/91)):

- **RPM/dnf packages — never a version-bump PR here, regardless of whether
  the package rode in via the base image or is `dnf install`ed explicitly in
  this Containerfile.** The fix is exclusively a base-image bump. PRs #77
  and #78 forward-pinned `gdb-gdbserver` and `gawk` via `dnf -y update <pkg>`
  - a "pin" to no particular version, i.e. just "whatever's newest right
    now" - and both were already redundant when opened: the UBI base image
    had already shipped the fixed RPM. (The honest version of that timeline:
    #77/#78 were opened from a scan taken *before* the UBI bump that fixed
    them had even merged - a plain race, not a broken pipeline. Don't
    over-claim a root cause you haven't verified with `skopeo inspect`
    against the actual image in question.) This applies to **every**
    RPM-sourced package, not just ones with no explicit repo pin - `go
    install`-based or other non-RPM tools that happen to be pinned are a
    different ecosystem entirely (see below) and are unaffected by this
    rule.
- **Everything else — go.mod/`go install foo@version`, `requirements.txt`/
  `pip install foo==version`, `package.json`/npm, a standalone binary
  download pinned to a version in an `ARG`/Containerfile line - is this
  repo's to fix via a normal version-bump PR**, exactly as before. This is
  the #79/#80/#87 case (`govulncheck`, `setup-envtest`, `golangci-lint`
  versions the Containerfile pins directly) - keep fixing these. It also
  covers a tool floating on an unpinned `latest`/`@main`-style reference
  where the image simply hasn't been rebuilt recently: the fix there is
  still a PR (forcing a rebuild, or - preferably, if it fits the repo's
  conventions - pinning an explicit version as part of it), never a
  base-image bump.

### Handling an RPM/dnf bucket

**Never open a `dnf -y update <pkg>` forward-pin PR for one of these** - it
names no version, so it isn't a real pin, and the base image almost always
ships the fix before a bot-opened PR would clear review (in the cases
checked for #91, Red Hat's errata-to-UBI-image lag was only ~4-6 days).
Instead:

1. Resolve the base image tag actually pinned in the target repo's
   Containerfile **at the target repo's current default-branch HEAD** (not a
   stale fork, not this run's checkout from before `sync_fork.sh` - re-read
   it after syncing).
2. Check whether that pinned tag already contains a version of the package
   meeting `fixed_in_version` (the Red Hat Catalog's package listing for
   that image tag is the real source here - this needs `catalog.redhat.com`
   egress, which is **not yet allow-listed**; until it is, treat this
   sub-step as blocked and say so rather than guessing from a `skopeo
   inspect` label set, which gives you labels, not an RPM manifest). If it
   already has the fix: **no PR** - report "already fixed in the pinned
   base image at HEAD" and move on.
3. If the pinned tag does **not** have the fix, check whether a **newer**
   UBI tag exists that does, and whether a MintMaker/Renovate PR is already
   open bumping toward (or past) that tag. If one is: **this bucket is
   already being handled - do nothing.** Same rule as an already-open
   `job-image-vuln-check` PR elsewhere in this skill: don't comment on it,
   don't open anything, don't escalate it, don't mention it in a report.
   Move on silently. If no newer tag has the fix yet, or one does but
   nothing is open to get there, there's genuinely nothing actionable until
   Red Hat ships it or MintMaker opens its own PR.
4. If no tag anywhere has the fix yet, and nothing is already open to
   deliver one, wait - escalate only once the errata is older than a grace
   period. Use Red Hat's own Container Health Index grace periods as the
   threshold: ~7 days for Critical, ~30 days for Important; never escalate
   Moderate/Low RPM CVEs (Red Hat's own grading doesn't count them either).
   Escalation means **updating one persistent tracking issue** for this
   repo+image (find it by a fixed title/marker, same discipline as the PR
   marker in step 6; create it once, edit/comment on it on later runs) -
   never a new PR, and never just a run-summary line nobody will read again.

### Sanity-check the scan before triaging anything

Compare the scanned image's own revision label (e.g.
`org.opencontainers.image.revision`, via `skopeo inspect` - the same
approach `repo_resolver.py` uses for the source-repo label, just a different
key) against the target repo's current default-branch HEAD commit. Treat a
mismatch as a **note to carry into your report, not an automatic stop**:
don't let it block remediating an ordinary non-RPM bucket (step 6 still
applies to those regardless), and don't assume it means a release pipeline
is stuck - a normal in-flight PR merged between the last release and
tonight's scan produces the same mismatch. Only escalate the mismatch itself
(via `file_failure_issue`) if it persists across multiple runs with no
release landing in between. Caveat: this relies on the image actually
carrying its own commit in that label rather than inheriting the base
image's - true for rosa-agent's own build today (spot-checked directly
against the production `:latest` image), but verify it holds for whatever
target you're checking before trusting it.

## 6. Remediate, one CVE-fix at a time

For buckets that step 5 identified as a non-RPM ecosystem's to fix (group
multiple CVE IDs that share the same package+fixed-version+layer into one
fix - this grouping is the "bucket" referred to below):

0. Check whether an open PR already covers this exact bucket before doing
   any work on it:

   ```shell
   UPSTREAM=<owner/repo> FORK_OWNER=<your-account> IMAGE=<image-ref> \
     PACKAGE=<package> \
     bash /sandbox/.claude/skills/job-image-vuln-check/scripts/find_existing_pr.sh
   ```

   - **Exit 3** (nothing printed): no open PR for this bucket yet - continue
     to step 1, no supersession involved.
   - **Exit 0**, prints `<pr-number><TAB><branch><TAB><old-cves>`: an open PR
     already exists for this exact image+package bucket.
     - `old-cves` (comma-separated) is the **same set** as this bucket's
       current CVE IDs → this bucket is already being handled. **Skip
       it - do not open a duplicate PR, do not touch the existing one, do
       not do any of steps 1-9 for this bucket.**
     - `old-cves` is a **strict subset** of this bucket's current CVE IDs
       (every old CVE is still present, plus at least one new one added
       since that PR was opened) → this is a **supersede**: continue
       through steps 1-8 for the full current CVE set, then pass
       `SUPERSEDES=<pr-number>` to `open_pr.sh` in step 8 so it comments on
       the old PR referencing the new one and closes it.
     - Anything else (the sets differ without the old set nesting inside
       the new one - e.g. some old CVEs no longer appear in this scan) →
       ambiguous; don't auto-close another PR on a guess. Continue through
       steps 1-8 as an ordinary new PR for the current set, and add a line
       to its body noting the older open PR by number, so a human can
       reconcile the two - don't pass `SUPERSEDES` in this case.
   - **Exit 2**: a failure Issue was already filed by the script for this
     target - stop, don't retry.

1. Find where the version is actually pinned in the repo (e.g. a
   Containerfile `ARG`, a `go.mod`, a `requirements.txt` - search, don't
   guess). If the pin already meets or exceeds the fix version, it's
   already remediated - skip it, don't open a duplicate PR.
2. Verify the fix version against real upstream evidence (release notes,
   changelog, the dependency's own `go.mod`) - cite it in the PR body.
3. **Never downgrade.** If the only available fix is older than the current
   pin, don't apply it.
4. If nothing fixes it yet (e.g. a transitive dep of a third-party tool with
   no new release), don't force a change - note it as an unfixable
   follow-up and move on.
5. Run `make test` (repo root) - must pass before you touch anything.
6. Apply the single bump. Run `make test` again - must still pass.
7. Check coverage, but only if the bumped dependency is actually called by
   this repo's own code (not just an opaque pinned CLI-tool binary the
   build downloads and never calls into): does an existing test exercise
   that call site and its immediate caller? If there's a real gap, write a
   test against **this repo's own code** - never against the third-party
   library's internals. Say explicitly when this step is a no-op.
8. Commit, push, then open the fix-only PR. Always pass `IMAGE`, `PACKAGE`
   and `CVES` (the current bucket's comma-separated CVE IDs) so the PR
   carries the marker `find_existing_pr.sh` looks for on future runs; add
   `SUPERSEDES=<pr-number>` only in the supersede case from step 0:

   ```shell
   git push origin <branch>
   FORK_OWNER=<your-account> UPSTREAM=<owner/repo> BRANCH=<branch> \
     TITLE="<title naming the CVE(s)>" BODY="<summary + evidence>" \
     IMAGE=<image-ref> PACKAGE=<package> CVES="<CVE-1,CVE-2>" \
     [SUPERSEDES=<old-pr-number>] \
     bash /sandbox/.claude/skills/job-image-vuln-check/scripts/open_pr.sh
   ```

9. If (and only if) step 7 added tests, open a **second, separate** PR
   (same script, new branch) containing only those tests, referencing the
   fix PR's number. Never combine a fix and new tests in one PR/commit.

Repeat per fixable CVE bucket. One fix per PR - never batch.

## Guardrails

- Never downgrade. Never batch multiple fixes in one PR. Never force-push.
- Never open a duplicate PR for a bucket an open PR already covers with the
  identical CVE set (step 6's step 0) - and never close an existing PR
  unless its CVE set is a confirmed strict subset of the new one (a real
  supersede, not a guess).
- Never close a PR via step 4 on a partial CVE match - only when none of its
  CVEs appear in the current scan at all.
- Never open a `dnf -y update <pkg>` forward-pin PR for an RPM/dnf package
  (step 5) - it names no version, so it isn't a real pin, and it almost
  never beats the base image's own rebuild. This holds for **every**
  RPM-sourced package, whether it came in via the base image or an explicit
  `dnf install` in this Containerfile - the fix is always a base-image bump.
  Only non-RPM ecosystems (Containerfile `ARG`, `go install @version`,
  `requirements.txt`, npm, etc.) are legitimate remediation targets.
- Never escalate a base-image-pending RPM CVE as a new PR or a one-off
  run-summary note - update the single persistent tracking issue for that
  repo+image instead (step 5).
- Verify version/evidence claims against something real - never invent one.
- Never print, commit, or put a literal credential anywhere - only the
  credential placeholders (openshell resolve-env) / `${GITHUB_TOKEN}`-style references already
  used throughout this repo.
- No `ROSA-Agent` label on these PRs (unlike `job-sop-improve`'s target).

## Failures

Any policy-denied (HTTP 403) response, or any other step that stops the
run cleanly, gets reported via `file_failure_issue` (sourced from
`scripts/common.sh`) against `openshift-online/rosa-agent` - never retried,
never routed around. `sync_fork.sh` and `open_pr.sh` already call it
themselves on their own failures; call it directly for anything else:

```shell
source /sandbox/.claude/skills/job-image-vuln-check/scripts/common.sh
file_failure_issue "<stage>" "<exact captured error>"
```
