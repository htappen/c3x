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

## Result

Always emit:

```json
{
  "task_id": "task-id",
  "status": "completed",
  "summary": "What changed",
  "changed_files": [],
  "verification": [],
  "blockers": [],
  "proposed_tasks": [],
  "scope_expansion": []
}
```

Use `blocked` when setup, scope, missing context, or verification prevents safe completion.

