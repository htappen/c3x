# Flow Mode Testing Guidelines

These guidelines cover testing `flow-mode` itself: the CLI, supervisor loop, Beads integration, worktree orchestration, agent lifecycle, result ingestion, review gates, merge behavior, and recovery paths.

The verification skill for downstream projects is covered separately in `skills/flow-verify/reference/verify-skill-guidelines.md`.

## Goals

- Prove the supervisor never corrupts task state.
- Prove only the supervisor writes to Beads.
- Prove workers run in isolated branches and worktrees.
- Prove result ingestion is deterministic and schema-validated.
- Prove review and merge gates are conservative.
- Prove interrupted runs can resume safely.
- Prove the terminal loop surfaces questions, blockers, and agent status clearly.

## Test Pyramid

`flow-mode` should bias toward fast deterministic tests first, then integration tests around real git and Beads behavior, then a small number of end-to-end CLI flows.

### Unit Tests

Unit tests should cover pure logic and command planning:

- Config loading and defaults.
- Path resolution for `.flow/`.
- Task state classification.
- Beads JSON parsing.
- Result schema validation.
- Branch and worktree name generation.
- Prompt input assembly.
- Scope validation.
- Verification command planning.
- Review gate decisions.
- Critic pattern classification.

These tests should not call `git`, `bd`, or `codex`.

### Integration Tests

Integration tests should use temporary directories and real command execution for local system boundaries:

- Initialize `.flow/` in a temporary repo.
- Shell out to real `bd` against isolated temporary repositories.
- Create and remove git branches and worktrees.
- Import worker result files.
- Handle malformed result files.
- Detect stale agent process metadata.
- Recover from interrupted runs.
- Refuse merge on failed verification.

Use fakes where external behavior is not the subject of the test. Use real git where worktree behavior is the subject.

### End-To-End CLI Tests

End-to-end tests should exercise the user-facing workflow:

```bash
c3x init
c3x add "change login redirect behavior"
c3x status
c3x answer <question-id> "preserve query params"
c3x start <task-id>
c3x review <task-id>
c3x land <task-id>
```

Early E2E tests should use fake agents rather than real Codex calls. Real Codex-based tests should be optional and never required for the default local test suite.

## Required Test Fixtures

`flow-mode` needs test fixtures that make orchestration behavior reproducible:

- Empty git repo.
- Repo with an existing dirty worktree.
- Repo with a simple web app skeleton.
- Repo with existing `.flow/config.yml`.
- Repo with existing Beads data.
- Fake `bd` command that records calls and returns JSON.
- Fake `codex` command that writes controlled `result.json` files.
- Fake worker that succeeds.
- Fake worker that fails verification.
- Fake worker that writes malformed output.
- Fake worker that times out.
- Fake worker that requests scope expansion.

The fake commands should be small executables placed at the front of `PATH` during tests.

## Beads Adapter Tests

The Beads adapter is a critical boundary and should be tested without assuming Beads internals.

Test cases:

- Builds expected `bd` commands.
- Parses valid `bd --json` output.
- Handles empty lists.
- Handles command failures.
- Handles invalid JSON.
- Handles missing `bd` executable.
- Preserves unknown metadata fields.
- Serializes writes through the supervisor.
- Refuses worker-originated direct ledger writes.

The adapter should have contract tests against real `bd` running only in isolated temporary repositories.

## Worktree Lifecycle Tests

Worktree orchestration should be tested against real git in temporary directories.

Test cases:

- Creates branch and worktree for a task.
- Refuses to create duplicate worktree for the same task unless resuming.
- Refuses unsafe branch names.
- Detects dirty main worktree when required.
- Allows dirty worker worktree to remain for review.
- Cleans up worktree after successful merge.
- Preserves worktree on failed review.
- Handles merge conflicts without losing worker changes.
- Recovers from a missing worktree directory.
- Recovers from a branch that already exists.

## Supervisor Loop Tests

The supervisor loop should be testable without sleeping in real time.

Requirements:

- Inject a fake clock.
- Inject fake process runners.
- Run one deterministic tick at a time.
- Record emitted events.
- Avoid real terminal control in core loop tests.

Test cases:

- Picks ready tasks up to `max_parallel_workers`.
- Does not dispatch blocked tasks.
- Does not dispatch tasks with pending questions.
- Imports completed worker results.
- Marks timed-out workers as blocked.
- Runs critic pass on schedule.
- Emits question notifications.
- Persists enough state to resume after restart.

## Agent Launch Tests

Agent launch tests should verify command construction and isolation, not model behavior.

Test cases:

- Worker prompt contains exactly one task.
- Worker prompt includes owned scope.
- Worker prompt includes relevant verification requirements.
- Worker prompt excludes unrelated ledger data.
- Worker process runs in the task worktree.
- Worker environment contains expected constraints.
- Worker logs are captured under `.flow/runs/<task-id>/`.
- Worker cannot overwrite another task's run directory.

Use fake `codex` for default tests. Real Codex invocation should be opt-in.

## Result Ingestion Tests

The supervisor should treat worker output as untrusted input.

Test cases:

- Accepts valid completed result.
- Accepts valid blocked result.
- Accepts valid scope-expansion request.
- Rejects malformed JSON.
- Rejects missing task id.
- Rejects task id mismatch.
- Rejects result for unassigned task.
- Rejects changed files outside worktree.
- Records verification evidence.
- Converts proposed follow-up tasks into Beads writes.
- Does not import the same result twice.

## Review And Merge Tests

Review and merge gates should fail closed.

Test cases:

- Blocks merge when required checks did not run.
- Blocks merge when verification failed.
- Blocks merge on scope violation.
- Blocks merge when acceptance criteria are unresolved.
- Blocks merge on merge conflict.
- Allows merge after reviewer approval and passing checks.
- Updates Beads state after merge.
- Leaves branch/worktree available when merge fails.
- Cleans up branch/worktree only after successful landing.

## Critic Tests

The critic should produce specific improvement tasks from repeated failures.

Test cases:

- Detects repeated setup failures.
- Detects repeated missing fixture failures.
- Detects repeated flaky verification.
- Detects repeated scope expansion requests.
- Detects long-running task patterns.
- Avoids duplicating existing critic tasks.
- Produces concrete acceptance criteria.
- Does not block urgent product tasks unless policy allows it.

## Terminal UI Tests

The terminal UI should be thin over testable state and events.

Test cases:

- Renders counts for inbox, questions, ready, running, reviewing, blocked, and done.
- Highlights pending questions.
- Emits terminal bell when configured.
- Suppresses terminal bell when configured.
- Shows recent agent events.
- Handles narrow terminal width.
- Exits gracefully on interrupt.
- Resumes state after restart.

Prefer snapshot tests for rendered text only after the structure stabilizes. Core behavior should be tested through events and state transitions.

## Failure Recovery Tests

Recovery is central because `flow-mode` will manage long-running autonomous work.

Test cases:

- Supervisor interrupted while worker is running.
- Worker exits without result file.
- Worker writes partial result file.
- Worktree deleted while task is running.
- Branch deleted while task is running.
- Beads command fails during result import.
- Merge succeeds but Beads update fails.
- Beads update succeeds but cleanup fails.
- User resumes after process restart.

Recovery commands should be tested before parallel workers are enabled.

## Test Data And Isolation

Tests should not depend on the developer's real Beads database, git config, Codex auth, network, or global shell state. Real `bd` is allowed for integration tests, but it must operate only on temporary test data.

Guidelines:

- Use temporary directories for each test.
- Use `scripts/validate-setup.sh` and `scripts/validate-teardown.sh` for manual validation projects.
- Source `.tmp/validation/env` before running tools that write to `$HOME`, including `bd`.
- Override `HOME` where necessary.
- Override `PATH` for fake commands.
- Avoid network in default tests.
- Avoid real Codex in default tests.
- Use real `bd` only against isolated temporary repos.
- Keep generated `.flow/` data inside the temp repo.
- Keep manual validation state under `.tmp/validation`.
- Clean up processes started during tests.
- Make tests safe to run repeatedly.

## CI Expectations

The default CI suite should run without secrets or external services.

Required checks:

- Format.
- Lint.
- Typecheck if applicable.
- Unit tests.
- Integration tests using real `bd` and fake `codex`.
- Git worktree integration tests.

Optional checks:

- Real Codex smoke tests.
- Longer end-to-end autonomous runs.

## Current Decisions

- The CLI will be implemented in Python.
- Default Beads integration tests should use real `bd`.
- Default Codex tests should not call a real model-backed agent.
- Git worktree tests are integration tests, not unit tests.
- If merge succeeds but Beads update fails, cleanup should block and require explicit repair.
- Terminal UI tests should use both event/state assertions and snapshots.

## Codex Test Double

The CLI should launch Codex through a configurable command path, not by hardcoding a binary invocation deep inside the supervisor. Tests can then point that command at a fake executable.

Example config shape:

```yaml
agents:
  codex_command: codex
```

In tests:

```yaml
agents:
  codex_command: tests/fixtures/fake-codex
```

The fake Codex command should:

- Record argv, environment, working directory, and stdin.
- Assert it was launched from the expected worktree.
- Read the generated prompt if passed by file.
- Write a controlled `.flow/runs/<task-id>/result.json`.
- Exit with configurable status codes.
- Support modes for success, blocked, malformed output, timeout, and scope expansion.

This verifies that the `flow` CLI builds prompts, creates worktrees, launches processes, captures logs, and ingests results correctly without sending anything to a real agent. Real Codex smoke tests should remain opt-in because they require credentials, model availability, network behavior, and non-deterministic agent output.

## Open Questions

These decisions should be answered before implementing the test harness:

- Should the first implementation optimize for simple shell-based E2E tests or language-native integration tests?
- Which Python test stack should be used: `pytest` only, or `pytest` plus helpers like `pytest-console-scripts`?
- Should real `bd` tests install or bootstrap Beads automatically, or fail fast with a clear missing-dependency message?
- Should fake Codex be a simple script fixture or a reusable Python test harness module?
- What should the explicit repair command be called after partial merge failure: `c3x repair`, `c3x reconcile`, or something narrower?
