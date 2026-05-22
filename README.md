# c3x

`c3x` is a local, terminal-first supervisor for the `flow-mode` agentic coding workflow. It uses Beads as the task ledger, git worktrees for isolation, Codex-compatible worker commands for implementation, and structured run files under `.flow/` for review, landing, and metrics.

The current implementation is a practical thin slice: it can capture feedback, plan inbox items into tasks, start workers, import worker results, review and land branches, run verification commands, and summarize agent outcomes.

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

Add raw feedback:

```bash
c3x add "checkout page flashes empty cart on refresh"
```

Let the architect pass convert inbox feedback into ready work:

```bash
c3x run --once
c3x status
```

Start a ready task:

```bash
c3x start <task-id>
```

`c3x` creates a branch, creates a worktree, writes a worker prompt, launches the configured Codex-compatible command, and waits for `.flow/runs/<task-id>/result.json`.

Import completed worker output:

```bash
c3x run --once
c3x agents
```

Review and land:

```bash
c3x review <task-id>
c3x land <task-id>
c3x cleanup <task-id>
```

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

This is not yet a full autonomous daemon. Real Codex invocation should be validated in your environment, and the supervisor loop is intentionally simple. The next hardening work is richer failure recovery, stronger worktree cleanup, and more complete Beads metadata analysis.
