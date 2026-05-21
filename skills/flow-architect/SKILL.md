---
name: flow-architect
description: Use when refining raw c3x inbox ideas into actionable Beads tasks with acceptance criteria, dependencies, and verification expectations.
---

# Flow Architect

Convert vague feedback into small, testable Beads tasks.

## Workflow

1. Read c3x inbox items and any human answers.
2. Ask a question if the user-facing outcome, scope, or verification path is unclear.
3. Create tasks only when they can be assigned to one worker with a narrow file scope.
4. Include acceptance criteria and verification expectations in the task description.
5. Prefer spikes for unknown implementation areas.
6. Avoid bundling unrelated product changes, test harness work, and refactors.

## Task Shape

Each ready task should state:

- Desired behavior.
- Relevant files or areas when known.
- Acceptance criteria.
- Minimum verification.
- Known blockers or dependencies.

If a task cannot be verified automatically, create a verification-improvement task first or explicitly record the gap.

