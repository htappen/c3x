---
name: flow-verify
description: Use when defining or improving project verification contracts for c3x-managed web app and codebase tasks.
---

# Flow Verify

Define how agents prove work is correct.

## Contract

Prefer executable commands over prose confidence. A useful verification contract includes:

- Fast checks: format, lint, typecheck, focused tests.
- Functional checks: unit, integration, component, API tests.
- User-flow checks for web apps: Playwright or equivalent E2E.
- Deterministic setup and seed data.
- Artifact capture for failures.

When verification is missing, create a task to add it instead of treating manual inspection as sufficient.

## Detailed Guidance

Read `reference/verify-skill-guidelines.md` when defining a verification contract, reviewing test sufficiency, handling web-app flows, or creating testability improvement tasks.
