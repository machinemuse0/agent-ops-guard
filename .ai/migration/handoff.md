# Workflow Migration Handoff

## Files changed

- Root `AGENTS.md`, `CLAUDE.md`, and `.gitignore`.
- `.ai/` workflow config, templates, and migration evidence.
- `docs/ai/` project context, working agreement, and review policy.
- `scripts/verify.sh`.

## Baseline before and after

- Before: 117 tests passed; release-prep and target-1.0 gates were blocked by
  pending external evidence.
- After: 117 tests passed; the same gate states and exit codes remain.

## Decisions

- Project status is `operational` because cross-vendor review and the real
  `TASK-20260730-contributor-workflow-docs` smoke task completed.
- Quick verification is a focused 16-test privacy/release/stability/security set.
- Full verification runs 117 tests, both release-gate contracts, and an isolated
  no-build-isolation wheel/self-check smoke.
- Wheel smoke isolates `AICG_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and `PATH`.

## Unknowns and blockers

- No migration-review or workflow-operational blocker remains.
- External beta, bug-bar, and 30-day accuracy evidence remain outside the
  migration and continue to block v1 release readiness.

## Reviewer focus

- No-raw and offline guarantees in the installed workflow rules.
- Verification command safety and isolation.
- Consistency with current Python/CI/package behavior.
- Absence of business behavior changes.

## Completed migration actions

1. Claude Code authentication was restored after the initial blocked attempt.
2. The migration and contributor workflow documentation were committed locally.
3. Claude independently reviewed the smoke-task diff and returned `APPROVE`.
4. Both `LOW` findings were remediated and quick/full verification passed.
5. The smoke task reached `DONE`, then workflow status became `operational`.

## Remaining manual actions

1. Collect real external beta, bug-bar, and 30-day accuracy evidence.
2. Re-run the release-readiness gates after that evidence is available.
3. Keep push, merge, tag, publish, and release human-controlled.
