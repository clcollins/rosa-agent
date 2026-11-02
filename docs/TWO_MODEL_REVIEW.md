# Two-Model Code Review Process

This document describes the two-model review workflow added to the `pmr-action-item` skill.

## Overview

The PMR action item workflow now uses **two different models** for implementation and review:

1. **Model 1 (Sonnet 4.5)** - implements the ticket
2. **Model 2 (Fable/Opus)** - critically reviews the implementation
3. **Model 1 (Sonnet 4.5)** - implements the review findings
4. **Security re-check** - re-runs security review on the updated code
5. **PR creation** - only after both reviews pass

This mirrors the workflow where a human shares an external model's review, and we implement those findings before creating the PR.

### Why Fable for Code Review?

Fable is designed to be more analytical and critical, making it ideal for code review:

- More thorough at finding edge cases and correctness issues
- Better at identifying subtle logic errors
- Provides detailed, structured feedback
- Different reasoning approach than Sonnet provides independent perspective

If Fable is unavailable, the agent falls back to Opus or another available model.

## Workflow Steps

### Updated PMR Action Item Flow

1. Get target (Jira issue key)
2. Read ticket
3. Identify target repository
4. Fork and sync, then branch
5. Implement the work described in the ticket
6. **Security fan-out** - parallel subagents + security-review skill
7. **Critical code review** - independent model (Fable) reviews the diff ← NEW
8. **Implement code review findings** - fix CRITICAL and IMPORTANT issues ← NEW
9. **Re-run security fan-out** - verify fixes didn't introduce security issues ← NEW
10. Blockers go on the ticket
11. Open the PR
12. Link back to the ticket

## Implementation Details

### Model Override in Skill

The PMR action item skill directly spawns an Agent with a model override using the `model` parameter:

```python
Agent(
  description="Critical code review",
  model="fable",  # or "opus" or "haiku" as fallback
  prompt="Review this diff for correctness, edge cases, and maintainability..."
)
```

This keeps the two-model review **isolated to PMR action items only** - it doesn't affect other workflows or create a global code-reviewer agent that would be used everywhere.

To change which model is used for code review, edit the skill file (`sandbox/skills/pmr-action-item/SKILL.md`) and update the `model` parameter in step 7.

### Review Criteria

The code-reviewer agent assesses:

1. **Correctness** - logic errors, edge cases, nil/null handling
2. **Robustness** - error handling, boundary conditions
3. **Maintainability** - code clarity, structure, naming
4. **Testing** - test coverage for new code paths
5. **Integration** - consistency with codebase patterns
6. **Performance** - obvious inefficiencies (N+1 queries, etc.)

### Finding Severity Levels

- **CRITICAL**: Blocks PR - logic error, crash risk, production failure scenario
- **IMPORTANT**: Should fix - maintainability issues, missing tests for new code
- **MINOR**: Nice-to-have - style consistency, naming improvements

Only CRITICAL findings block the PR. IMPORTANT findings should be implemented if straightforward.

## Files Changed

### 1. `sandbox/skills/pmr-action-item/SKILL.md`

- Updated description to include two-model review process
- Added step 7: Critical code review with independent model using `model="fable"` parameter
- Added step 8: Implement code review findings  
- Added step 9: Re-run security fan-out after implementing review findings
- Renumbered subsequent steps (old 7→10, 8→11, 9→12)
- Updated guardrails to mandate the code review step
- Updated blocker references to include code review findings

**Note**: The model override is embedded directly in the skill instructions, not a global agent definition. This keeps the two-model review isolated to PMR action items only.

## What Needs to be Verified

### 1. Vertex AI Model Access

Verify that the Vertex AI provider (`rosa-agent-vertex`) has access to the required models:

- ✅ `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (current default for implementation)
- ❓ Fable, Opus, or Haiku (for independent code review)

Check Google Vertex AI Model Garden (`hypershell-976970` project) for available Anthropic models.

**Model flexibility**: The PMR action item skill is currently configured to use `fable` for code review, but this can be changed to `opus` or `haiku` by editing step 7 in `sandbox/skills/pmr-action-item/SKILL.md`. The key requirement is that it uses a **different model** than the implementing agent (Sonnet) to provide independent perspective.

**Scope**: This two-model review is **specific to PMR action items only**. Other workflows continue to use only Sonnet.

### 2. Model Override Works

Test that the Agent tool's `model` parameter successfully spawns agents with different models:

```bash
# In a sandbox, test spawning an agent with explicit model override:
claude --print "Use Agent tool with model='fable' to analyze this file and report back"
```

Check the agent's behavior to confirm it's using a different model than the default Sonnet.

### 4. Cost Implications

Understand the cost impact:

- Sonnet 4.5: implementation + remediation + security reviews
- Fable (or Opus/Haiku): one code review pass per ticket

The code review is primarily **reading** (input tokens) with small structured output (findings list). Total overhead is approximately 30-40% additional tokens, not 100% doubling:

- **Review model input tokens**: reading diff + context (cheaper)
- **Review model output tokens**: structured findings (small)
- **Additional Sonnet tokens**: targeted fixes + security re-check

Most of the additional cost is input tokens, which are cheaper than output tokens.

## Testing the Workflow

### Manual Test

1. Create a test Jira ticket labeled `PMR-AI-PILOT`, assigned to `rosa-agent`
2. Make it a simple, safe change (e.g., update a README)
3. Invoke manually:

   ```bash
   /pmr-action-item ROSAENG-XXXXX
   ```

4. Observe the new steps 7-9 in the output
5. Verify the PR includes both the original implementation AND the code review fixes

### Verify Multi-Model Usage

Check the logs/output to confirm:

- Step 5 uses Sonnet (implementation)
- Step 7 spawns code-reviewer agent with Fable (code review)
- Step 8 uses Sonnet (implement findings)
- Step 9 uses Sonnet (security re-check)

## Rollback Plan

If the two-model review causes issues:

1. Revert `sandbox/skills/pmr-action-item/SKILL.md` to remove steps 7-9 (the code review and remediation steps)
2. Rebuild image

The skill will revert to the previous one-model workflow (implement → security check → PR).

Since the model override is embedded in the skill itself (not a global agent definition), reverting the skill file is all that's needed. No other workflows are affected.

## Future Enhancements

- **Configurable review model**: Allow per-ticket override (e.g., ticket comment `USE_MODEL=opus` to switch from Fable to Opus for a specific review)
- **Review quality metrics**: Track how often the review model finds issues Sonnet missed
- **Iterative reviews**: Allow multiple review-fix cycles if first pass has many findings
- **Review scope tuning**: Fine-tune what the reviewer focuses on based on ticket type
- **Model selection logic**: Automatically choose review model based on code type (e.g., Fable for complex logic, Haiku for simple changes)
