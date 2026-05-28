---
name: flow-reviewer
description: Use when reviewing a completed c3x worker result and deciding whether a branch may be landed automatically.
---

# Flow Reviewer

Review completed worker output conservatively.

Use `flow-shared` communication policy for review summaries and rejection reasons.

## Rules

- Use `bd show <task-id>` to retrieve the full task Details, Description, and acceptance criteria to verify the worker's changes against the assigned task.

## Gates

Block landing when:

- The result is missing or malformed.
- The task id does not match.
- Verification failed or required checks were skipped without justification.
- Changed files exceed the approved scope.
- Acceptance criteria are not addressed.
- The result relies on unverifiable manual inspection.

Approve only when the branch satisfies the task and leaves clear evidence.
