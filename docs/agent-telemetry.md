# Agent Telemetry

`c3x` tracks agent effectiveness through both Beads metadata and `.flow/runs` records.

## Beads Labels

Use labels for quick filtering:

- `flow`: managed by c3x.
- `running`, `reviewing`, `blocked`, `landed`: current workflow bucket.
- `attempt-N`: the current attempt number.
- `completed-by-agent`: worker reported successful completion.
- `rejected`: supervisor rejected the worker result.
- `blocker-<category>`: blocked reason, for example `blocker-setup` or `blocker-verification`.

## Worker Result Fields

Workers must write `result.json` with:

- `task_kind`: `feature`, `bug`, `test`, `refactor`, `docs`, `infra`, or `spike`.
- `attempt`: attempt number from the prompt/run.
- `status`: `completed`, `blocked`, or `failed`.
- `blocker_category`: required for blocked/failed work.
- `confidence`: `low`, `medium`, or `high`.
- `unfinished`: explicit list of incomplete work.

These fields let c3x answer:

- How many tasks are unfinished?
- How many tasks were blocked or rejected?
- What kinds of tasks fail most?
- Which blocker categories repeat?
- How many attempts does landing take?

## Analysis Commands

Human-readable summary:

```bash
c3x metrics
```

Machine-readable JSON:

```bash
c3x metrics --json
scripts/c3x-metrics /path/to/project
```

The script reads `.flow/runs/*/run.json` and `.flow/runs/*/result.json`, so it can analyze a project even when Beads is unavailable.
