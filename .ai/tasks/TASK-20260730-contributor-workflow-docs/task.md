---
id: TASK-20260730-contributor-workflow-docs
title: contributor workflow docs
type: docs
risk: R1
status: IN_REVIEW
status_before_block: null
owner: human
planner: human
writer: codex
reviewer: claude
base_branch: chore/dual-ai-workflow-v1
created_at: 2026-07-30T21:16:20+0800
updated_at: 2026-07-30T21:19:32+0800
---

# Task — contributor workflow docs

## Goal

Make the contributor guide accurately describe the installed dual-agent task,
Writer/Reviewer, state, and verification workflow.

## Why

The repository now has executable workflow commands and a single verification
entrypoint, but `CONTRIBUTING.md` still documents only the legacy local checks.

## Current behavior and evidence

- `CONTRIBUTING.md` ends with direct pytest/release commands.
- `AGENTS.md` requires task artifacts, one Writer, other-vendor review, and
  `scripts/verify.sh`.
- `aiwf-new-task` and `aiwf-task-state` are installed and locally tested.

## In scope

- Add one concise dual-agent workflow section to `CONTRIBUTING.md`.
- Document task creation, state progression, Writer/Reviewer separation, and
  quick/full verification.
- Keep the existing PR classes, stability, fixture, and provider rules intact.

## Out of scope

- Product behavior, CLI behavior, schemas, tests, release gates, and dependencies.
- Push, merge, tag, publish, release, or external beta/accuracy evidence.

## Acceptance criteria

- [ ] `CONTRIBUTING.md` names the task directory as the source of truth.
- [ ] It documents one Writer and other-vendor independent review.
- [ ] It includes accurate `aiwf-new-task`, `aiwf-task-state`, and
      `./scripts/verify.sh --list|quick|full` commands.
- [ ] It contains no personal absolute path or new product/release claim.
- [ ] Audit, quick, and full verification preserve their existing results.

## Constraints and invariants

- Documentation must match the installed scripts.
- Existing contributor and v0.9 stability policy remains unchanged.
- No network, model, secret, or release action.

## External side effects

- Production: none
- Database writes: none
- Network: none
- Financial/on-chain: none
- Secrets/permissions: none

## Relevant components

- `CONTRIBUTING.md`
- `AGENTS.md`
- `.ai/README.md`
- `scripts/verify.sh`

## Risk rationale

This is a documentation-only change with no runtime effect, but inaccurate
commands could bypass workflow and release gates.

## Open questions

- Claude review remains unavailable until the local CLI is authenticated.
