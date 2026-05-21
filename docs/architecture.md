# Flow Mode Architecture

`flow-mode` is a lightweight local supervisor for personal agentic coding with Codex. It is inspired by Beads and Gas Town, but intentionally avoids a large always-on distributed system. The goal is to keep durable task state, launch isolated coding agents automatically, and continuously improve project verification based on where agents get stuck.

## Design Goals

- Stay terminal-first and local-first.
- Use Beads as the durable task ledger.
- Avoid a Dolt server initially.
- Run multiple agents on one machine safely.
- Isolate each worker in a git branch and worktree.
- Keep subagent context small and task-scoped.
- Enforce model, file-scope, runtime, and permission constraints.
- Automatically review and merge when verification passes.
- Capture repeated agent failures and turn them into testability or infrastructure tasks.

## Non-Goals

- Recreate Gas Town's full orchestration system.
- Maintain a remote scheduler, worker pool, or distributed database.
- Require a web UI for the first usable version.
- Let every worker write directly to the ledger concurrently.
- Treat agent-written tests as sufficient without a project verification contract.

## System Shape

```text
User -> c3x inbox -> architect loop -> Beads task graph
                           |
                           v
                     worker launcher
                           |
        git worktree + constrained prompt + sandboxed Codex
                           |
                           v
                verify -> review -> merge -> learn
                           |
                           v
                      critic loop
```

The CLI owns orchestration and guardrails. Codex agents own reasoning, implementation, review, and critique within constrained prompts and file scopes.

## Terminal Experience

The primary interface is a blocking terminal app:

```bash
c3x run
```

The app keeps a live operations loop open:

```text
flow-mode

Inbox:        3 raw ideas
Questions:   2 waiting for you
Ready:        5 tasks
Running:      3 agents
Reviewing:    1 branch
Blocked:      2 tasks

[question] bd-123: Should auth redirects preserve query params?
[agent]    bd-456: worker-2 running tests in app-login-fix
[critic]   repeated failure: agents lack reliable seeded checkout data
```

The loop should alert the user when input is needed through visible terminal prompts, terminal bell, and optionally desktop notifications when available.

Example commands:

```bash
c3x add "checkout page flashes empty cart on refresh"
c3x run
c3x answer bd-123 "yes, preserve all query params"
c3x pause
c3x resume
c3x status
c3x agents
c3x review
```

## Ledger Model

Beads is the source of truth for durable state:

- Raw ideas and feedback.
- Human questions and answers.
- Epics, tasks, spikes, bugs, tests, and critic findings.
- Dependencies and readiness state.
- Agent assignments.
- Blockers.
- Review state.
- Verification evidence.
- Follow-up tasks discovered by workers.

Because the first version does not use a Dolt server, the supervisor process should be the only Beads writer. Workers submit structured results to a spool directory. The supervisor validates those results and serializes writes into Beads.

## Spool Model

Workers do not write directly to Beads. They write structured outputs:

```text
.flow/
  runs/
    bd-123/
      prompt.md
      result.json
      logs/
      events.jsonl
  agents/
  worktrees/
```

`result.json` should include:

- Task id.
- Status.
- Summary.
- Changed files.
- Tests run.
- Test result.
- Blockers.
- Scope expansion requests.
- Proposed follow-up tasks.
- Review notes.

The supervisor imports worker results into Beads serially.

## Agent Roles

### Architect

The architect turns raw intent into executable work.

Responsibilities:

- Read raw Beads items.
- Ask follow-up questions.
- Convert vague ideas into a task graph.
- Define acceptance criteria.
- Define verification expectations.
- Refuse to dispatch underspecified work.

### Worker

The worker completes exactly one task.

Responsibilities:

- Receive one task, its dependencies, relevant context, and file scope.
- Use one git branch and one git worktree.
- Investigate before editing.
- Implement only within owned scope.
- Run required verification.
- Emit structured results.
- Propose new tasks when blocked instead of hiding uncertainty.

### Reviewer

The reviewer decides whether completed work can land.

Responsibilities:

- Review the branch against acceptance criteria.
- Check changed files against file scope.
- Check test evidence.
- Rerun verification when needed.
- Approve automatic merge when constraints pass.
- Reopen or create follow-up tasks when not safe.

### Critic

The critic looks for systemic problems in the flow.

Responsibilities:

- Analyze blocked tasks, long-running agents, repeated failures, flaky checks, and review rejections.
- Identify common places where agents get stuck.
- Create improvement tasks for fixtures, docs, test helpers, seed data, observability, or verification.
- Prevent the system from repeatedly paying the same failure cost.

Example critic-created tasks:

- Add seeded checkout fixture.
- Create authenticated Playwright helper.
- Document local environment setup.
- Add deterministic test data reset.
- Add DOM-level assertions for cart restore.
- Add visual or screenshot checks where manual judgment is recurring.

## Constraints

Constraints should be explicit and enforced by the supervisor when possible:

```yaml
models:
  architect: gpt-5.4
  worker: gpt-5.4-mini
  reviewer: gpt-5.4
  critic: gpt-5.4

limits:
  max_parallel_workers: 3
  max_files_per_task: 8
  max_context_tokens_worker: 50000
  max_runtime_minutes: 45
  require_clean_worktree: true

permissions:
  worker_shell: sandboxed_full_auto
  network: false_by_default
  merge: reviewer_only
```

Worker prompts should include an explicit owned scope:

```text
Owned scope:
- apps/web/src/cart/**
- apps/web/tests/cart.spec.ts

Do not edit outside this scope unless you stop and emit a scope-expansion request.
```

## Worktree Lifecycle

Each dispatched task gets a dedicated branch and worktree:

```text
main
  flow/bd-123-checkout-refresh
    .flow/worktrees/bd-123-checkout-refresh
```

Lifecycle:

1. Supervisor selects a ready task.
2. Supervisor creates branch and worktree.
3. Supervisor generates a constrained prompt.
4. Supervisor launches a sandboxed Codex worker.
5. Worker emits structured result.
6. Reviewer evaluates branch.
7. Supervisor merges when approved.
8. Supervisor cleans up branch and worktree when safe.

## Automatic Review And Merge

Automatic merge should be conservative:

1. Worker finishes task.
2. Worker emits result with changed files, tests run, failures, and summary.
3. Reviewer checks diff against task acceptance criteria.
4. Reviewer checks file scope.
5. Reviewer reruns `c3x verify --task <id>` when needed.
6. Supervisor merges the branch if verification and review pass.
7. Supervisor reopens the task or creates follow-ups on failure.
8. Critic observes repeated failures and creates improvement tasks.

The first version should merge locally into the current main branch. PR support can be added later.

## Verification Contract

Every project should define a `FLOW.md` verification contract. A project is not flow-ready until an agent can run meaningful checks without human interpretation.

Example:

```markdown
# Flow Verification

## Baseline
npm run typecheck
npm run lint
npm test

## Web
npm run test:e2e

## Local App
npm run dev

## Test Data
Use seed script: npm run seed:test

## Known Gaps
No visual regression coverage yet.
```

For web apps, the architect and critic should continuously look for ways to turn manual feedback into automated checks:

- If agents manually test login repeatedly, add login fixtures.
- If UI state is hard to assert, add Playwright helpers.
- If setup fails repeatedly, add deterministic seed commands.
- If regressions are visual, add screenshot or DOM assertions.
- If acceptance depends on manual judgment, define executable probes.

## Minimal Architecture

```text
c3x CLI
  add
  run
  status
  answer
  start
  pause
  resume
  verify
  land
  cleanup

Beads ledger
  raw ideas
  questions
  tasks
  dependencies
  assignments
  blockers
  review state
  critic findings

Codex skills
  flow-architect
  flow-worker
  flow-reviewer
  flow-critic
  flow-verify

Git
  branch per task
  worktree per task
  automatic merge after review

Spool files
  worker outputs
  logs
  proposed follow-up tasks
  test artifacts
```

The first useful version is a single blocking `c3x run` process that supervises Beads, launches constrained Codex workers in worktrees, serializes ledger writes, and alerts the user only when human judgment is needed.
