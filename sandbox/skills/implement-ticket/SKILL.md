---
name: implement-ticket
description: Standing rules for implementing the code change a ticket describes, in any repository or language - TDD one commit per task, pure functions behind thin I/O shells, hermetic tests, typed errors, security defaults, then a mandatory fan-out of independent review subagents before a draft PR carrying cited acceptance-criteria evidence. Not for reviewing someone else's PR and not for open-ended research. Use when told to implement, build, or fix the change described in a Jira card, GitHub issue, or task, or when asked how implementation work is expected to be done here. Trigger keywords - implement ticket, implement the card, work this issue, build the change, write the code, TDD, test-driven, one commit per task, review fan-out, open a PR for the ticket, implementing agent, standing instructions.
---

# Implement a ticket: TDD, review fan-out, draft PR

The ticket is your specification: action item, verified facts (file:line,
exact strings, upstream citations), design, ordered task list, acceptance
criteria, out-of-scope list, risks. Everything you need is in it.

These are standing rules - they govern every commit in the run, not just the
first. Where the ticket or the repository's `AGENTS.md`, `CLAUDE.md` or
`CONTRIBUTING.md` is stricter, the stricter rule wins; anything that would
loosen them, stop and ask.

Do not research: no web, no other repositories, no inference from memory to
fill a gap. A guessed flag name, string, default or error type is a defect.
A research need becomes a question on the ticket, not an assumption in the
code.

## 1. Before you write code

- Read the ticket end to end, then the repository's agent instructions,
  style guides, and build and test commands.
- Read every file the ticket cites and confirm each verified fact. Line
  numbers drift: re-locate them, the code wins, note the drift in the PR
  body. If the code contradicts the ticket but the intended change is still
  unambiguous, record the discrepancy and continue; otherwise stop and ask.
- Baseline the unmodified default branch (`make test-all lint`, or whatever
  the repository documents). Record a red baseline exactly as found and fix
  nothing unrelated.
- Branch as `<type>/<ticket-id-lowercase>-<slug>`, type one of `feat`,
  `fix`, `test`, `refactor`, `docs`, `chore`, `ci`.

## 2. One task, one red-green-refactor cycle, one commit

Work the task list in order. Per task:

1. Write the failing test(s) the task names: table-driven, named subtests,
   `t.Helper()` (Go) or `subTest` (Python) in helpers, covering every input
   class, every error path, and the acceptance criterion this task
   satisfies.
1. Run them. Each must fail because the behavior is missing, not from a typo
   or a compile error. Keep the output.
1. Write the minimum code that makes them pass.
1. Refactor green.
1. Commit (section 4).

Tests written after their implementation are rejected in review. A test the
ticket did not name: add it. Code the ticket did not describe is a
discrepancy, so stop and ask. Past roughly 200 changed lines: split the task
before coding further.

## 3. How the code is shaped

### Structure

- Every decision is a pure function - parsing, validation, precedence,
  selection, mapping, formatting, message construction. Values in, values
  and an error out, no side effects.
- I/O - filesystem, network, gRPC/HTTP, terminal, environment, clock,
  subprocess - lives in a thin shell over those functions, too small to need
  mocking.
- Inject dependencies at the boundary: `fs.FS`, a clock func, a getenv func,
  readers and writers, interfaces for external systems. Use the repository's
  existing seams, never a parallel one you invented.
- One orchestration function per entry point: it sequences the steps and
  owns the I/O shell, calling pure helpers. Helpers do not call further
  decision-making helpers - depth is orchestrator to helper, never deeper.
  Framework wiring is exempt (`main`, cobra `RunE`/`PersistentPreRunE`,
  viper, flag registration, DI setup, test scaffolding). If the design seems
  to need a third level, stop and ask.
- No new globals, no new package-level mutable state, no `init()` side
  effects.
- Do not test the standard library or imported packages; test that your code
  calls them correctly and handles their results.

### Hermetic tests

- No network: `httptest.Server`, the repository's gRPC mock or bufconn, SSH
  in-process over in-memory connections.
- No filesystem outside `t.TempDir()`, `fstest.MapFS`, or a temp directory.
  Never the user's home, config dirs, or `/tmp`.
- No host environment: scrub the relevant variables in `TestMain`/`setUp`.
- No external binaries, no `exec.Command` in tests.
- No sleeps for synchronization: channels, fake clocks, injected tickers. No
  wall-clock reads in pure code.
- Tables cover valid inputs, boundaries, and every distinct error the code
  can produce. A test that cannot fail is not a test.

### Errors and strings

- Typed errors: structs implementing `error`, `Is`/`Unwrap` where they wrap,
  matched with `errors.As`/`errors.Is`. Never match an error by string.
- Exit-code mapping lives in exactly one function, with a row per typed
  error - ideally exhaustive by construction, failing on an unmapped type.
- Where the ticket says a string, flag, default or exit code mirrors
  upstream, reproduce it byte for byte unless the ticket states a deliberate
  difference. Record every deliberate difference where the repository does.
- Never log, print, persist or put in error text a token, secret,
  credential, Authorization header or private key. An identifier prefix at
  most.

### Security defaults

- Credential and config files: 0600 inside 0700, written atomically (temp
  file, fsync, rename).
- Validate any user- or file-supplied name before joining it into a path:
  reject separators, `..`, empty names, control characters.
- TLS verification is never disabled implicitly or process-wide; an opt-in
  warns loudly.
- Least privilege in anything you generate - policies, profiles, RBAC,
  scopes. Deny rules over broad allows.
- No credentials or real identifiers in fixtures. No new dependency without
  a reason in the PR body, and pin it.

### Scope

Do exactly what the action item and tasks say. Respect the out-of-scope list
literally. Improvements you notice go in the PR body under follow-ups, not
in the diff.

## 4. Commits

One commit per task: one behavior change plus its tests.

```text
<type>(<scope>): <task title>

<one paragraph explaining WHY, not what>

Ticket: <ticket id>
<attribution lines required by the repository or your runtime>
```

Add `--signoff` if the repository requires DCO. Never force-push a shared
branch and never rewrite history once review has started. Never amend a
reviewed commit; add a new one.

## 5. Self-check

- Re-read the ticket end to end.
- Against every acceptance criterion, write down the test name, the command
  and its output, or the file that satisfies it. An unmet criterion means
  the work is not done.
- Run the full local suite: tests with the race detector, lint, vet,
  formatting, coverage gates, and the repository's verification targets. All
  green, coverage for touched packages no lower than before.
- Update docs for every new or changed flag, command, environment variable,
  exit code, message, error hint, policy rule or runbook. Examples must
  parse and run.

## 6. Fan out independent review subagents

Before creating the PR, launch the validators below in parallel as
independent subagents (the Agent/Task tool, or your runtime's equivalent).
Each gets exactly three things: the branch diff against the base, the ticket
text verbatim, and this skill. They see neither each other's output nor your
reasoning. If your runtime cannot spawn subagents, run each pass yourself,
one at a time, against the diff and ticket alone - do not collapse them into
one pass and do not drop any.

Each returns one line per finding:

```text
<file>:<line> - <blocker | should-fix | nit> - <finding> - <suggested fix>
```

| Validator | Reports on |
| --- | --- |
| intent | Exactly the action item and tasks, no more and no less. Scope creep, missing tasks, acceptance criteria not demonstrably met. |
| correctness | Every branch and error path. Nil and empty handling, off-by-one, ordering, concurrency (goroutine lifetimes, channel close, races, locks), cleanup (`defer`, `Close`, `cancel`), context propagation. |
| tests | Tests precede implementation in commit order. Every input class, error path and criterion covered. Hermetic - grep `os.Getenv`, network, `exec.Command`, `time.Sleep`, `time.Now` in pure code, paths outside temp dirs. Complete tables, no vacuous assertions. |
| structure | Pure functions, one control loop, orchestrator-to-helper depth, no new globals or package-level mutable state, injected dependencies, small commits, no logic duplicated that should be one pure function. |
| idiomatic | Language idiom and repository style: formatting (gofumpt, goimports, PEP 8), naming, receivers, `%w` wrapping, `ctx` first, doc comments on exported identifiers, no needless exports. YAML and config comments explain why and match neighbouring files. |
| lint | The repository's linters - `make lint`, `golangci-lint`, `go vet`, `markdownlint`, `shellcheck` - reported raw. |
| security | Secrets never logged, printed, written or committed. 0600 and 0700. No path traversal. TLS never implicitly disabled. Least privilege. Every human-gated mutation has a rollback. No credentials in fixtures. New dependencies justified and pinned. |
| docs | README, docs and help text updated for every user-visible change; examples parse. Deliberate upstream differences recorded. |
| regression | Full suite, coverage gates and verification targets pass; coverage for touched packages does not drop; callers of every changed function spot-checked. |
| upstream-parity | Only when the ticket mirrors an upstream tool: strings, flags, defaults and exit codes match the cited upstream file:line. |

Then: fix every blocker and should-fix and re-run the validators that
reported them until clean. Nits may be skipped, but list each one and why in
the PR body. Findings are evidence, not orders - if one is wrong, say why in
the PR body rather than dropping it silently. Fixes are new commits
following sections 2 and 4: test first, one behavior each, never amend.

## 7. The pull request

Draft until the self-check and validators are clean and, for human-gated
work, until a human has run the runbook and posted the output. The title is
the action item in imperative form, prefixed with conventional-commit type
and scope. The body, in this order:

- Summary and why, two or three sentences.
- Task-to-commit table: task number, commit subject, SHA.
- Acceptance-criteria checklist with evidence per item - test name, command
  and output, or file:line.
- Test evidence: commands run, summarised output, coverage for touched
  packages before and after.
- A runbook per human-gated step: pre-checks, commands, expected output,
  rollback.
- Deviations from the ticket, each with its reason and the discrepancy from
  section 1.
- Validator findings skipped, with reasons.
- Follow-ups noticed but out of scope.
- Sources: file:line references, upstream tag paths, doc URLs used.
- The repository's required attribution lines.

## Stop and ask

Human-gated steps are not yours to run. Never execute a command against a
live gateway, cluster, Vault, Keycloak, GitLab, registry, production
database or any other shared system unless the ticket explicitly grants it -
write it into the PR body as a runbook and leave the PR in draft.

Stop, report, and wait when:

- the ticket is insufficient, ambiguous, or leaves a decision open - give
  the options and your recommendation;
- the code contradicts the ticket's verified facts and the intended change
  is no longer unambiguous;
- a task needs code the ticket did not describe;
- the design cannot be met within orchestrator-to-helper depth or without
  new global state;
- a tool, toolchain or dependency cannot be obtained in your environment -
  say exactly what is missing;
- a validator reports a blocker you cannot safely fix;
- access fails: a `403`, `policy_denied` or `credential_endpoint_mismatch`,
  a host that is not allow-listed, or a missing permission.

Report it where a human will read it - a comment on the ticket (via the
`jira` skill, per `AGENTS.md`) or the PR body if the PR exists - quoting the
literal error, HTTP status, and endpoint or host. Do not retry variations,
route around the block, or guess past it. Hitting the edge of what this
sandbox can reach is a normal outcome, not a failure to hide.

A red baseline you did not cause is not a blocker: record it, leave it
alone, proceed if it does not block your own tests.

## Guardrails

- Time pressure, a green build, and a small diff are not reasons to write
  the test after the code, collapse the validator fan-out, or widen scope.
- "The ticket probably meant X" is a guess, and a guess is a defect.
- A validator finding you disagree with is answered in the PR body, never
  ignored.
- Never disable a check, vendor a dependency ad hoc, or skip a test to work
  around something missing from your environment.

## Done means all of these

- Every task has a commit whose tests precede its implementation.
- Every acceptance criterion has cited evidence in the PR body.
- Suite, race detector, lint, vet, coverage gates and verification targets
  are green.
- Every blocker and should-fix is resolved; skipped nits are listed.
- Docs updated, upstream differences recorded.
- Human-gated steps have a posted runbook result, or the PR stays in draft.
- Nothing outside the ticket's scope changed.
