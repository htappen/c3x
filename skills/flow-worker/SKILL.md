---
name: flow-worker
description: Use when implementing exactly one c3x Beads task in an isolated git worktree and writing a structured worker result.
---

# Flow Worker

Complete one assigned task in the current worktree.

## Rules

- Work only on the assigned task.
- Investigate before editing.
- Stay inside the owned file scope unless you emit a scope-expansion request.
- Run the required verification commands when available.
- Do not write directly to Beads.
- Write the required `result.json` before exiting.
- Report honestly: use `blocked` or `failed` when the task is not solved.
- Classify blockers so c3x can learn from repeated failures.

## Result

Always emit:

```json
{
  "task_id": "task-id",
  "status": "completed",
  "summary": "What changed",
  "task_kind": "feature",
  "attempt": 1,
  "changed_files": [],
  "verification": [],
  "blockers": [],
  "blocker_category": null,
  "proposed_tasks": [],
  "scope_expansion": [],
  "confidence": "high",
  "unfinished": []
}
```

Use `blocked` when setup, scope, missing context, or verification prevents safe completion.

Allowed `task_kind` values: `feature`, `bug`, `test`, `refactor`, `docs`, `infra`, `spike`.

Recommended `blocker_category` values: `setup`, `missing-context`, `scope`, `verification`, `dependency`, `flaky-test`, `merge-conflict`, `unknown`.

If anything remains incomplete, list it in `unfinished` and lower confidence to `medium` or `low`.
