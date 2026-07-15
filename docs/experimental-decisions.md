# v0.9 Experimental Decisions

Status: release-prep snapshot.

`v1.0` must not contain default-enabled experimental behavior. Items without
beta evidence either remain explicit/experimental or stay pending until the
external validation window is complete.

| Item | Introduced | Beta evidence | Acceptance state | Decision | Record |
| --- | --- | --- | --- | --- | --- |
| `capture codex` with redacted storage | v0.2/v0.4 | pending | local privacy tests pass; beta evidence pending | pending; not promoted beyond documented stable command until beta evidence lands | docs/spec-v0.9-v1.0.md |
| Windows support | v0.8 | pending | CI is best-effort via `continue-on-error` | experimental into 1.x until Windows CI is required green | .github/workflows/ci.yml |
| `--allow-unverified` provider loading | v0.8 | not required | trust model makes it an escape hatch | permanent experimental | docs/threat-model.md |
| `import usage --provider openrouter` | v0.3 | pending | local import tests pass; real beta evidence pending | pending; provider dialect remains evidence-gated | docs/spec-v0.3-v0.5.md |
| review LLM assistance | v0.6 non-goal | none | not implemented by default | out of 1.0 | docs/spec-v0.6-v0.8.md |

Decision rule: a pending item cannot become stable without real fixture or beta
evidence plus regression coverage. A permanent experimental item must remain
explicit opt-in and documented as such.
