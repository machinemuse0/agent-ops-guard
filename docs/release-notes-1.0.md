# Release Notes 1.0 Draft

Status: draft; do not publish until release gates pass.

## Summary

AgentOps Guard `1.0.0` is intended to mark the stable local-first contract:
offline diagnostics, no raw prompt/code/output storage, stable CLI/data/plugin
surfaces, and documented support/security policies.

## Version Timeline

- `v0.1`: local Codex/Claude scan, SQLite metadata, daily Markdown report.
- `v0.2`: schema/evidence hardening, inspect/export, self-check.
- `v0.3`: provider abstraction, canonical token semantics, usage import.
- `v0.4`: policy/privacy guard and stream-redacted capture.
- `v0.5`: cost/productivity analytics and period reports.
- `v0.6`: deterministic review module.
- `v0.7`: local dashboard, alerts, schedule snippets.
- `v0.8`: packaging, plugin verification, threat model, salted hashes.
- `v0.9`: format drift observability, fixture redaction, support bundle, release
  prep gates.

## Contract Highlights

- Semver mapping for CLI, data, and plugin surfaces is in `docs/stability.md`.
- Security response policy is in `SECURITY.md`.
- Contribution and fixture rules are in `CONTRIBUTING.md`.
- Accuracy audit methodology is in `docs/accuracy-audit.md`.

## Accuracy Audit

Pending. Link `docs/accuracy-report-1.0.md` only after the report status is
`passed`.

## Migration

Recommended path for any `0.x` install:

```bash
pip install -U aicg
python -m aicg rebuild --since all
python -m aicg doctor --self-check --json
```
