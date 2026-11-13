---
name: orchestrate-epic
description: Orchestrate parallel implementation of a Jira epic's stories by launching, monitoring, and coordinating multiple HyperShell sandbox agents — each running implement-ticket independently — then managing review fixups, merge conflict coordination, and Jira housekeeping. Use when asked to implement an entire epic, orchestrate agents, run stories in parallel via sandboxes, or act as master control for multi-agent work. Trigger keywords - orchestrate epic, implement epic, parallel sandboxes, master control, multi-agent, launch sandboxes, sandbox orchestration, agent coordination, implement all stories.
---

# Orchestrate epic: parallel sandbox agents with review/merge coordination

You are the orchestrator ("Master Control"). You launch one sandbox agent
per actionable story in the epic, monitor them, coordinate review fixups,
manage merge conflicts, update Jira, and clean up.

## Python helpers

This skill includes a Python CLI at `orchestrate_epic/` with deterministic
helpers. Run them from this skill's directory:

```shell
python3 -m orchestrate_epic <subcommand> [args]
```

Subcommands:

- `filter-actionable` — pipe a Jira search response, get actionable stories
- `sandbox-name <KEY> [--suffix fixup]` — derive a valid sandbox name
- `sandbox-create-args --name N --template T --prompt P [--model M]` — build args
- `detect-stuck --type nit|fixup|implementation --elapsed-minutes N` — stuck check
- `epic-comment --repo owner/repo` — pipe merged stories JSON, get wiki comment
- `assignee-payload --account-id ID` — Jira assignee payload
- `remote-link-payload --url U --title T` — Jira remote link payload
- `transition-payload --transition-id ID` — Jira transition payload
- `sprint-payload ISSUE [ISSUE ...]` — Jira sprint-add payload

## Prerequisites

1. `openshellctl` on PATH
2. Vault OIDC session active — tokens expire in **minutes**
3. Vault env vars (must be set on EVERY openshellctl call — shell state does
   not persist between tool calls):

   ```shell
   export OPENSHELL_VAULT_KV_MOUNT=osd-sre OPENSHELL_VAULT_KV_PATH=rosa-agent OPENSHELL_VAULT_FIELD_PREFIX=hypershell
   ```

4. Jira API access (via the `jira` skill's curl helpers, or shim-mcp)
5. `gh` CLI for PR monitoring
6. The epic key that defines the scope

## Phase 0 — Discovery

1. **Fetch epic children.** Use the Jira v3 search endpoint (v2 is removed):

   ```shell
   jira "$JB/rest/api/3/search/jql?jql=%22Epic+Link%22+%3D+<EPIC>&fields=summary,status,issuetype"
   ```

   Pipe the result into `python3 -m orchestrate_epic filter-actionable` to
   get the actionable stories (filters out Closed and Spike cards).

2. **Discover the active sprint** (for adding cards later):

   ```shell
   jira "$JB/rest/agile/1.0/board?projectKeyOrId=<PROJECT>&type=scrum"
   # note the board ID, then:
   jira "$JB/rest/agile/1.0/board/<boardId>/sprint?state=active"
   ```

3. **Discover transition IDs** if not already known:

   ```shell
   jira "$JB/rest/api/2/issue/<ANY-KEY>/transitions"
   ```

4. **Validate scope.** Each card should have enough detail for autonomous
   implementation. If a card is too sparse, flag it for human enrichment —
   do NOT launch a sandbox for it.

## Phase 1 — Parallel implementation

For each actionable story, launch a sandbox:

```shell
export OPENSHELL_VAULT_KV_MOUNT=osd-sre OPENSHELL_VAULT_KV_PATH=rosa-agent OPENSHELL_VAULT_FIELD_PREFIX=hypershell
openshellctl sandbox create \
  --name "$(python3 -m orchestrate_epic sandbox-name <KEY>)" \
  -f <keep-alive-template> \
  -p "NAME=$(python3 -m orchestrate_epic sandbox-name <KEY>)" \
  -p "PROMPT=/implement-ticket <KEY>" \
  --env "ANTHROPIC_MODEL=claude-opus-4-6" \
  --detach --replace
```

### Critical rules

- **Use `-f` and `-p`**, not `--template` or `--set`.
- **`--detach` blocks** until Ready phase. Run via `run_in_background`.
- **Use the keep-alive template** (no `noKeep: true`) so completed sandboxes
  persist for failure inspection. The upstream `noninteractive.yaml` has
  `noKeep: true` which auto-deletes — use the keep-alive variant instead.
- **Max sandbox name: 19 characters.** The helper validates this.
- **Vault tokens expire in minutes.** Refresh proactively before every
  openshellctl call: `vault login -method=oidc -non-interactive -no-print`.
  If SSO is also expired, notify the human.

### Monitoring obligation

You MUST actively poll all running sandboxes — do NOT wait for the human
to ask "status?":

- Poll `openshellctl sandbox list` every 2-5 minutes
- Use `openshellctl sandbox get <name> --output json` for structured status
- When a sandbox completes, immediately check for new PRs
- Track ALL sandboxes concurrently, not just the most recent
- Proactively report completions and issues

### Transient listing

Sandboxes can temporarily vanish from `sandbox list` without completing.
If a sandbox disappears but no PR was created and `sandbox get` fails,
wait 2-3 minutes and recheck before assuming failure.

## Phase 2 — Review fixups

When the human provides PR review feedback:

1. Parse the feedback into blocking / should-fix / nit categories
2. Launch a fixup sandbox with a **specific prompt** that includes:
   - Exact PR number and branch name
   - Each fix with file:line references
   - "Push to existing branch, do NOT create a new PR"
   - "Commit and push EACH fix individually" (prevents total loss)
   - "If tests fail after 3 minutes, commit the code changes anyway"
3. Include ALL nits — every code-touching nit must be addressed
4. Design questions with concrete fixes: include them, don't defer

Use `sandbox-name <KEY> --suffix fixup` (or `nits`, `rebase`) for the name.

## Phase 3 — Merge conflict coordination

1. **Merge conflict-free PRs first** — conflicts compound with each merge
2. After each merge, check remaining PRs:

   ```shell
   gh pr view <N> --repo <owner/repo> --json mergeable,mergeStateStatus
   ```

   Values: `MERGEABLE|CONFLICTING`, `CLEAN|DIRTY|UNSTABLE|BLOCKED`
3. For conflicting PRs, combine rebase + any pending fixups into ONE sandbox
4. **Draft PR gotcha**: `gh pr merge` fails on drafts. Run
   `gh pr ready <N> --repo <owner/repo>` first.

## Phase 4 — Jira housekeeping

After each PR merges:

1. Add card to sprint:

   ```shell
   python3 -m orchestrate_epic sprint-payload <KEY> | jira -H "Content-Type: application/json" -X POST "$JB/rest/agile/1.0/sprint/<sprintId>/issue" -d @-
   ```

2. Update assignee:

   ```shell
   python3 -m orchestrate_epic assignee-payload --account-id <ID> | jira -H "Content-Type: application/json" -X PUT "$JB/rest/api/2/issue/<KEY>" -d @-
   ```

3. Add PR link:

   ```shell
   python3 -m orchestrate_epic remote-link-payload --url <PR_URL> --title "PR #N: title" | jira -H "Content-Type: application/json" -X POST "$JB/rest/api/2/issue/<KEY>/remotelink" -d @-
   ```

4. Transition to Closed:

   ```shell
   python3 -m orchestrate_epic transition-payload --transition-id <ID> | jira -H "Content-Type: application/json" -X POST "$JB/rest/api/2/issue/<KEY>/transitions" -d @-
   ```

After all stories merge, add an epic completion comment and close the epic:

```shell
echo '<merged-stories-json>' | python3 -m orchestrate_epic epic-comment --repo <owner/repo>
```

The comment uses Jira wiki markup (h2., ||table||, [text|url], (/)).

### Known Jira limitations

- **Epic Link**: use the v3 `parent` field, NOT the legacy `customfield_12311140`.
  The old Epic Link field is dead in Atlassian Cloud — PUT with it returns
  "Field cannot be set. It is not on the appropriate screen."
  Instead: `PUT /rest/api/3/issue/<KEY>` with `{"fields": {"parent": {"key": "<EPIC>"}}}`.
  Works on both CREATE and UPDATE. Always use `/rest/api/3/`, not `/rest/api/2/`.
- Contributor field (`customfield_12313240`) may not be on the edit screen —
  the PUT will error. This is a Jira screen configuration issue, not a bug.
- Transition IDs vary per project/workflow — always discover them, never
  hardcode across projects.
- Use v3 search API (`/rest/api/3/search/jql`), not v2 (returns 410).

## Phase 5 — Cleanup

1. Delete completed sandboxes — only those you launched (check your log)
2. Never delete sandboxes with unfamiliar names
3. `openshellctl sandbox delete <name>` (with vault env vars)

## Stuck sandbox rules

Run `python3 -m orchestrate_epic detect-stuck --type <type> --elapsed-minutes <N>`:

- `ok` — sandbox running within normal time
- `investigate` — use `sandbox exec` to diagnose:

  ```shell
  openshellctl sandbox exec --name <name> -- ps aux
  openshellctl sandbox exec --name <name> -- git -C /sandbox/<repo> status
  openshellctl sandbox exec --name <name> -- git -C /sandbox/<repo> diff --stat
  ```

  The `--name` flag is REQUIRED — without it, exec hits the "last-used" sandbox.
  `exec` only works on Ready-phase sandboxes — Completed ones reject it.
- `kill` — delete and relaunch with a hardened prompt

### Thresholds

| Type | Normal | Investigate | Kill |
| --- | --- | --- | --- |
| nit | 5-10 min | 15 min | 20 min |
| fixup | 10-15 min | 20 min | 30 min |
| implementation | 15-45 min | 60 min | 90 min |

### Hardened prompt rules (prevent stuck sandboxes)

Always include in fixup/rebase prompts:

- "Commit and push EACH fix individually rather than batching"
- "If tests fail after 3 minutes, commit the code changes anyway"

These two rules reduced a 50+ min stuck sandbox to 4 min in the trial.

## CI system detection

Different repos use different CI. Detect before advising on merge:

- **Prow** (OpenShift GitHub repos): `/ok-to-test`, `/lgtm`, `/approve`
- **GitHub Actions** (newer repos): auto-triggers for org PRs; external fork
  PRs may need manual workflow approval
- Do NOT use Prow commands on GH Actions repos — they do nothing

## Template: `${...}` conflicts

openshellctl scans the ENTIRE manifest YAML for `${...}` patterns —
including command text. Bash variable references like `${VAR}` will be
treated as unresolved template parameters. Use `env | grep` or `$VAR`
(without braces) instead.

## stdout capture

openshellctl does NOT relay container stdout. To capture output from a
sandbox:

1. Write to a file under `/sandbox/` (NOT `/tmp/` — download restricted)
2. `openshellctl sandbox download <name> /sandbox/<file> <local-path>`
