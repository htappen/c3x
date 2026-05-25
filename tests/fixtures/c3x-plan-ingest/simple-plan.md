# Widget Import Plan

## Goal

Build a three-step widget import flow.

## Step 1: Parse Uploads

- Add a CSV parser for widget rows.
- Reject rows missing `sku` or `name`.
- Test with valid and invalid fixture files.

## Step 2: Preview Changes

- Show created and updated widget counts before saving.
- Include row-level validation errors in the preview.
- Add a CLI smoke test for the preview command.

## Step 3: Commit Import

- Save accepted rows only after preview succeeds.
- Write an import manifest with timestamp, source path, and accepted row count.
- Add an integration test using a temporary database.
