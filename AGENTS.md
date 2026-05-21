# Repository Guidelines

## Project Structure & Module Organization

This repository contains `c3x`, the planned Python CLI for the `flow-mode` local agentic coding workflow.

- `docs/architecture.md`: high-level system design and agent roles.
- `docs/plan.md`: phased implementation plan.
- `docs/testing-guidelines.md`: testing strategy for `flow-mode` itself.
- `docs/verify-skill-guidelines.md`: guidelines for the downstream verification skill.

Current and planned layout:

- `src/c3x/`: CLI and library code.
- `tests/`: unit, integration, and CLI tests.
- `tests/fixtures/`: fake `codex`, temp repo fixtures, and test data.
- `scripts/setup.sh`: installs Beads if needed and sets up the Python dev environment.

## Build, Test, and Development Commands

Set up local development with:

```bash
scripts/setup.sh
```

Key commands:

```bash
c3x init
c3x add "task description"
c3x inbox
c3x status
pytest
```

Use isolated validation projects when checking repository behavior:

```bash
PROJECT_DIR="$(scripts/validate-setup.sh)"
. .tmp/validation/env
cd "$PROJECT_DIR"
# run c3x validation commands here
cd -
scripts/validate-teardown.sh
```

## Coding Style & Naming Conventions

For documentation, use concise Markdown with sentence-case prose and descriptive headings. Keep examples shell-copyable and repository-specific.

For Python code, use 4-space indentation, typed functions where practical, and snake_case for modules, functions, variables, and test files. Keep orchestration logic separated from command execution so tests can inject fake `codex`, clocks, and process runners.

## Testing Guidelines

Follow `docs/testing-guidelines.md`. Unit tests should avoid real `git`, `bd`, and `codex`. Integration tests may use real `git` worktrees and real `bd` against isolated temporary repositories. Default tests must use a fake `codex` command and must not contact a real model-backed agent.

Prefer test names that describe behavior, for example:

```text
test_worker_result_rejects_task_id_mismatch
test_supervisor_blocks_cleanup_after_beads_update_failure
```

## Commit & Pull Request Guidelines

There is no existing commit history, so no project-specific commit convention has been established. Use short imperative commit messages, for example:

```text
Add flow-mode testing guidelines
```

Pull requests should include a clear summary, linked Beads task or issue when available, test evidence, and notes about any verification gaps. For UI or terminal-output changes, include before/after output snippets when useful.

## Agent-Specific Instructions

Do not overwrite user work. Before editing, check `git status --short`. Keep `docs/verify-skill-guidelines.md` focused on downstream project verification and `docs/testing-guidelines.md` focused on testing `flow-mode` itself.

Generated validation state belongs under `.tmp/validation`; do not create ad hoc temp projects in the repository root.

Automatically commit after finishing each major piece of work unless the user explicitly asks not to commit. Run the relevant tests first, stage only the intended files, and use a concise imperative commit message.
