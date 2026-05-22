# Flow Mode Testing Guidelines

`flow-mode` should treat testing as an orchestration requirement, not just a worker habit. Every task should have an explicit verification path, every worker should produce evidence, every review should gate on that evidence, and repeated failures should become tasks that improve the project test harness.

## Principles

- Prefer executable verification over prose confidence.
- Make the cheapest useful checks run first.
- Require deterministic setup for autonomous agents.
- Capture artifacts whenever a failure needs interpretation.
- Treat missing testability as a first-class task.
- Let the critic convert repeated friction into fixtures, helpers, docs, or new checks.
- Do not merge work solely because an agent says it manually inspected behavior.

## Verification Contract

Every project should have a `FLOW.md` verification contract or equivalent `.flow/config.yml` section. The contract tells agents how to prove changes work without relying on hidden human context.

Minimum contract:

```markdown
# Flow Verification

## Baseline
npm run typecheck
npm run lint
npm test

## Web
npm run test:e2e

## Setup
npm install
npm run seed:test

## Artifacts
E2E traces, screenshots, and videos are written to test-results/.

## Known Gaps
List areas that still require manual judgment.
```

The contract should define:

- Required baseline checks.
- Optional task-specific checks.
- Local setup commands.
- Test data and seed commands.
- Required environment variables.
- Artifact paths.
- Known verification gaps.
- Commands that are safe for sandboxed workers.

## Check Tiers

Use tiers so agents can run fast checks while iterating and stronger checks before review.

### Tier 0: Static And Fast

Run before or during implementation:

- Format check.
- Typecheck.
- Lint.
- Focused unit tests.
- Schema validation.

### Tier 1: Functional

Run after implementation:

- Full unit test suite.
- Integration tests.
- Component tests.
- API contract tests.
- Task-specific regression tests.

### Tier 2: User Flow

Run before review for user-visible web changes:

- Playwright or equivalent E2E tests.
- Authenticated browser flows.
- Seeded-data scenarios.
- Accessibility smoke checks.
- Screenshot or DOM assertions when useful.

### Tier 3: Release Confidence

Run before automatic merge when the change is broad or risky:

- Full project verification.
- Cross-browser E2E.
- Migration checks.
- Build verification.
- Smoke test of production-like bundle.

## Task Verification Requirements

The architect should not mark a task ready unless it has a verification expectation.

Each task should specify:

- Expected behavior.
- Acceptance criteria.
- Files or areas likely in scope.
- Minimum check tier.
- Specific commands to run when known.
- Required artifacts for UI or hard-to-debug behavior.
- Known risks or gaps.

Example:

```yaml
verification:
  minimum_tier: 2
  commands:
    - npm run typecheck
    - npm run test:e2e -- cart-refresh.spec.ts
  artifacts:
    - test-results/cart-refresh/trace.zip
  acceptance:
    - Cart persists after refresh.
    - Empty-cart fallback does not flash before hydration completes.
```

## Worker Evidence

Every worker result must include test evidence. If the worker cannot run verification, it must say why and classify the failure.

`result.json` should include:

```json
{
  "task_id": "bd-123",
  "status": "completed",
  "changed_files": [],
  "verification": {
    "commands": [
      {
        "command": "npm run typecheck",
        "status": "passed",
        "duration_seconds": 12,
        "log_path": ".flow/runs/bd-123/logs/typecheck.log"
      }
    ],
    "artifacts": [],
    "not_run": [],
    "known_gaps": []
  }
}
```

When checks fail, workers should classify the failure:

- Product bug found.
- Test expectation is stale.
- Environment/setup failure.
- Flaky test.
- Missing fixture or seed data.
- Scope too broad.
- Permission or sandbox limitation.
- Unknown blocker.

The classification lets the critic find recurring patterns.

## Review Gates

The reviewer should block automatic merge when:

- Required verification did not run.
- Verification failed.
- The worker changed files outside owned scope without approval.
- Acceptance criteria are not directly addressed.
- UI changes lack user-flow coverage or an explicit reason.
- Test data setup is manual or undocumented.
- The result relies on unverifiable manual inspection.
- Logs or artifacts are missing for failures.

The reviewer may allow merge when:

- Required checks pass.
- The changed files match the approved scope.
- Acceptance criteria are satisfied.
- Known gaps are documented as follow-up tasks.
- Any skipped checks are justified and non-blocking for the task risk.

## Web App Defaults

For web apps, default verification should bias toward user-observable behavior.

Recommended baseline:

```bash
npm run typecheck
npm run lint
npm test
npm run test:e2e
```

Recommended project conventions:

- Deterministic seed data for E2E.
- Stable selectors or accessible role-based queries.
- Auth/session helpers.
- Resettable local database or mocked service layer.
- Captured Playwright traces on failure.
- Screenshots or videos for visual regressions.
- Test helpers for common user journeys.
- Clear separation between unit, integration, and E2E commands.

The architect should ask for user-facing acceptance criteria when a task affects UI. The critic should create testability tasks when agents repeatedly rely on manual browser inspection.

## Critic Inputs

The critic should periodically inspect:

- Blocked tasks.
- Long-running tasks.
- Failed verification commands.
- Review rejections.
- Repeated edits to the same test setup files.
- Repeated environment failures.
- Repeated scope expansion requests.
- Tasks completed without meaningful tests.
- UI tasks that required manual judgment.

The critic should produce concrete improvement tasks, not vague warnings.

Good critic output:

```text
Create a seeded checkout fixture covering signed-in and guest carts.
Reason: 4 cart tasks failed or stalled because agents could not create stable checkout state.
Acceptance: `npm run test:e2e -- checkout-seed.spec.ts` passes and workers can reuse `createCheckoutFixture()`.
```

Bad critic output:

```text
Improve tests.
```

## New Project Bootstrap

When `flow-mode` is initialized in a new project, the architect should assess test readiness before dispatching product work.

Bootstrap checklist:

- Detect package manager and test commands.
- Detect app type and framework.
- Detect existing unit, integration, and E2E tests.
- Detect lint, typecheck, and build commands.
- Detect local setup requirements.
- Detect required environment variables.
- Detect seed data strategy.
- Create or update `FLOW.md`.
- Create tasks for missing verification basics.

Suggested initial tasks for a web app:

- Add baseline `c3x verify` command.
- Add deterministic test seed/reset.
- Add one smoke E2E test for app startup.
- Add auth helper if login exists.
- Add artifact capture for E2E failures.
- Document local environment setup.

## Merge Policy

Automatic merge should require passing verification unless the task is explicitly documentation-only or marked as a spike.

Default policy:

- Documentation-only tasks require markdown/link checks when available.
- Code tasks require at least Tier 0 and task-specific checks.
- UI behavior tasks require Tier 2 unless explicitly waived.
- Broad refactors require Tier 3.
- Spikes do not merge product code unless converted into implementation tasks.

Waivers should be explicit and visible in Beads. A waiver should include the reason, risk, and follow-up task if the gap matters.

## Open Questions

These defaults need user decisions before implementation:

- Which checks should be mandatory for all web app code changes?
- Should UI tasks always require E2E coverage, or only when the architect marks them user-facing?
- Should automatic merge be blocked by known flaky tests?
- Should the system support temporary verification waivers?
- Should the critic be allowed to create high-priority infrastructure tasks automatically?
- How much time should workers spend trying to fix test infrastructure before marking a blocker?
