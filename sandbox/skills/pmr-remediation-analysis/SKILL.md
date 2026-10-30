---
name: pmr-remediation-analysis
description: Analyze a PMR action brief and propose a specific remediation tied to the incident's failure mechanism. Favors code/architectural changes over workarounds. Can explicitly decline if no sound proposal is possible. Runs in two modes - (1) discovery/triage mode: scans Jira for all pmr-ai tickets assigned to rosa-agent, checks readiness, and comments on incomplete tickets mentioning the reporter; (2) per-ticket analysis mode: given a specific Jira key, analyzes the brief and emits a structured proposal or explicit decline. Use for PMR pipeline Stage 2 (proposal generation). Triggers on "analyze PMR brief", "propose PMR remediation", "PMR action analysis", "scan pmr-ai tickets", "triage PMR tickets".
---

# PMR Remediation Analysis

**PMR Pipeline Stage 2**: Analyze a Post-Mortem Review action item brief and propose a concrete, code-first remediation.

## Context

This skill is part of the PMR action pipeline (MCS-46):

1. **Intake** (MCS-214) → Assembles action + incident evidence into brief
2. **Proposal** (MCS-216 - **THIS SKILL**) → Analyzes brief, proposes remediation
3. **Card Creation** → Proposal becomes Jira card  
4. **Grooming Gate** → Human approves/denies
5. **Assignment** → Assign to rosa-agent
6. **Implementation** → rosa-agent implements (see `pmr-action-item` skill)
7. **Review Gate** → PR review before merge

**This skill's job:** Given a complete brief, output either:
- A structured remediation proposal (failure mechanism + proposed change + verification), OR
- An explicit decline with specific reason why no sound proposal can be made

**Remediation Preference (from MCS-46):** Favor code or architectural changes. Workarounds require explicit, evidence-based justification.

## Input: PMR Action Brief

**Expected from MCS-214 intake pipeline** (schema in progress):

```yaml
brief:
  action:
    jira_key: ROSAENG-XXXXX
    summary: "[One-line action description]"
    description: "[Detailed action from Jira]"
    
  incident:
    itn_reference: ITN-YYYY-NNNNN
    webrca_id: "[WebRCA incident ID]"
    impact_start: "[ISO 8601 timestamp]"
    impact_end: "[ISO 8601 timestamp]"
    affected_clusters: ["cluster-id-1", ...]
    customer_facing: true/false
    severity: P1/P2/P3/P4
    
  evidence:
    timeline:
      - timestamp: "[ISO 8601]"
        event: "[What happened]"
        source: "[Slack/PagerDuty/Logs]"
    logs: "[Log excerpts or links]"
    alerts: "[Firing alerts]"
    
  affected_component:
    repository: "[GitHub owner/repo]"
    component: "[Service/controller name]"
```

**Until MCS-214 is complete:** Accept flexible input (Jira key, text paste, doc link) and adapt.

## Skill Modes

This skill has two modes of operation:

- **Discovery/Triage Mode** (no specific ticket key given, or asked to scan/triage):
  Query Jira for all `pmr-ai` tickets assigned to rosa-agent, check readiness, and comment on
  incomplete tickets mentioning the reporter. See "Ticket Discovery and Triage" section.

- **Per-Ticket Analysis Mode** (given a specific Jira issue key):
  Analyze that ticket's brief and emit a structured proposal or explicit decline.
  See "Workflow" section.

---

## Ticket Discovery and Triage

Run this mode when invoked without a specific issue key, or when asked to "scan", "check", or
"triage" PMR-AI tickets. This is the entry point for automated/cron-driven execution.

Per `AGENTS.md`, all Jira calls MUST go through the `jira` skill — load it and run auth detection
once at the start of this mode.

### Step D1: Get Agent's Own AccountId

```shell
# Needed to filter by assignee in the JQL query
MYSELF=$(jira -H "Accept: application/json" "$JB/rest/api/3/myself")
AGENT_ACCOUNT_ID=$(echo "$MYSELF" | python3 -I -c "import sys,json; print(json.load(sys.stdin)['accountId'])")
```

### Step D2: Query for Unprocessed pmr-ai Tickets

Find tickets that:
- Have the `pmr-ai` label
- Are assigned to rosa-agent (this agent)
- Do NOT yet have the `pmr-ai-ready` label (not yet through analysis)
- Are not closed/done

```shell
jira -H "Content-Type: application/json" \
  -X POST "$JB/rest/api/3/search/jql" \
  -d "{
    \"jql\": \"labels = \\\"pmr-ai\\\" AND assignee = \\\"${AGENT_ACCOUNT_ID}\\\" AND labels NOT IN (\\\"pmr-ai-ready\\\") AND statusCategory != Done ORDER BY created ASC\",
    \"fields\": [\"summary\",\"description\",\"labels\",\"assignee\",\"reporter\",\"status\",\"comment\"],
    \"maxResults\": 50
  }"
```

### Step D3: For Each Ticket, Check Readiness

A ticket is **ready for analysis** when its description contains all four of:

1. **Action description** — what specifically needs to be fixed and why
2. **Incident evidence** — timeline of events, impact scope, symptoms observed
3. **Affected component/repository** — the GitHub owner/repo and service/controller name
4. **Failure context** — what went wrong and when

A description is "non-trivial" if it contains at least 100 characters AND includes recognizable
content for each required element. An empty description, a one-line placeholder, or a description
that only has one of the four required elements is **not ready**.

### Step D4: Skip if Already Commented

Before posting a comment, check the ticket's existing comments to avoid duplicates:

- Fetch `GET $JB/rest/api/3/issue/<KEY>/comment`
- If there is already a comment whose body contains the phrase
  "PMR remediation analysis skill reviewed this ticket" AND that comment was posted within
  the last 7 days, skip this ticket — the reporter has already been notified.
- If the ticket's description has since been updated (check `updated` vs comment `created`),
  treat the ticket as needing a fresh check regardless.

### Step D5: Comment on Unready Tickets, Mention Reporter

For each ticket that fails the readiness check (and hasn't been recently notified):

**1. Get the reporter's accountId and displayName** from the ticket's `reporter` field.

**2. Build the list of missing elements** from the Step D3 check.

**3. Post a comment using ADF with a proper `@mention` of the reporter.**

The ADF mention node requires the reporter's `accountId` (from `reporter.accountId` on the
issue). This is what produces a real Jira notification to the reporter — a bare `@name` text
node does not notify anyone.

```shell
# Build the ADF comment body. Fill in:
#   REPORTER_ACCOUNT_ID  — from issue.fields.reporter.accountId
#   REPORTER_DISPLAY_NAME — from issue.fields.reporter.displayName
#   MISSING_ITEMS_JSON   — a JSON array of ADF listItem nodes, one per missing element

jira -H "Content-Type: application/json" \
  -X POST "$JB/rest/api/3/issue/<KEY>/comment" \
  -d '{
    "body": {
      "type": "doc",
      "version": 1,
      "content": [
        {
          "type": "paragraph",
          "content": [
            {
              "type": "mention",
              "attrs": {
                "id": "<REPORTER_ACCOUNT_ID>",
                "text": "@<REPORTER_DISPLAY_NAME>"
              }
            },
            {
              "type": "text",
              "text": " — the PMR remediation analysis skill reviewed this ticket but cannot produce a proposal yet because the brief is incomplete."
            }
          ]
        },
        {
          "type": "paragraph",
          "content": [
            {"type": "text", "text": "Missing required elements:"}
          ]
        },
        {
          "type": "bulletList",
          "content": [
            <MISSING_ITEMS_JSON>
          ]
        },
        {
          "type": "paragraph",
          "content": [
            {
              "type": "text",
              "text": "Once the brief is complete, re-assign to rosa-agent and the skill will retry."
            }
          ]
        }
      ]
    }
  }'
```

**ADF listItem node for each missing element:**
```json
{
  "type": "listItem",
  "content": [
    {
      "type": "paragraph",
      "content": [
        {"type": "text", "text": "<element name>: <description of what's needed>"}
      ]
    }
  ]
}
```

**Missing element descriptions** (use only the ones that are actually missing):
- `"Action description: what specifically needs to be fixed, and why"`
- `"Incident evidence: timeline of events, impact scope, symptoms observed"`
- `"Affected component/repository: the GitHub owner/repo and service/controller name"`
- `"Failure context: what went wrong and when"`

### Step D6: Process Ready Tickets

For each ticket that IS ready for analysis, proceed with the per-ticket **Workflow** below
(starting at Step 1). Each ticket is a separate analysis run; do not mix context across tickets.

If the discovery run finds more than 10 ready tickets, process them in batches of 5 and note
remaining count in your final summary output.

---

## Workflow

### Step 1: Load and Validate Brief

**Accept brief via:**
- Direct text paste of brief content
- Jira issue key (fetch using `jira` skill per AGENTS.md)
- File path to brief document
- Google Doc URL (if MCP available)

**Validate completeness:**
- ✅ Action description (what needs fixing)
- ✅ Incident evidence (timeline, impact, symptoms)
- ✅ Affected component/repository
- ✅ Failure context (what went wrong, when)

**If incomplete:**
```markdown
⚠️ Brief Incomplete - Cannot Proceed

Missing required elements:
- [List what's missing]

Required for sound proposal:
- Action description
- Incident evidence (timeline, impact, symptoms)
- Affected component/repository
- Failure context

Recommendation: Return to intake (MCS-214) to complete brief.
```
**STOP HERE** if incomplete - do not guess.

### Step 2: Analyze Failure Mechanism

**Extract failure signals:**
- Error messages, stack traces, log patterns
- Timeline: what changed → what failed → what recovered
- Impact: user symptoms, scope, duration
- Environment: load, timing, configuration

**Identify root cause category:**
- Logic bug (incorrect behavior, unhandled edge case)
- Race condition (timing-dependent)
- Missing validation (unchecked input/state)
- Configuration drift (wrong config)
- Architectural gap (design limitation)
- Dependency issue (external service)
- Resource constraint (capacity, timeouts)
- Observability gap (cannot detect/prevent)

**State failure mechanism** (one clear sentence):
- WHY the incident occurred (not just what failed)
- Connect symptoms to root cause
- Must be falsifiable/verifiable

**Example:**
```
Failure Mechanism:
Cluster upgrade timed out because the control plane health check failed 
to account for StatefulSet rolling-restart delay with OnDelete update 
strategy, causing abort before pods became ready.
```

### Step 3: Propose Change (Code or Architecture First)

**DEFAULT TO CODE/ARCHITECTURAL CHANGES** - workarounds need justification (Step 4).

**1. Identify change location:**
- Repository and file paths
- Function/method/component name
- Config files if architectural
- API contracts if cross-service

**2. Specify change type:**
- Code fix (change logic, add validation, fix race)
- Architectural change (refactor component, change data flow)
- Configuration change (update defaults, add flag, modify limits)
- Observability addition (add metrics, alerts, logging)
- **Workaround** (ONLY if code/arch infeasible - see Step 4)

**3. Describe proposed change:**

```
Proposed Change:

TYPE: [Code fix / Architectural / Configuration / Observability / Workaround]

WHAT:
[Specific modification to make]

WHERE:
[owner/repo/path/to/file.go:function_name]
[List all affected files/components]

HOW:
[Implementation approach at design level - not line-by-line code]
[Key logic changes or architectural decisions]
```

**Example:**
```
TYPE: Code fix + Observability

WHAT:
1. Modify upgrade health check to detect StatefulSet update strategy
2. Calculate timeout based on replica count and restart interval  
3. Add metric tracking StatefulSet ready state during upgrades

WHERE:
- openshift-online/cluster-upgrade-controller/pkg/health/statefulset.go
- openshift-online/cluster-upgrade-controller/pkg/metrics/upgrade.go
- openshift-online/cluster-upgrade-controller/config/timeout.yaml

HOW:
- In StatefulSet health check, inspect .spec.updateStrategy.type
- If OnDelete: timeout = replicas × (restart_interval + startup_time) + margin
- Emit statefulset_rolling_restart_duration_seconds metric
```

### Step 4: Justify Workarounds (If Proposed)

**A workaround is NOT first choice.** If proposing workaround, you MUST provide:

**1. Why code/architecture change is infeasible:**
- Technical blocker (upstream bug, API limitation)
- Timeline constraint with evidence (critical incident, customer commitment)
- Resource constraint (requires N-month refactor, no capacity)

**2. Evidence supporting infeasibility:**
- Link to upstream issue
- Quote from customer escalation/commitment
- Architecture complexity estimate

**3. Explicit tradeoffs:**
- What breaks or degrades
- Technical debt incurred
- Follow-up action item to address properly

**Example:**
```
Workaround Justification:

Proposed: Increase global upgrade timeout 90m → 180m

Why Not Code Fix:
StatefulSet timeout logic requires refactoring entire health check 
framework - 2 sprint effort (team estimate). Blocks EUS upgrade path 
for customer ACME-123 by 2026-11-15 (OHSS-98765 commitment).

Evidence:
- Customer escalation: OHSS-98765  
- Team estimate: 2 sprints (Oct 22 planning)
- Deadline: 2026-11-15 (24 days)

Tradeoffs:
- Slower failure detection for non-StatefulSet issues
- Increases downtime window by up to 90m on failed upgrades
- Technical debt: proper per-resource timeout still needed

Follow-up:
Epic for health check refactor targeting Q1 2027
```

**If workaround lacks justification → DECLINE** (see Decline Path).

### Step 5: Define Verification Approach

Specify how to verify fix prevents recurrence:

**1. Unit tests:**
- Conditions to test
- Expected outcomes
- Edge cases covered

**2. Integration tests:**
- Scenario to reproduce
- How to trigger failure condition
- Success criteria

**3. Observability:**
- Metrics/alerts to watch
- Log messages to verify
- Dashboards to update

**4. Canary criteria** (if applicable):
- What to monitor during rollout
- Rollback triggers
- Verification timeline

**Example:**
```
Verification:

UNIT TESTS:
- Test StatefulSet health check: OnDelete strategy + 3 replicas
- Verify timeout = replicas × (restart + startup) + margin
- Test RollingUpdate uses original 90m (no change)
- Verify metric emission

INTEGRATION TESTS:
- Test cluster with OnDelete StatefulSet
- Trigger upgrade with health check
- Verify completion within calculated timeout
- Verify abort if exceeds timeout

OBSERVABILITY:
- Monitor statefulset_rolling_restart_duration_seconds in staging
- Alert if duration > expected_time

CANARY:
- Staging 7 days, watch for timeout incidents (expect zero)
- Monitor avg upgrade duration (expect no regression)
- If clean, promote to prod
```

### Step 6: Capture Provenance (Per MCS-208)

**Every proposal needs traceable reasoning.**

Document:

**1. Brief source:**
- Where brief came from (Jira, doc, manual)
- When retrieved
- Brief version if available

**2. Incident evidence used:**
- ITN/incident.io reference
- WebRCA ID
- Timeline events or log excerpts cited
- OHSS ticket if customer-facing

**3. Code/architecture investigation:**
- Repos and commits inspected
- Files read during analysis
- Assumptions made

**4. Key decisions:**
- Why this change vs alternatives
- Why code fix vs workaround (with evidence)
- Why these files/components vs others

**Example:**
```
Provenance:

Brief Source:
- Jira: ROSAENG-12345
- Retrieved: 2026-10-22 11:45 EDT
- Assembled by: MCS-214 intake

Incident Evidence:
- ITN-2026-00123: Control plane upgrade timeout
- WebRCA: webrca.example.com/incidents/456
- Timeline: 2026-10-15 14:30-16:00 UTC
- Impact: 4 clusters blocked on EUS
- OHSS-98765: ACME-123 escalation

Investigation:
- Repo: cluster-upgrade-controller (commit: abc1234)
- Files: pkg/health/statefulset.go:45-89
- Assumption: StatefulSet restart interval = 30s (cluster default)

Decisions:
- Why code fix: Deterministic calc, no external blocker
- Why these files: StatefulSet check isolated in pkg/health
- Alternative rejected: Global timeout increase (hides other failures)
```

### Step 7: Output Structured Proposal

```markdown
# PMR Remediation Proposal: [Brief Title]

**Brief:** [Jira key / reference]
**Incident:** [ITN / incident.io link]
**Status:** ✅ Proposal Ready

---

## Failure Mechanism

[One sentence: WHY incident occurred]

---

## Proposed Change

**Type:** [Code fix / Architectural / Configuration / Observability / Workaround]

**What:**
[Specific modification]

**Where:**
[Files, functions, components - with repo paths]

**How:**
[Implementation approach at design level]

[IF WORKAROUND: Full justification per Step 4]

---

## Rationale

[Connect change to failure mechanism. Explain WHY this prevents 
recurrence. Address why this approach vs alternatives.]

---

## Verification Approach

**Unit Tests:**
- [Test cases]

**Integration Tests:**
- [Scenarios]

**Observability:**
- [Metrics, logs, dashboards]

**Canary:**
- [Rollout verification]

---

## Provenance

**Brief Source:**
- [Origin, timestamp]

**Incident Evidence:**
- [ITN, WebRCA, timeline]

**Investigation:**
- [Repos, files, assumptions]

**Key Decisions:**
- [Why this change, alternatives]

---

## Next Steps

1. **Grooming gate:** Human review/approval (MCS-46 Stage 4)
2. **If approved:** Assign to rosa-agent
3. **Implementation:** rosa-agent writes tests first, then fix (pmr-action-item skill)
4. **Review gate:** PR review before merge

---

🤖 *Proposal by pmr-remediation-analysis skill*
```

### Step 8: Tag Jira Issue as Proposal-Ready

After successfully generating a proposal (not a decline), tag the Jira issue with **`pmr-ai-ready`** to signal that:
- Analysis is complete
- Proposal is ready for human grooming gate
- Issue is eligible for `pmr-action-item` implementation

**Per `AGENTS.md`, use the `jira` skill for all Jira operations:**

```shell
# Add pmr-ai-ready label to the issue
jira -H "Content-Type: application/json" \
  -X PUT "$JB/rest/api/3/issue/<ISSUE-KEY>" \
  -d '{"update":{"labels":[{"add":"pmr-ai-ready"}]}}'
```

**Do NOT tag if:**
- Proposal was declined (explicit reasoning output)
- Brief was incomplete (stopped at Step 1)
- Analysis identified blockers requiring human input

**Comment on the ticket with proposal link:**
After tagging, add a comment with a link to where the proposal was saved (Google Doc, Jira comment, file attachment, etc.):

```
Proposal analysis complete. See attached proposal document.

Next: Human grooming gate for approval before implementation.
```

## Decline Path: Cannot Propose Sound Remediation

**Use when you cannot propose with confidence.**

**Reasons to decline:**
- Failure mechanism unclear from evidence
- Multiple plausible root causes, cannot disambiguate
- Proposed fix would be a guess (not evidence-based)
- Workaround proposed but lacks proper justification
- Insufficient code/architecture context
- Brief complete but evidence contradictory

**Output:**

```markdown
# PMR Remediation: Cannot Propose

**Brief:** [Jira key]
**Incident:** [ITN link]
**Status:** ❌ Declined

---

## Reason for Decline

[Specific reason no sound proposal can be made]

---

## What's Missing or Unclear

[Detailed gap explanation]:
- [Contradictory evidence]
- [Missing context]
- [Investigation needed]

---

## Recommended Next Steps

**To unblock, one of:**

1. **Additional investigation:**
   - [Specific data to gather]
   - [Tests to run]
   - [Logs to examine]

2. **Evidence clarification:**
   - [Questions to answer]
   - [Contradictions to resolve]

3. **Manual review:**
   - [Why human expertise needed]
   - [Who should review]

**Alternative:** Manual assignment to engineer familiar with [component]

---

## Provenance

[Document what you DID examine - same format as proposal]

---

🤖 *Decline by pmr-remediation-analysis skill*
```

**CRITICAL:** Declining is better than low-confidence guessing. Reviewers spend more time on plausible-but-wrong proposals than explicit declines.

## Execution Patterns

### Pattern 1: Interactive Use (Human-Driven)

```
User: /pmr-remediation-analysis ROSAENG-12345

→ Fetch Jira ticket using jira skill
→ Validate brief completeness
→ Analyze failure mechanism
→ Propose remediation
→ Output structured proposal for human review
```

### Pattern 2: Triage Scan (Discovery Mode)

```
User: /pmr-remediation-analysis (no ticket key)
  — OR —
Cron: claude --dangerously-skip-permissions \
        --print "/pmr-remediation-analysis"

→ Query Jira: labels=pmr-ai, assignee=rosa-agent, not pmr-ai-ready
→ For each ticket:
    → Check readiness
    → If incomplete: comment with ADF @mention of reporter, skip
    → If ready: run full analysis (Steps 1–8)
→ Report: N tickets found, M ready, K commented
```

### Pattern 3: Pipeline Integration (Automated)

```
Intake (MCS-214) → produces brief
↓
Proposal (THIS SKILL) → analyzes brief
↓
Card Creation → proposal becomes Jira card
↓
Grooming Gate → human approves/denies
```

**Non-interactive note:** If invoked non-interactively (like pmr-action-item skill):
- Never pause for input
- "Can't determine X" → decline with reason, don't guess
- Output to stdout for capture by pipeline

## Integration with Jira Skill

Per `AGENTS.md`, all Jira calls MUST go through the `jira` skill:

```
1. Load jira skill: `Skill({skill: "jira"})`
2. Let it run auth detection
3. Use its $JB (Jira base URL) and methods
```

## Important Notes

- **This skill does NOT implement** - it only proposes
- **Implementation** is done by `pmr-action-item` skill (Stage 6)
- **Declining is valid** - report what's missing, don't guess
- **Workarounds need justification** - default to code/arch changes
- **Provenance required** - reviewer must audit reasoning
- **Brief schema evolving** - adapt to MCS-214 output format
- **Proposal format may change** - based on MCS-208 decisions
- **ADF mentions require accountId** - fetch from `reporter.accountId`; bare `@name` text does not send Jira notifications

## Related

- **MCS-46**: PMR action pipeline (parent epic)
- **MCS-214**: Intake (brief assembly) - feeds this skill
- **MCS-208**: Provenance record spec
- **pmr-action-item skill**: Implements approved proposals (Stage 6)

## Example Usage

### Example 1: Successful Proposal

```
User: Analyze this PMR brief:

Action: ROSAENG-74825 - Prevent cluster upgrade timeout during StatefulSet restart
Incident: ITN-2026-00123
Evidence: Upgrade timed out at 90m, StatefulSet pods hadn't finished rolling restart...

[... full brief ...]

Agent:
→ Validates brief (complete ✅)
→ Analyzes: Failure = health check doesn't account for OnDelete strategy delay
→ Proposes: Code fix in cluster-upgrade-controller health check logic
→ Outputs: Structured proposal with failure mechanism + change + verification + provenance
```

### Example 2: Decline - Unclear Mechanism

```
User: Analyze ROSAENG-74826

Agent:
→ Fetches ticket via jira skill
→ Validates brief (complete ✅)
→ Analyzes evidence: 3 plausible root causes (race condition vs config vs upstream bug)
→ Cannot disambiguate from available evidence
→ Outputs: Decline with specific gap (need distributed trace or reproduce steps)
```

### Example 3: Decline - Workaround Without Justification

```
User: Propose fix for intermittent API timeout

Agent:
→ Analyzes: Timeout occurring under load
→ Initial thought: Increase timeout (workaround)
→ Lacks evidence that code fix is infeasible
→ Outputs: Decline - need investigation into root cause before proposing timeout increase
```

### Example 4: Discovery Mode - Incomplete Ticket

```
Agent (no ticket key given, or cron-driven):
→ Queries Jira: labels=pmr-ai, assignee=rosa-agent, not pmr-ai-ready
→ Finds MCS-1434 (no description)
→ Readiness check fails: missing all 4 required elements
→ Posts ADF comment @mentioning reporter Eddie Rosentel with specific missing items
→ Skips to next ticket
→ Summary: 1 ticket found, 0 ready, 1 commented
```

---

🤖 *MCS-216: PMR remediation proposal skill*
