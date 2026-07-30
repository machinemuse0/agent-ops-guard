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

- Project status remains `installed` until cross-vendor migration review and a
  real task smoke complete.
- Quick verification is a focused 16-test privacy/release/stability/security set.
- Full verification runs 117 tests, both release-gate contracts, and an isolated
  no-build-isolation wheel/self-check smoke.
- Wheel smoke isolates `AICG_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and `PATH`.

## Unknowns and blockers

- Claude Code authentication is unavailable, so migration review is pending.
- External beta, bug-bar, and 30-day accuracy evidence remain outside this task.

## Reviewer focus

- No-raw and offline guarantees in the installed workflow rules.
- Verification command safety and isolation.
- Consistency with current Python/CI/package behavior.
- Absence of business behavior changes.

## Remaining manual actions

1. Authenticate Claude Code.
2. Commit this migration diff.
3. Run `aiwf-review-claude` from a clean committed branch.
4. Resolve findings before claiming migration review complete.
5. Run the `contributor-workflow-docs` task and only then consider `operational`.
