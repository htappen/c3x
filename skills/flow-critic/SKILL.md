---
name: flow-critic
description: Use when analyzing repeated c3x worker failures, blocked tasks, slow tasks, or review rejections to create improvement tasks.
---

# Flow Critic

Find systemic friction in the agent workflow.

Use `flow-shared` communication policy for findings and improvement tasks.

## Look For

- Repeated setup failures.
- Missing seed data or fixtures.
- Flaky verification.
- Repeated scope expansion requests.
- Long-running tasks with similar causes.
- Review failures caused by missing evidence.
- High retry counts before landing.
- Repeated `blocker_category` values in worker results.
- Tasks with `unfinished` entries or low confidence.

Create concrete improvement tasks with acceptance criteria. Do not produce vague findings like "improve tests".
