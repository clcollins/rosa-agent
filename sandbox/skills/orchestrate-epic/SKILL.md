---
name: orchestrate-epic
description: Orchestrate parallel implementation of a Jira epic's stories by launching, monitoring, and coordinating multiple HyperShell sandbox agents — each running implement-ticket independently — then managing review fixups, merge conflict coordination, and Jira housekeeping. Use when asked to implement an entire epic, orchestrate agents, run stories in parallel via sandboxes, or act as master control for multi-agent work. Trigger keywords - orchestrate epic, implement epic, parallel sandboxes, master control, multi-agent, launch sandboxes, sandbox orchestration, agent coordination, implement all stories.
---

# Orchestrate epic: parallel sandbox agents with review/merge coordination

**This skill runs on a laptop (or any host with `openshellctl`), NOT
inside a sandbox.** It requires interactive vault OIDC login, Claude
Code's `run_in_background`, a human for review feedback, and egress to
the OpenShell gateway. Child sandboxes cannot create nested sandboxes.

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
- `sandbox-create-args --name N --template T --prompt P --run-id R --epic E --ticket K --type T [--model M] [--environment E] [--label K=V ...]` — build args with cost-tracking labels
- `run-id --epic E` — generate a unique run ID for this orchestration run
- `detect-stuck --type nit|fixup|rebase|implementation --elapsed-minutes N` — stuck check
- `epic-comment --repo owner/repo [--jira-base-url URL]` — pipe merged stories JSON, get wiki comment
- `assignee-payload --account-id ID` — Jira assignee payload
- `remote-link-payload --url U --title T` — Jira remote link payload
- `transition-payload --transition-id ID` — Jira transition payload
- `sandbox-status [--list]` — parse sandbox JSON from stdin, emit structured status

## Prerequisites

This skill runs on a **laptop** (not inside a sandbox):

1. `openshellctl` on PATH
2. Vault OIDC session active — tokens expire in **minutes**;
   refresh with `vault login -method=oidc -non-interactive -no-print`.
   All `openshellctl` calls must go through the `_osctl` wrapper (defined
   in Phase 1) which sets the vault env vars.
3. A local checkout of this repo (for the template file)
4. Jira API access (via the `jira` skill's curl helpers, or shim-mcp)
5. `gh` CLI for PR monitoring
6. The epic key that defines the scope

## Labels

Every child sandbox MUST carry cost-tracking labels. The `_run_sandbox`
wrapper (Phase 1) applies them automatically via the `sandbox-create-args`
helper. Never bypass this.

| Key | Value | Source |
| --- | --- | --- |
| `project` | `rosa-agent` | Fixed |
| `workload` | `orchestrate-epic` | Fixed |
| `owner` | `sd-sre-platform` | Fixed |
| `environment` | `test` (default) | Per run (`--environment`) |
| `run-id` | `<epic>-<YYYYmmddHHMM>` | Generated once in Phase 0 |
| `epic` | `<epic key, lowercase>` | Per run |
| `ticket` | `<ticket key, lowercase>` | Per child |
| `sandbox-type` | `implementation\|fixup\|nit\|rebase` | Per child |

Additional labels can be passed via `--label key=value` but cannot
override the base or per-child keys.

## Phase 0 — Discovery

1. **Generate run ID.** Create once and reuse for every child sandbox in this run:

   ```shell
   RUN_ID="$(python3 -m orchestrate_epic run-id --epic <EPIC>)"
   ```

2. **Fetch epic children.** Use the Jira v3 search endpoint (v2 is removed):

   ```shell
   jira "$JB/rest/api/3/search/jql?jql=parent+%3D+<EPIC>&fields=summary,status,issuetype"
   ```

   The v3 search endpoint paginates — repeat with `nextPageToken` until
   all children are fetched:

   ```shell
   CHILDREN="[]"
   TOKEN=""
   while true; do
     RESULT=$(jira "$JB/rest/api/3/search/jql?jql=parent+%3D+$EPIC&fields=summary,status,issuetype${TOKEN:+&nextPageToken=$TOKEN}")
     CHILDREN=$(echo "$CHILDREN" "$RESULT" | jq -s '.[0] + .[1].issues')
     TOKEN=$(echo "$RESULT" | jq -r '.nextPageToken // empty')
     [[ -n "$TOKEN" ]] || break
   done
   echo "{\"issues\": $CHILDREN}" | python3 -m orchestrate_epic filter-actionable
   ```

   Pipe the result into `python3 -m orchestrate_epic filter-actionable` to
   get the actionable stories (filters out Closed and Spike cards).

3. **Discover the active sprint** (for adding cards later):

   ```shell
   jira "$JB/rest/agile/1.0/board?projectKeyOrId=<PROJECT>&type=scrum"
   # note the board ID, then:
   jira "$JB/rest/agile/1.0/board/<boardId>/sprint?state=active"
   ```

4. **Discover the Sprint custom field ID** (needed for adding cards to sprint):

   ```shell
   jira "$JB/rest/api/3/field" | jq '.[] | select(.name == "Sprint") | .id'
   ```

   Store the result (e.g. `customfield_10020`) as `SPRINT_FIELD_ID`.

5. **Discover transition IDs** if not already known:

   ```shell
   jira "$JB/rest/api/3/issue/<ANY-KEY>/transitions"
   ```

6. **Validate scope.** Each card should have enough detail for autonomous
   implementation. If a card is too sparse, flag it for human enrichment —
   do NOT launch a sandbox for it.

## Phase 1 — Parallel implementation

Define the vault wrapper and session variables before launching any sandboxes:

```shell
_osctl() {
  OPENSHELL_VAULT_KV_MOUNT=osd-sre OPENSHELL_VAULT_KV_PATH=rosa-agent \
  OPENSHELL_VAULT_FIELD_PREFIX=hypershell "$@"
}
```

Set session variables:

```shell
# TEMPLATE must point to noninteractive-keep.yaml in YOUR local checkout.
# openshellctl reads this file locally and uploads it when creating the sandbox.
TEMPLATE="$(git rev-parse --show-toplevel)/sandbox/templates/noninteractive-keep.yaml"
MODEL="claude-sonnet-4-6"
EPIC="<EPIC-KEY>"  # e.g. ROSAENG-12345
```

Define the `_run_sandbox` wrapper once per session. All sandbox creation
MUST go through this function to ensure cost-tracking labels are applied:

```shell
_run_sandbox() {  # usage: _run_sandbox <KEY> <type> <prompt> [name-suffix]
  local key=$1 type=$2 prompt=$3 suffix=${4:-}
  local name
  name=$(python3 -m orchestrate_epic sandbox-name "$key" ${suffix:+--suffix "$suffix"}) || return 1
  local json
  json=$(python3 -m orchestrate_epic sandbox-create-args \
    --name "$name" --template "$TEMPLATE" --prompt "$prompt" --model "$MODEL" \
    --run-id "$RUN_ID" --epic "$EPIC" --ticket "$key" --type "$type") || return 1
  local -a args
  mapfile -d '' args < <(printf '%s' "$json" | jq -j '.[] + "\u0000"')
  [[ ${#args[@]} -gt 0 ]] || { echo "error: sandbox-create-args produced no arguments" >&2; return 1; }
  _osctl openshellctl "${args[@]}"
}
```

For each actionable story, launch a sandbox:

```shell
_run_sandbox <KEY> implementation "/implement-ticket <KEY>"
```

### Critical rules

- **Never call `openshellctl sandbox create` directly — always `_run_sandbox`,
  so cost-tracking labels are applied.**
- **Use `-f` and `-p`**, not `--template` or `--set`.
- **`--detach` blocks** until Ready phase. Run via `run_in_background`.
- **Use `noninteractive-keep.yaml`** (`noKeep: false`) so completed sandboxes
  persist for failure inspection. The standard `noninteractive.yaml` has
  `noKeep: true` which auto-deletes on completion.
- **Max sandbox name: 19 characters.** The helper validates this.
  Use short suffixes (`fx`, `nt`, `rb`) to leave room for longer ticket keys.
- **`--replace` kills by name, not run-id.** Before relaunching a sandbox,
  check if the name is already in use by another run:

  ```shell
  _osctl openshellctl sandbox get <name> --output json 2>/dev/null \
    | jq -r '.metadata.labels["run-id"] // "none"'
  ```

  Only proceed with `--replace` if the existing sandbox's `run-id` matches
  `$RUN_ID` or the sandbox doesn't exist. If it belongs to another run,
  stop and ask the human.
- **Vault tokens expire in minutes.** Refresh proactively:
  `vault login -method=oidc -non-interactive -no-print`.
  If SSO is also expired, notify the human. All `openshellctl` calls go
  through `_osctl` which sets the vault env vars.

### Monitoring obligation

You MUST actively poll all running sandboxes — do NOT wait for the human
to ask "status?":

- Poll only this run's sandboxes using the label selector:

  ```shell
  _osctl openshellctl sandbox list --selector "run-id=$RUN_ID" --output json
  ```

- Use `_osctl openshellctl sandbox get <name> --output json` for structured status
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
2. Launch a fixup sandbox via `_run_sandbox` with the matching type:

   ```shell
   _run_sandbox <KEY> fixup "<fixup prompt with PR#, branch, file:line refs>" fx
   _run_sandbox <KEY> nit "<nit prompt>" nt
   ```

   The prompt MUST include:
   - Exact PR number and branch name
   - Each fix with file:line references
   - "Push to existing branch, do NOT create a new PR"
   - "Commit and push EACH fix individually" (prevents total loss)
   - "If tests fail after 3 minutes, commit the code changes anyway"
3. Include ALL nits — every code-touching nit must be addressed
4. Design questions with concrete fixes: include them, don't defer

## Phase 3 — Merge conflict coordination

1. **Merge conflict-free PRs first** — conflicts compound with each merge
2. After each merge, check remaining PRs:

   ```shell
   gh pr view <N> --repo <owner/repo> --json mergeable,mergeStateStatus
   ```

   Values: `MERGEABLE|CONFLICTING`, `CLEAN|DIRTY|UNSTABLE|BLOCKED`
3. For conflicting PRs, combine rebase + any pending fixups into ONE sandbox
   via `_run_sandbox`:

   ```shell
   _run_sandbox <KEY> rebase "<rebase + fixup prompt>" rb
   ```

4. **Draft PR gotcha**: `gh pr merge` fails on drafts. Run
   `gh pr ready <N> --repo <owner/repo>` first.

## Phase 4 — Jira housekeeping

After each PR merges:

1. Add card to sprint. Sprint is a **custom field** — discover the field
   ID once per project:

   ```shell
   # Find the Sprint field ID (run once):
   jira "$JB/rest/api/3/field" | jq '.[] | select(.name == "Sprint") | .id'
   # Returns e.g. "customfield_10020"
   ```

   Then update the issue:

   ```shell
   jira -H "Content-Type: application/json" -X PUT "$JB/rest/api/3/issue/<KEY>" \
     -d '{"fields": {"<SPRINT_FIELD_ID>": <sprintId>}}'
   ```

   If this fails, ask to add `POST /rest/agile/1.0/sprint/*/issue` to the
   Jira provider profile — that endpoint is purpose-built for sprint moves.

2. Update assignee:

   ```shell
   python3 -m orchestrate_epic assignee-payload --account-id <ID> | jira -H "Content-Type: application/json" -X PUT "$JB/rest/api/3/issue/<KEY>" -d @-
   ```

3. Add PR link:

   ```shell
   python3 -m orchestrate_epic remote-link-payload --url <PR_URL> --title "PR #N: title" | jira -H "Content-Type: application/json" -X POST "$JB/rest/api/3/issue/<KEY>/remotelink" -d @-
   ```

4. Transition to Closed:

   ```shell
   python3 -m orchestrate_epic transition-payload --transition-id <ID> | jira -H "Content-Type: application/json" -X POST "$JB/rest/api/3/issue/<KEY>/transitions" -d @-
   ```

After all stories merge, add an epic completion comment and close the epic:

```shell
echo '<merged-stories-json>' | python3 -m orchestrate_epic epic-comment --repo <owner/repo> \
  | jira -H "Content-Type: application/json" -X POST "$JB/rest/api/3/issue/<EPIC>/comment" -d @-
```

The comment is emitted as an ADF (Atlassian Document Format) JSON payload
wrapped in `{"body": ...}`, ready for the v3 comment endpoint.

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

1. List only this run's sandboxes:

   ```shell
   _osctl openshellctl sandbox list --selector "run-id=$RUN_ID"
   ```

2. Delete completed sandboxes — only those you launched (filtered by run-id)
3. Never delete sandboxes with unfamiliar names
4. `_osctl openshellctl sandbox delete <name>`

## Stuck sandbox rules

Run `python3 -m orchestrate_epic detect-stuck --type <type> --elapsed-minutes <N>`:

- `ok` — sandbox running within normal time
- `investigate` — use `sandbox exec` to diagnose:

  ```shell
  _osctl openshellctl sandbox exec --name <name> -- ps aux
  _osctl openshellctl sandbox exec --name <name> -- git -C /sandbox/<repo> status
  _osctl openshellctl sandbox exec --name <name> -- git -C /sandbox/<repo> diff --stat
  ```

  The `--name` flag is REQUIRED — without it, exec hits the "last-used" sandbox.
  `exec` only works on Ready-phase sandboxes — Completed ones reject it.
- `kill` — delete and relaunch with a hardened prompt

### Thresholds

| Type | Normal | Investigate | Kill |
| --- | --- | --- | --- |
| nit | 5-10 min | 15 min | 20 min |
| fixup | 10-15 min | 20 min | 30 min |
| rebase | 10-15 min | 20 min | 30 min |
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

## Template: `${...}` substitution

openshellctl scans string values in the manifest YAML for `${KEY}`
patterns and substitutes them with `-p KEY=VALUE` parameters. This
only applies to the manifest file — values passed via `-p` are NOT
re-scanned. Use `$${VAR}` to escape a literal `${VAR}` in the manifest
if needed (the `$$` prefix prevents substitution).

## stdout capture

openshellctl does NOT relay container stdout. To capture output from a
sandbox:

1. Write to a file under `/sandbox/` (NOT `/tmp/` — download restricted)
2. `_osctl openshellctl sandbox download <name> /sandbox/<file> <local-path>`
