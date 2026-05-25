---
name: c3x-plan-ingest
description: Use when Codex needs to read implementation plans, design docs, or milestone docs in any project directory and populate that project's c3x inbox by running `c3x add` for small doc-backed tasks. Use for converting multi-step docs into c3x work items while preserving source doc paths, requirements, acceptance criteria, verification guidance, and unresolved questions for the c3x master/supervisor.
---

# C3x Plan Ingest

Turn planning docs into c3x inbox items from a normal Codex session.

## Inputs

Require:

- Target project directory where `c3x` should run.
- One or more plan/design doc paths or doc directories.

If either is missing, inspect the current request and workspace first. Ask the user only when the target directory or source docs cannot be inferred safely.

## Workflow

1. Check project state.
   - Run `git status --short` in the target project before edits or task creation.
   - Confirm `c3x` is available from that directory, for example with `c3x --help`.
   - Do not modify project files unless the user explicitly asks for implementation work.
2. Read docs.
   - List relevant Markdown files with `rg --files` or `find`.
   - Read overview, milestone, implementation-plan, architecture, protocol, requirements, and testing docs first.
   - Build a dependency-ordered outline from binding milestones, phases, prerequisites, and acceptance criteria.
3. Resolve ambiguities.
   - Ask follow-up questions for choices that cannot be answered from docs and would materially change task boundaries, ordering, verification, or ownership.
   - When c3x asks validation questions during `c3x add`, answer from the source docs when possible.
   - Redirect a c3x question to the user when docs do not contain a reliable answer.
4. Create task candidates.
   - Prefer tasks one worker can complete with a narrow file scope.
   - Preserve milestone order and explicit prerequisites.
   - Split spikes, protocol/schema work, harness work, rollout/storage work, Python loader/model work, CLI work, and verification work into separate items when they touch different areas.
   - Create verification-improvement tasks before implementation tasks when docs require behavior that cannot currently be tested.
   - Avoid creating tasks for out-of-scope or later-milestone items until prerequisite milestones are represented.
5. Add tasks with c3x.
   - From the target project directory, run plain `c3x add "<title>"`.
   - Do not use `--no-validate` unless the user explicitly changes this rule.
   - Provide detailed descriptions through the interactive prompt when c3x asks, or by answering validation prompts with doc-backed detail.
   - If c3x cannot accept a needed description interactively, use `c3x add "<title>" --description "<description>"`.

## Task Description Template

Each task description should include:

```text
Source docs:
- path/to/doc.md: relevant section or requirement
- path/to/other.md: relevant contract

Goal:
<one concrete outcome>

Requirements:
- <doc-backed requirement>
- <doc-backed requirement>

Acceptance criteria:
- <observable result>
- <observable result>

Verification:
- <command or test expectation from docs>
- <manual gap only if no executable check exists>

Dependencies:
- <task title or prerequisite doc condition, if any>

Notes for c3x master:
- <questions answered from docs, scope constraints, forbidden files, or context needed by workers>
```

Use source paths plus concise extracted requirements. Do not paste full docs unless the user asks or the exact wording is critical.

## Task Titles

Use imperative, specific titles:

- `Add training protocol schema package`
- `Implement legal action candidate encoder contract`
- `Add fixed-seed rollout artifact smoke test`

Avoid vague titles:

- `Training work`
- `Do phase 1`
- `Implement docs`

## Safety

- Do not write directly to Beads with `bd` for this workflow; use `c3x add`.
- Do not create duplicate tasks for requirements already present in `c3x inbox` or `c3x status`.
- Do not claim tasks were added unless command output confirms success.
- Report any skipped docs, unresolved questions, and failed `c3x add` calls.
