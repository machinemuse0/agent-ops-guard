# AGENTS.md

## Project

- Name: ai-coding-cost-guard
- Runtime: Python 3.11+, standard-library-only package runtime.
- Purpose and architecture: `docs/ai/PROJECT_CONTEXT.md`
- Dual-agent workflow: `docs/ai/WORKING_AGREEMENT.md`
- Review policy: `docs/ai/REVIEW_POLICY.md`
- Machine-readable workflow config: `.ai/workflow.yaml`

## Mandatory workflow

1. For any non-trivial change, create or use `.ai/tasks/TASK-*/task.md`.
2. Read the approved task and plan before editing.
3. One task has exactly one Writer. Never let two agents write the same checkout or file set.
4. Do not expand scope, change acceptance criteria, or redesign approved behavior without recording a blocker.
5. The Writer must not serve as the independent final Reviewer. Use the other vendor model.
6. Completion requires executable verification evidence, not a verbal claim.
7. Do not push, merge, deploy, run production migrations, send transactions, or access real funds without explicit human approval.

## Safety boundaries

- Never read, print, commit, or copy secrets, `.env` values, auth files, wallet material, private keys, seed phrases, or production credentials.
- Network access and dependency installation require explicit approval.
- Prefer read-only inspection and the least-permission sandbox.
- Never use destructive Git/filesystem/database commands unless the task explicitly requires them and a human approves.
- Treat production, financial, signing, permission, migration, and on-chain paths as R3.

## Change discipline

- Keep changes minimal and reviewable.
- Do not include unrelated refactors or formatting churn.
- Reuse established project patterns before introducing abstractions.
- Add or update tests for behavior changes.
- Preserve known baseline failures and distinguish them from regressions introduced by the task.
- Record deviations and unknowns in the task directory.

## Verification

```bash
./scripts/verify.sh quick
./scripts/verify.sh full
```

Do not introduce a new formatter, linter, type checker, package dependency, or
network-dependent test unless the task and Owner explicitly approve it.

## Project invariants

- Local-first and offline-by-default behavior must remain intact.
- Reports, support bundles, fixtures, and diagnostics must remain no-raw.
- Never read or copy real prompt/code/output bodies into task evidence.
- Provider token/cache semantics and schema compatibility are public contracts.
- Treat rebuild, capture, provider loading, HTML/SVG rendering, schedule output,
  and security-audit paths as focused review areas.

Project-specific commands and prerequisites are listed by:

```bash
./scripts/verify.sh --list
```

## Review rules

- Review bugs, regressions, missing edge cases, security, concurrency, data integrity, performance, observability, rollback, and missing tests.
- Do not spend review bandwidth on formatting or style already enforced by tools.
- Every finding must include severity, file/line, evidence or counterexample, expected behavior, and a minimal fix direction.
- Standard flow allows one full review/fix cycle plus targeted recheck. R3 allows at most two full cycles before redesign or human arbitration.

## Task artifacts

Each task lives in `.ai/tasks/TASK-YYYYMMDD-slug/` and contains:

- `task.md`
- `plan.md`
- `implementation.md`
- `reviews/`
- `resolution.md`
- `evidence.md`
- `transitions.jsonl`

Do not use chat history as the only source of truth.
