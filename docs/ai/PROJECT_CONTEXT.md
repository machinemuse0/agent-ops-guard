# Project Context — AgentOps Guard

## Purpose

AgentOps Guard is a local-first Python CLI for scanning AI coding session logs,
producing deterministic no-raw diagnostics, cost/usage reports, security audits,
provider conformance evidence, dashboards, alerts, and support bundles.

## Technology stack

- Python 3.11+ with a standard-library-only package runtime.
- SQLite local state.
- `setuptools` build backend and `pytest` tests.
- JSON Schema files packaged under `aicg/schemas/` and mirrored under `schemas/`.

## Repository map

- `aicg/cli.py` and `aicg/__main__.py`: CLI entry and command dispatch.
- `aicg/readers/`: Codex/Claude JSONL parsing and provider registry.
- `aicg/db.py`: schema, migration, and SQLite persistence.
- `aicg/review/`: deterministic feature extraction, rules, and rendering.
- `aicg/security_audit.py`: no-raw command-risk reporting.
- `aicg/reporter.py`, `dashboard.py`, and `alerts.py`: report surfaces.
- `tests/`: CLI, migration, privacy, schema, review, and release regressions.
- `scripts/check_release_ready.py`: local v1 release gate model.

## Primary entry points and data flows

1. `python -m aicg init` creates isolated local state under `AICG_HOME`.
2. `scan` reads supported local JSONL sources through provider readers.
3. Parsed, normalized metadata is stored in SQLite; raw prompt/code/output is
   not persisted in evidence/report tables.
4. `summary`, `review`, `security audit`, `dashboard`, and `support bundle`
   derive deterministic outputs from stored metadata.
5. `doctor --self-check --json` verifies schema, config, packaged schemas, and
   no-raw persistence invariants.

## External systems and dependencies

- Default operations are local and offline.
- Local Codex/Claude session directories are optional scan inputs.
- Provider plugins are loaded locally and require conformance evidence.
- PyPI and release publication are human-only and outside agent automation.

## High-risk paths

- Session ownership and retry/delta accounting.
- Provider-specific token and cache semantics.
- SQLite rebuild/migration and preservation of user-state tables.
- Raw-data redaction, fixture generation, support bundles, and path leakage.
- Plugin import/conformance, malformed JSONL, and provider drift.
- HTML/SVG output injection, command security flags, and large-directory scans.

## Invariants

- Offline is the default and no telemetry is introduced.
- Reports and evidence remain deterministic and no-raw.
- Stable IDs and local salted hashes preserve documented semantics.
- Schema/package/CLI versions remain synchronized.
- Rebuild preserves acknowledged findings, Git links, snapshots, and alerts.
- External beta and accuracy gates remain visibly blocked until real evidence exists.

## Operations that agents must never perform automatically

- Read, print, copy, or commit real session bodies, credentials, tokens, or salts.
- Run `capture` around a real user command without explicit task approval.
- Install dependencies, access PyPI, publish artifacts, sign/tag, push, or merge.
- Modify cron, launchd, systemd, production systems, or external services.
- Mark beta, bug-bar, or accuracy evidence complete without real source evidence.

## Known baseline failures

- `python scripts/check_release_ready.py` reports `blocked` by design while the
  external beta/bug-bar and accuracy audit are pending.
- `python scripts/check_release_ready.py --target 1.0.0` returns exit 3 by design.

## Open unknowns

- The external beta and 30-day accuracy evidence are outside this migration.
