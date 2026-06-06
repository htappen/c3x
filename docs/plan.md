# Flow Mode Execution Plan

This plan builds `flow-mode` incrementally as a lightweight local supervisor. The first milestone should produce a useful personal workflow without requiring a daemon, Dolt server, web UI, or remote worker pool.

## Phase 0: Decisions And Project Skeleton

Outcome: a small CLI project with documented defaults.

Tasks:

- Choose implementation language for the `flow` CLI.
- Define `.flow/config.yml` schema for models, limits, paths, permissions, and verification commands.
- Define `.flow/` runtime directory layout.
- Define canonical task states and result schema.
- Add initial `FLOW.md` template.
- Use the `skill-creator` workflow to add initial Codex skill directories for architect, worker, reviewer, critic, and verify.

Acceptance criteria:

- `c3x init` can create the local `.flow/` structure.
- The repo has templates for config and verification contract.
- The architecture docs and plan match the generated skeleton.

## Phase 1: Beads Integration

Outcome: Beads becomes the durable task ledger.

Tasks:

- Add a Beads adapter that shells out to `bd --json`.
- Implement `c3x add`.
- Implement `c3x inbox`.
- Implement `c3x status`.
- Define item types: idea, question, epic, task, spike, bug, test, critic-finding.
- Define dependency conventions.
- Define labels or metadata for readiness, role, priority, and verification.

Acceptance criteria:

- Raw feedback can be added from the terminal.
- Existing ledger items can be listed in a stable machine-readable format.
- The supervisor can identify inbox, ready, running, blocked, reviewing, and done work.

## Phase 2: Architect Loop

Outcome: the system can refine raw ideas into actionable task graphs.

Tasks:

- Use the `skill-creator` workflow to author the `flow-architect` skill.
- Implement `c3x run` with an architect-only loop.
- Detect underspecified ideas and create questions.
- Implement `c3x answer <id> <answer>`.
- Convert answered ideas into epics, tasks, spikes, bugs, and tests.
- Add acceptance criteria and verification expectations to tasks.
- Refuse to mark tasks ready when scope or verification is unclear.

Acceptance criteria:

- User can add vague feedback.
- Architect asks follow-up questions.
- User answers in terminal.
- Architect creates ready tasks with acceptance criteria and dependencies.

## Phase 3: Worktree Dispatch

Outcome: one worker task can be launched automatically in an isolated branch and worktree.

Tasks:

- Implement branch naming.
- Implement worktree creation and cleanup.
- Generate worker prompts from task data, relevant context, file scope, and verification contract.
- Use the `skill-creator` workflow to author the `flow-worker` skill.
- Implement a worker result schema.
- Launch a sandboxed Codex worker process from `c3x start <task-id>`.
- Store prompts, logs, events, and results under `.flow/runs/<task-id>/`.
- Keep Beads writes centralized in the supervisor.

Acceptance criteria:

- `c3x start <task-id>` creates a branch and worktree.
- Worker receives one constrained task.
- Worker writes `result.json`.
- Supervisor imports the result into Beads.

## Phase 4: Parallel Local Agents

Outcome: multiple workers can run concurrently without corrupting ledger state.

Tasks:

- Implement `max_parallel_workers`.
- Implement task leasing or assignment state in Beads.
- Add process tracking under `.flow/agents/`.
- Add timeout handling.
- Add stale-agent detection.
- Add terminal status updates for running workers.
- Ensure only supervisor writes to Beads.

Acceptance criteria:

- `c3x run` can keep up to N workers active.
- Agents run in separate worktrees.
- Concurrent workers cannot write directly to the ledger.
- Timed-out or crashed agents become blocked tasks with useful evidence.

## Phase 5: Verification Contract

Outcome: every task has a repeatable way to prove correctness.

Tasks:

- Use the `skill-creator` workflow to author the `flow-verify` skill.
- Implement `c3x verify`.
- Read verification commands from `FLOW.md` or `.flow/config.yml`.
- Support baseline commands such as typecheck, lint, unit tests, and E2E.
- Capture logs and artifacts.
- Let tasks specify narrower verification commands when appropriate.
- Teach architect to create verification-improvement tasks when checks are missing.

Acceptance criteria:

- `c3x verify` runs project checks consistently.
- Verification output is captured into task evidence.
- Tasks without meaningful verification are flagged before dispatch or review.

## Phase 6: Review And Merge

Outcome: completed worker branches can be reviewed and merged automatically.

Tasks:

- Use the `skill-creator` workflow to author the `flow-reviewer` skill.
- Implement `c3x review <task-id>`.
- Check changed files against owned scope.
- Check acceptance criteria against worker result.
- Rerun verification when required.
- Implement `c3x land <task-id>`.
- Implement `c3x land --all` to merge all reviewed tasks dependency-first, then oldest-worktree-first, and continue after individual conflicts.
- Merge approved branches into the main worktree.
- Reopen tasks or create follow-ups when review fails.
- Clean up branches and worktrees after successful landing.

Acceptance criteria:

- Worker branches do not land without review.
- Scope violations block automatic merge.
- Verification failures block automatic merge.
- Successful tasks merge locally and update Beads state.

## Phase 7: Critic Loop

Outcome: the system learns from repeated agent failures.

Tasks:

- Use the `skill-creator` workflow to author the `flow-critic` skill.
- Track blocked tasks, long-running tasks, repeated test failures, review rejections, and setup failures.
- Implement periodic critic passes inside `c3x run`.
- Create critic findings in Beads.
- Convert findings into infrastructure, fixture, documentation, or testability tasks.
- Make critic tasks visible in status and planning.

Acceptance criteria:

- The critic identifies repeated failure patterns.
- The critic creates concrete improvement tasks.
- The architect can prioritize those tasks against product work.

## Phase 8: Terminal Operations Console

Outcome: `c3x run` feels like a usable blocking terminal app.

Tasks:

- Render live counts for inbox, questions, ready, running, reviewing, blocked, and done.
- Show recent agent events.
- Show pending questions prominently.
- Add terminal bell for questions.
- Add optional desktop notifications.
- Add pause and resume commands.
- Add graceful shutdown that preserves running state.

Acceptance criteria:

- User can leave `c3x run` open.
- The app clearly alerts when human input is needed.
- The app can resume cleanly after restart.

## Phase 9: Web-App Optimizations

Outcome: `flow-mode` becomes especially effective for web application repos.

Tasks:

- Add Playwright-oriented verification template.
- Add conventions for seeded test data.
- Add conventions for auth/session helpers.
- Add artifact capture for screenshots, traces, and videos.
- Teach architect to ask for user-facing acceptance criteria.
- Teach critic to detect recurring manual UI validation.

Acceptance criteria:

- New web apps get useful default verification scaffolding.
- UI-related tasks can require E2E checks.
- Repeated manual validation becomes automated test work.

## Phase 10: Hardening

Outcome: the workflow is reliable enough for everyday personal use.

Tasks:

- Add structured logging.
- Add dry-run mode.
- Add recovery commands for stuck runs.
- Add config validation.
- Add schema validation for worker results.
- Add tests for Beads adapter, worktree lifecycle, result import, and status computation.
- Document common failure modes and recovery steps.

Acceptance criteria:

- A failed worker or interrupted supervisor does not corrupt state.
- The user can recover stuck tasks.
- Core behavior has automated test coverage.

## Initial Build Order

The most useful thin slice is:

1. `c3x init`
2. `c3x add`
3. `c3x status`
4. `c3x run` with architect questions only
5. `c3x answer`
6. `c3x start <task-id>` for one worker
7. Worker `result.json` import
8. `c3x verify`
9. `c3x review`
10. `c3x land`

Parallel workers, critic analysis, and the richer terminal console should come after this loop works end to end for one task.
