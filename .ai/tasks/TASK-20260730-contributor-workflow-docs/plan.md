# Plan — TASK-20260730-contributor-workflow-docs

## Current-state findings

- `CONTRIBUTING.md` defines PR classes, stability notes, fixture/provider rules,
  and direct local checks.
- The installed workflow adds `AGENTS.md`, `.ai/tasks/`, `aiwf-task-state`, and
  `scripts/verify.sh`.
- Existing direct commands remain valid but the unified verify entrypoint should
  be the contributor-facing default.

## Assumptions

- The workflow binaries are available through the rollout-kit `bin/` directory.
- The section should stay concise and avoid repeating `AGENTS.md`.
- This R1 task uses the Owner-provided plan because Claude is not authenticated.

## Option A

Add a short `Dual-Agent Workflow` section before `Local Checks`.

## Option B

Create a separate contributor workflow document and link it.

## Decision and rationale

Choose Option A. Contributors need the mandatory entrypoint visible in the
existing guide; deeper policy remains in `AGENTS.md` and `docs/ai/`.

## Files, interfaces, data and configuration affected

- `CONTRIBUTING.md` only.
- No public CLI, schema, data, package, or configuration change.

## Test matrix

| Case | Expected result | Verification |
|---|---|---|
| Happy path | Contributor can create and route a task | Manual diff + exact commands |
| Boundary | R0/R1 still use risk-appropriate flow | Wording review |
| Invalid input | No invalid command shapes are documented | `--help`/`--list` evidence |
| Failure/retry | BLOCKED and review-pending states remain explicit | Wording review |
| Compatibility/regression | Existing PR/stability content is unchanged | `git diff` |
| Security/privacy | No secrets, raw logs, or personal paths | `rg` + audit |
| Performance/resource bound | No runtime behavior | Documentation-only diff |

## Rollback and observability

Revert the single `CONTRIBUTING.md` commit. Verification and Git diff provide
all required observability.

## Acceptance-criteria mapping

| Criterion | Implementation location | Verification command/evidence |
|---|---|---|
| Task source of truth | `CONTRIBUTING.md` workflow section | manual diff |
| Writer/Reviewer separation | same section | manual diff |
| Exact commands | same section | `scripts/verify.sh --list` |
| No behavior regression | no code touched | full verification |

## Plan challenge

### BLOCKING

- None yet.

### NON-BLOCKING

- None.

### ASSUMPTIONS TO VERIFY

- Claude authentication for final independent review.

### SUGGESTED CHANGES

- None yet.

## Approval

- Status: APPROVED
- Owner: human
- Date: 2026-07-30
