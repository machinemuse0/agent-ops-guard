---
id: TASK-20260803-refresh-migration-evidence
title: refresh migration evidence
type: docs
risk: R1
status: IN_REVIEW
status_before_block: null
owner: human
planner: codex
writer: codex
reviewer: claude
base_branch: release/v1.0
created_at: 2026-08-03T22:46:00+0800
updated_at: 2026-08-03T22:49:19+0800
---

# Task — refresh migration evidence

## Goal

Make the workflow migration evidence accurately describe the repository's
current operational and cross-vendor review state.

## Why

The migration evidence and handoff still say Claude is unauthenticated and the
workflow is only `installed`, while `.ai/workflow.yaml` is `operational` and the
real contributor workflow smoke task completed independent Claude review.

## Current behavior and evidence

- `.ai/migration/evidence.md` says review is pending and the project remains
  `installed`.
- `.ai/migration/handoff.md` lists authentication, migration commit, review,
  and smoke-task execution as remaining actions.
- `.ai/workflow.yaml` says `project.status: operational`.
- `TASK-20260730-contributor-workflow-docs` is `DONE`, records Claude's
  `APPROVE` verdict, and records quick=16 and full=117 passing tests.

## In scope

- Refresh `.ai/migration/evidence.md` with the completed review/smoke state.
- Refresh `.ai/migration/handoff.md` with completed actions and remaining
  release-readiness work.
- Add this task's versioned artifacts and independent review evidence.

## Out of scope

- Product code, schemas, tests, package metadata, release gates, and runtime
  behavior.
- Push, merge, tag, publish, release, dependency installation, secrets, or
  authenticated business/model commands.

## Acceptance criteria

- [x] Both migration documents agree that the workflow is `operational`.
- [x] Completed Claude review and smoke-task facts cite versioned evidence.
- [x] External beta, bug-bar, and accuracy gates remain visibly blocked.
- [x] No business behavior or protected path changes.
- [x] Audit, quick, full, diff, and focused stale-wording checks pass.

## Constraints and invariants

- Preserve all historical verification facts; label superseded state rather
  than inventing new test results.
- Do not expose secrets, raw session data, credentials, or personal paths.
- One Writer (Codex); independent final Reviewer (Claude).

## External side effects

- Production: none
- Database writes: none
- Network: none
- Financial/on-chain: none
- Secrets/permissions: none

## Relevant components

- `.ai/migration/evidence.md`
- `.ai/migration/handoff.md`
- `.ai/workflow.yaml`
- `.ai/tasks/TASK-20260730-contributor-workflow-docs/`

## Risk rationale

Documentation-only correction with no runtime effect. Incorrect workflow status
could still misroute future work, so deterministic verification and independent
review are required.

## Open questions

- None.
