## PMR Remediation Analysis Skill

**MCS-216**: Analyze PMR action briefs and propose concrete remediations tied to failure mechanisms.

## Purpose

This skill is **Stage 2** of the PMR action pipeline (MCS-46):

1. **Intake** (MCS-214) → Assembles action + incident into brief
2. **Proposal** (MCS-216 - **THIS SKILL**) → Analyzes brief, proposes fix
3. Card Creation → Proposal becomes Jira card
4. Grooming Gate → Human approves/denies  
5. Assignment → Assign to rosa-agent
6. **Implementation** (`pmr-action-item` skill) → Implements approved proposal
7. Review Gate → PR review before merge

## What This Does

Given a PMR action brief (from intake), this skill:

✅ Analyzes incident evidence to identify failure mechanism  
✅ Proposes code or architectural fix (workarounds need justification)  
✅ Defines verification approach (tests, observability, canary)  
✅ Captures provenance (what evidence was used, what was investigated)  
✅ Tags Jira issue with `PMR-AI-Ready` label to signal proposal completion  
❌ **OR** explicitly declines if no sound proposal can be made

## What This Does NOT Do

- ❌ Assemble the brief (that's MCS-214 intake)
- ❌ Create Jira cards (that's Stage 3)
- ❌ Implement the fix (that's `pmr-action-item` skill)
- ❌ Approve proposals (that's human grooming gate)

## Usage

### Interactive (Human-Driven)

```bash
# From rosa-agent repo
claude --print "/pmr-remediation-analysis ROSAENG-12345"
```

Or paste brief directly:
```
Analyze this PMR brief:

Action: ROSAENG-12345 - Fix cluster upgrade timeout
Incident: ITN-2026-00123
Evidence: [timeline, logs, etc.]
Affected: cluster-upgrade-controller repo
```

### Pipeline Integration (Future)

```
Intake → Brief assembled
   ↓
Proposal → claude --dangerously-skip-permissions \
             --print "/pmr-remediation-analysis ${BRIEF}" \
             > proposal.md
   ↓
Card Creation → Create Jira from proposal.md
```

## Output

### Success: Structured Proposal

```markdown
# PMR Remediation Proposal: [Title]

## Failure Mechanism
[One sentence: WHY incident occurred]

## Proposed Change
TYPE: Code fix / Architectural / Workaround
WHAT: [Specific modification]
WHERE: [Files, repos, components]
HOW: [Implementation approach]

## Rationale
[Why this prevents recurrence]

## Verification Approach
[Tests, observability, canary]

## Provenance
[Evidence used, investigation trail, decisions]

## Next Steps
1. **Tag issue:** Adds `PMR-AI-Ready` label to Jira
2. **Grooming gate:** Human review/approval  
3. **Implementation:** `pmr-action-item` skill (only runs on `PMR-AI-Ready` tickets)
4. **Review gate:** PR review before merge
```

### Decline: Explicit Reasoning

```markdown
# PMR Remediation: Cannot Propose

## Reason for Decline
[Why no sound proposal possible]

## What's Missing or Unclear
[Specific gaps in evidence or context]

## Recommended Next Steps
[Investigation needed to unblock]
```

## Key Principles

1. **Code/Architecture First**: Workarounds need explicit justification with evidence
2. **Falsifiable Mechanism**: Failure mechanism must be specific enough to verify
3. **Decline When Uncertain**: Better than low-confidence guessing
4. **Provenance Required**: Reviewer must audit reasoning
5. **Evidence-Based**: Proposals tied to incident data, not speculation

## Dependencies

### Required
- `jira` skill (per AGENTS.md - all Jira calls go through it)
- Access to GitHub repos for code investigation
- Access to Jira API (OCM credentials or JBTOKEN)

### Optional (Enhances)
- WebRCA API access (via OCM `ocm get /api/web-rca/v1/...`)
- Google Docs MCP (for brief retrieval if doc link)
- Slack MCP (for timeline evidence)
- PagerDuty MCP (for alert context)

## Brief Schema

**Expected from MCS-214** (in progress):

```yaml
brief:
  action:
    jira_key: ROSAENG-XXXXX
    summary: "[Description]"
  incident:
    itn_reference: ITN-YYYY-NNNNN
    impact_start: "[ISO 8601]"
    impact_end: "[ISO 8601]"
  evidence:
    timeline: [events]
    logs: "[Excerpts]"
  affected_component:
    repository: "owner/repo"
    component: "[Name]"
```

**Current state**: Accepts flexible input (Jira key, text, doc) until MCS-214 standardizes.

## Provenance Record

**Per MCS-208 spec** (in progress):

Captures:
- Brief source (where, when)
- Incident evidence used (ITN, WebRCA, timeline)
- Code investigation (repos, files, commits)
- Key decisions (why this change, alternatives)

**Storage**: TBD by MCS-208 (likely Jira card or linked doc)

## Workaround Justification Requirement

If proposing a workaround (vs code/arch fix), MUST include:

1. **Why code/arch is infeasible:**
   - Technical blocker (upstream bug, API limit)
   - Timeline constraint with evidence (escalation, commitment)
   - Resource constraint (N-month refactor, no capacity)

2. **Evidence:**
   - Link to upstream issue
   - Quote from customer escalation
   - Team estimate for proper fix

3. **Tradeoffs:**
   - What breaks/degrades
   - Technical debt incurred
   - Follow-up action item

**If missing:** Skill will decline instead of proposing unjustified workaround.

## Acceptance Criteria (MCS-216)

Per ticket definition:

- ✅ Emits structured proposal with: failure mechanism, change, rationale, verification
- ✅ Workarounds include explicit justification
- ✅ Can decline with specific reason
- ✅ Includes provenance per MCS-208
- ⏳ Evaluated against 10+ real PMR actions (pending)
- ⏳ Wrong-proposal rate documented (pending)

## Development Status

**Current**: Initial implementation (MCS-216 In Progress)

**Next**:
- Evaluate against real PMR backlog actions
- Refine based on grooming gate reviewer feedback
- Integrate with MCS-214 intake output format
- Align provenance with MCS-208 final spec

## Related Skills

- **`pmr-action-item`**: Implements approved proposals (Stage 6)
- **`jira`**: Required for Jira API access
- **`rca-management`**: Similar pattern for RCA document generation

## Jira References

- **MCS-216**: This skill's ticket (PMR remediation proposal)
- **MCS-46**: PMR action pipeline (parent epic)
- **MCS-214**: Intake brief assembly
- **MCS-208**: Provenance record spec

## Example Invocations

### Analyze Jira Ticket
```bash
claude --print "/pmr-remediation-analysis ROSAENG-74825"
```

### Analyze Pasted Brief
```bash
claude --print "$(cat << 'EOF'
Analyze this PMR brief:

Action: ROSAENG-74825
Summary: Prevent cluster upgrade timeout during StatefulSet restart
Incident: ITN-2026-00123
Evidence:
- Timeline: 2026-10-15 14:30 - Upgrade started
- Timeline: 2026-10-15 16:00 - Timeout at 90m, aborted
- Logs: StatefulSet pods still in rolling restart when timeout hit
- Impact: 4 customer clusters blocked on EUS transition
Affected: cluster-upgrade-controller repo
Component: Upgrade health check logic

Details:
Control plane upgrade process timed out after 90 minutes. 
StatefulSets configured with OnDelete update strategy were still 
restarting pods when the health check timeout fired. The upgrade 
controller aborted before all pods became ready.
EOF
)"
```

### Non-Interactive Pipeline Use
```bash
#!/bin/bash
# Future pipeline integration

BRIEF=$(cat brief-rosaeng-12345.yaml)

claude --dangerously-skip-permissions \
  --print "/pmr-remediation-analysis ${BRIEF}" \
  > proposal-rosaeng-12345.md

if grep -q "Status: ✅ Proposal Ready" proposal-rosaeng-12345.md; then
  echo "Proposal generated, creating Jira card..."
  # Stage 3: Create Jira card from proposal
else
  echo "Proposal declined, manual review needed"
  # Human intervention required
fi
```

## Testing

### Unit Testing (Manual)

Test decline path:
```bash
# Incomplete brief
claude --print "Analyze PMR: Action ROSAENG-999 Incident ITN-2026-999"
# Expected: Decline - missing evidence

# Contradictory evidence
claude --print "Analyze PMR with conflicting timeline events..."
# Expected: Decline - cannot disambiguate
```

Test proposal path:
```bash
# Complete brief with clear failure mechanism
claude --print "/pmr-remediation-analysis ROSAENG-74825"
# Expected: Structured proposal
```

### Integration Testing (Future)

- Run against 10+ real PMR backlog actions (MCS-216 AC #5)
- Human reviewer rates each: sound / salvageable / wrong
- Document wrong-proposal rate (MCS-216 AC #6)

## Contributing

Changes to this skill should:
1. Maintain decline-when-uncertain principle
2. Preserve provenance capture
3. Enforce code/arch preference over workarounds
4. Update this README with new patterns

## License

Internal Red Hat tooling - not for external distribution.

---

🤖 *MCS-216: PMR remediation analysis skill for rosa-agent*
