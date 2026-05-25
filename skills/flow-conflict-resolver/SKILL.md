---
name: flow-conflict-resolver
description: Use when resolving a c3x merge conflict in an isolated resolver worktree and writing a structured worker result.
---

# Flow Conflict Resolver

Resolve one merge conflict for a c3x task in the current worktree.

Use `flow-shared` communication policy for summaries and blocker reports.

## Rules

- Inspect the conflicted files and both sides of the merge before editing.
- Preserve the original task intent and the current target branch behavior.
- Resolve only the merge conflict. Do not make unrelated changes.
- Do not write directly to Beads.
- Do not run `git commit`, `git push`, `git pull`, `git merge`, or branch cleanup.
- Run configured verification when available.
- Write the required `result.json` before exiting.
- Report honestly: use `blocked` if the conflict cannot be resolved safely.

## Checks

- Confirm there are no remaining conflict markers.
- Confirm `git diff --name-only --diff-filter=U` is empty.
- Confirm changed files are limited to the conflict resolution.
- Include verification commands and statuses in the result.

## Result

Always emit:

```json
{
  "task_id": "task-id",
  "status": "completed",
  "summary": "How the conflict was resolved",
  "task_kind": "merge-conflict",
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

Use `blocked` with `blocker_category` `merge-conflict` when a safe resolution is unclear. List unresolved files and the exact decision needed in `blockers` and `unfinished`.
