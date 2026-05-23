# c3x

`c3x` is a local, terminal-first supervisor for the `flow-mode` agentic coding workflow. It uses Beads as the task ledger, git worktrees for isolation, Codex-compatible worker commands for implementation, and structured run files under `.flow/` for review, landing, and metrics.

The current implementation is a practical thin slice: it can capture feedback, plan inbox items into tasks, start workers, import worker results, retry blocked work, review and land branches, clean stale attempts, run verification commands, and summarize agent outcomes.

## Install

From this repository:

```bash
scripts/setup.sh
. .venv/bin/activate
```

The setup script installs the Beads `bd` CLI if needed and installs `c3x` from `pyproject.toml` with development dependencies.

## Initialize A Project

Run this inside the repo you want `c3x` to manage:

```bash
c3x init
```

This creates:

- `.flow/config.yml`
- `.flow/runs/`
- `.flow/agents/`
- `.flow/worktrees/`
- a project-local Beads database via `bd init`

## Tutorial

Start the autonomous watcher in one terminal:

```bash
c3x watch
```

`c3x watch` imports completed worker results, plans clarified feedback, dispatches ready work, reviews completed work, lands reviewed branches into the current root branch, and cleans up landed worktrees.

In another terminal, add raw feedback:

```bash
c3x add "checkout page flashes empty cart on refresh"
```

By default, `c3x add` validates the feedback synchronously. If the feedback is too vague, it creates a durable human-clarification question, asks for an answer in the terminal, records that answer, closes the question, and then continues planning.

Use fire-and-forget mode when you want the watcher to pick it up later:

```bash
c3x add --no-validate "checkout page flashes empty cart on refresh"
```

Find or resume outstanding clarification:

```bash
c3x questions
c3x clarify
c3x answer <question-id> "preserve all cart items after reload"
```

Manual commands remain available when you want to step through the pipeline:

```bash
c3x run --once
c3x status
c3x agents
c3x start <task-id>
c3x retry <task-id>
c3x retry --all
c3x squash <task-id>
c3x squash --all
c3x review <task-id>
c3x land <task-id>
c3x cleanup
```

`c3x` creates a branch, creates a worktree, writes a worker prompt, launches the configured Codex-compatible command, waits for the worker result under the worktree `.c3x/result.json`, copies it into `.flow/runs/<task-id>/result.json`, reviews the result, merges the branch, and removes landed or superseded stale worktrees.

## Command Reference

Top-level help:

```bash
c3x --help
```

Commands:

| Command | Help text |
| --- | --- |
| `c3x init` | Initialize c3x metadata and a project-local Beads ledger. |
| `c3x add` | Add raw feedback to the Beads-backed c3x inbox. |
| `c3x inbox` | Show open c3x inbox items. |
| `c3x status` | Show the current c3x project status. |
| `c3x answer` | Record a human answer on a question bead. |
| `c3x questions` | Show outstanding human clarification questions. |
| `c3x clarify` | Answer outstanding human clarification questions in a terminal chat loop. |
| `c3x run` | Run the c3x supervisor loop. |
| `c3x watch` | Run the autonomous c3x watch loop. |
| `c3x start` | Start one worker in an isolated git worktree. |
| `c3x retry` | Start a fresh worker attempt for blocked or stale work. |
| `c3x squash` | Squash c3x-generated commits for landed work. |
| `c3x agents` | List known local worker runs. |
| `c3x metrics` | Summarize agent outcomes, retries, unfinished work, and blockers. |
| `c3x verify` | Run configured project verification commands. |
| `c3x review` | Review a completed worker result and mark it ready to land. |
| `c3x land` | Merge a reviewed task branch and close the bead. |
| `c3x cleanup` | Remove landed task worktrees and superseded stale attempts. |
| `c3x pause` | Pause supervisor dispatch/import loops. |
| `c3x resume` | Resume supervisor dispatch/import loops. |
| `c3x critic` | Create improvement tasks from repeated blocked work. |

Use command-specific help for full argument and option details:

```bash
c3x <command> --help
```

### Project Setup

```bash
c3x init
c3x init --skip-beads
```

- `init`: create `.flow/` directories, write default config, and initialize the project-local Beads database.
- `--skip-beads`: write c3x config/directories without running `bd init`.
- Help: `c3x init --help`

### Feedback And Planning

```bash
c3x add "task description"
c3x add --description "details" --priority 1 "task description"
c3x add --no-validate "task description"
c3x inbox
c3x questions
c3x clarify
c3x answer <question-id> "answer text"
```

- `add TITLE`: create an inbox item and, by default, validate it into a planned task or clarification question.
- `add --description, -d TEXT`: add optional detail for the inbox item.
- `add --priority, -p 0..4`: set Beads priority, where `0` is highest. Default: `2`.
- `add --validate/--no-validate`: validate synchronously and ask clarification questions before returning. Default: `--validate`.
- `inbox`: list open flow inbox items.
- `questions`: list open human-clarification questions.
- `clarify`: answer outstanding questions interactively.
- `answer`: record an answer for one question and unblock the related item.
- Help: `c3x add --help`, `c3x answer --help`

### Supervisor Loops

```bash
c3x run
c3x run --once
c3x run --dispatch
c3x run --interval 10
c3x status
c3x watch
c3x watch --no-review
c3x watch --no-land
c3x watch --no-cleanup
c3x watch --interval 10
c3x pause
c3x resume
```

- `run`: import finished worker results, plan inbox work, and run critic checks in a loop.
- `run --once`: perform one supervisor tick and exit. Default: loop until interrupted.
- `run --dispatch`: also start ready tasks up to the configured parallel worker limit. Default: no dispatch.
- `run --interval N`: set loop sleep seconds. Default: `5`.
- `status`: show current inbox, ready, running, reviewing, blocked, and done counts.
- `watch`: autonomous loop; dispatches, reviews, lands, and cleans landed work by default.
- `watch --interval N`: set loop sleep seconds. Default: `5`.
- `watch --review/--no-review`: automatically review completed worker results. Default: `--review`.
- `watch --land/--no-land`: automatically land reviewed work into the current root branch. Default: `--land`.
- `watch --cleanup/--no-cleanup`: automatically remove landed worktrees and branches. Default: `--cleanup`.
- `pause` / `resume`: create or remove the `.flow/paused` marker used by supervisor loops.
- Help: `c3x run --help`, `c3x watch --help`

### Workers And Attempts

```bash
c3x start <task-id>
c3x retry <task-id>
c3x retry --all
c3x squash <task-id>
c3x squash --all
c3x agents
```

- `start TASK_ID`: start one worker in an isolated git worktree.
- `retry [TASK_ID]`: archive the current run directory, clear blocked/review labels, and start a fresh attempt using the current agent config.
- `retry --all`: reconcile stale runs, then retry all currently blocked flow tasks. Mutually exclusive with `TASK_ID`.
- `squash [TASK_ID]`: squash the c3x-generated commits for one landed task when that task is the current branch tip.
- `squash --all`: squash the eligible landed task segment at the current branch tip without naming the task. Mutually exclusive with `TASK_ID`.
- `agents`: list known run records, statuses, PIDs, and branches.
- Help: `c3x start --help`, `c3x retry --help`, `c3x squash --help`, `c3x agents --help`

Retry creates attempt-specific branches/worktrees after the first attempt, for example:

```text
c3x/<task-id>-short-title-attempt-2
.flow/worktrees/c3x-<task-id>-short-title-attempt-2/
```

Archived run evidence is preserved under names like:

```text
.flow/runs/<task-id>-attempt-1/
```

Squash rewrites local git history. It requires a clean root worktree, ignores `.flow/` generated state, and refuses non-tip task history instead of attempting a broad rebase. It combines c3x task commits such as worker commits, `Merge c3x/...`, `Close c3x task ...`, and `Checkpoint c3x ledger before merge` into one `Complete c3x task <task-id>` commit.

### Review, Landing, And Cleanup

```bash
c3x review <task-id>
c3x land <task-id>
c3x land <task-id> --no-cleanup
c3x cleanup
c3x cleanup <task-id>
c3x cleanup --dry-run
c3x cleanup --force
```

- `review TASK_ID`: validate a completed worker result and mark it reviewed.
- `land TASK_ID`: merge a reviewed branch into the current root branch, close the Beads task, and remove the landed worktree/branch.
- `land --cleanup/--no-cleanup`: remove the landed worktree and branch after merge. Default: `--cleanup`.
- `cleanup`: remove landed task worktrees/branches and archived attempts superseded by later completed, reviewed, or landed attempts.
- `cleanup`: also detects tasks marked landed whose branch is not merged, shows commit/diff/status context, and asks before repairing with a merge.
- `cleanup [TASK_ID]`: cleanup candidates globally or only for one task.
- `cleanup --dry-run`: show cleanup candidates without deleting anything.
- `cleanup --force`: force-remove dirty stale worktrees and unmerged stale branches.
- Help: `c3x review --help`, `c3x land --help`, `c3x cleanup --help`

Cleanup intentionally does not remove active `completed` or `reviewing` worktrees, because those still hold code that may need review or landing.

### Verification And Metrics

```bash
c3x verify
c3x verify "pytest"
c3x metrics
c3x metrics --json
c3x critic
scripts/c3x-metrics /path/to/project
```

- `verify`: run configured verification commands from `.flow/config.yml`.
- `verify COMMAND...`: run an ad hoc verification command.
- `metrics`: summarize run outcomes, retries, unfinished work, task kinds, blocker categories, and attempts to land.
- `metrics --json`: emit machine-readable JSON.
- `critic`: create improvement tasks when repeated blocked work indicates a workflow problem.
- Help: `c3x verify --help`, `c3x metrics --help`, `c3x critic --help`

Check agent effectiveness:

```bash
c3x metrics
c3x metrics --json
scripts/c3x-metrics /path/to/project
```

Metrics summarize run outcomes, rejected or blocked tasks, unfinished tasks, task kinds, blocker categories, and average attempts to land.

## Configuring Workers

Worker launch is configurable in `.flow/config.yml`:

```yaml
agents:
  codex_command: codex
  codex_args:
    - exec
    - --full-auto
    - --model
    - "{model}"
    - --cd
    - "{worktree}"
    - --output-last-message
    - "{last_message}"
    - "{prompt}"
```

Tests and smoke validation use `tests/fixtures/fake-codex` so CLI behavior can be verified without contacting a real model-backed agent.

## Verification

Run configured verification commands:

```bash
c3x verify
```

Or run an ad hoc command:

```bash
c3x verify "pytest"
```

Verification logs are written to `.flow/verify-logs/`.

## Validation

Use isolated validation projects instead of testing directly in the repo root:

```bash
PROJECT_DIR="$(scripts/validate-setup.sh)"
. .tmp/validation/env
cd "$PROJECT_DIR"
c3x init
cd -
scripts/validate-teardown.sh
```

Run the automated tests:

```bash
. .venv/bin/activate
pytest
```

## Skills

Codex role guidance lives in `skills/`:

- `flow-shared`
- `flow-architect`
- `flow-worker`
- `flow-reviewer`
- `flow-critic`
- `flow-verify`

Detailed downstream verification guidance lives at `skills/flow-verify/reference/verify-skill-guidelines.md`.

`flow-shared` carries the caveman-mode communication policy. c3x also injects that policy into worker prompts through `src/c3x/prompts/caveman_mode.md`, and `.codex/hooks.json` enables it for repo-local Codex sessions.

## Current Gaps

This is not yet a full autonomous daemon. Real Codex invocation should be validated in your environment, and the supervisor loop is intentionally simple. The next hardening work is richer Beads metadata analysis and more complete recovery policy controls.
