# Plan — TASK-20260803-refresh-migration-evidence

## Current-state findings

- The two migration documents preserve the pre-review state.
- `.ai/workflow.yaml` is already `operational`.
- The completed smoke task contains the review verdict, remediation, final
  verification, and state transitions needed to update the migration record.
- Release readiness remains separately blocked by external evidence.

## Assumptions

- `TASK-20260730-contributor-workflow-docs` is the authoritative smoke-task
  record.
- No rerun can retroactively change the historical 16/117 results; current
  verification will be recorded separately in this task.

## Option A

Update both migration documents and clearly separate historical completion from
remaining release-readiness gates.

## Option B

Delete the stale migration documents or replace them with links to the smoke
task.

## Decision and rationale

Choose Option A. The migration directory is a durable entrypoint and should be
accurate without forcing readers to reconstruct status from another task.

## Files, interfaces, data and configuration affected

- Documentation only: `.ai/migration/evidence.md`,
  `.ai/migration/handoff.md`, and this task directory.
- No CLI, schema, package, configuration, test, or runtime change.

## Test matrix

| Case | Expected result | Verification |
|---|---|---|
| Happy path | Both migration docs report `operational` | focused `rg` and diff review |
| Boundary | Release gates stay blocked | focused `rg` plus full verification |
| Invalid input | No unsupported status claim | audit plus source cross-check |
| Failure/retry | Claude unavailability is not described as current | focused stale-wording scan |
| Compatibility/regression | Product verification remains unchanged | quick and full verification |
| Security/privacy | No secret/raw/path leakage | diff review and audit |
| Performance/resource bound | No runtime behavior | changed-file allowlist |

## Rollback and observability

Rollback is a documentation-only revert. Git diff, audit, focused wording
searches, and quick/full verification provide observability.

## Acceptance-criteria mapping

| Criterion | Implementation location | Verification command/evidence |
|---|---|---|
| Operational status | both migration docs | source cross-check + `rg` |
| Completed review/smoke facts | both migration docs | completed task artifacts |
| Release gates still blocked | both migration docs | full verification |
| No behavior/protected-path change | changed-file allowlist | `git diff --name-only` |
| Deterministic verification | task evidence | audit/quick/full/diff checks |

## Plan challenge

### BLOCKING

- None yet.

### NON-BLOCKING

- None yet.

### ASSUMPTIONS TO VERIFY

- Claude remains available for this task's independent review.

### SUGGESTED CHANGES

- None yet.

## Approval

- Status: APPROVED
- Owner: human
- Date: 2026-08-03
