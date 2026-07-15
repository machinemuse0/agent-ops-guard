# AgentOps Guard Stability Policy

本文是 `v1.0` 的稳定性契约草案。`v0.9` 进入 release prep 后，除
bugfix、docs、tests、fixture、performance fix 外，不再扩展默认功能面。

## Stable Surfaces

### CLI

Stable commands:

- `aicg init`
- `aicg capture codex -- ...`
- `aicg providers`
- `aicg providers verify <module_or_package> --fixtures <fixtures_dir>`
- `aicg scan --since <window> --provider <name|all>`
- `aicg rebuild --since all`
- `aicg import usage --provider openrouter --file <usage.json>`
- `aicg summary --format md|json|html`
- `aicg dashboard --period day|week|month --format html|json`
- `aicg alerts check --period day|week|month`
- `aicg schedule print --scheduler cron|launchd|systemd`
- `aicg security audit --since <window>`
- `aicg review --session|--last|--since|--project`
- `aicg policy check|rules|ack`
- `aicg inspect session|source`
- `aicg export --kind sessions|turns|tool-events|issues|scan-errors`
- `aicg fixture redact <input.jsonl> --out <fixture.jsonl>`
- `aicg support bundle --out <bundle.json>`
- `aicg doctor --self-check --json`

Experimental CLI surface:

- `--allow-unverified` provider loading.
- `doctor --online`, because it shells out to a locally installed Codex binary.
- HTML visual layout details. The JSON model is stable; CSS is not.
- Windows support until CI is made non-best-effort.

Exit-code policy:

- `policy check` and `alerts check` return `3` when thresholds or policy gates
  fail.
- `doctor --self-check` returns `3` when self-check status is `fail`.
- `providers verify` returns `3` when conformance fails.
- `support bundle` and `fixture redact` return non-zero when their privacy
  self-checks detect leaks.
- `summary` may print alert findings after writing snapshots, but still returns
  `0` when report generation succeeds.

### Data

Stable data surface:

- SQLite normalized tables for sessions, turns, tool events, issues, scan
  errors, policy findings, report snapshots, review findings, review evidence,
  alert events, git links/activity, and format observations.
- `schema_meta.schema_version`.
- `schema_meta.hash_salt`.
- `report_snapshots.report_json`.
- `report_snapshots.dashboard_model_json`.
- JSON schema files packaged under `aicg/schemas/*.schema.json`, with matching
  repo-review copies in `schemas/*.schema.json`.

Experimental data surface:

- Derived ranking order when metrics are tied.
- Recommendation wording in Markdown and HTML reports.
- Internal issue scoring thresholds.
- Support bundle wording and human-readable section order.

### Plugin

Stable plugin surface:

- `aicg.readers.base.ProviderReader`
- `aicg.readers.base.Capabilities`
- `aicg.models.ParsedRecords`
- `aicg.models.NormalizedSession`
- `aicg.models.NormalizedTurn`
- `aicg.models.NormalizedToolEvent`
- `aicg.models.PolicyFinding`
- Entry point group `aicg.providers`
- `aicg.conformance.verify_reader`

Experimental plugin surface:

- Additional optional provider metadata fields.
- Human-readable conformance Markdown wording.
- Unverified provider loading.

## Semver Mapping

| Surface | MAJOR | MINOR | PATCH |
| --- | --- | --- | --- |
| CLI | Remove or change meaning of a stable command, flag, exit code, or machine-readable output shape. | Add a command, flag, optional output field, or new non-default behavior. | Fix behavior while preserving the documented shape. |
| Data | Require semantic rebuild with changed meaning, remove/rename JSON fields, or remove stable SQLite columns/tables. | Additive SQLite table/column or JSON field. Rebuild may be required but must preserve user-state tables. | No structural change. |
| Plugin | Break `ProviderReader`, `Capabilities`, `ParsedRecords`, or make existing conforming plugins fail due to stricter required semantics. | Add optional protocol methods or capability fields with backward-compatible defaults. | Fix conformance bugs without changing required semantics. |

Data-surface nuance: even additive MINOR schema changes may use `rebuild`, but
MINOR rebuilds must preserve user-state tables and keep existing semantics.
Only MAJOR may intentionally change stable meaning.

## Version Carriers

The following version carriers must remain consistent:

- `python -m aicg --version` reports package version, DB schema version, and
  `REVIEW_RULESET_VERSION`.
- `schema_meta.schema_version` matches `aicg.db.CURRENT_SCHEMA_VERSION`.
- Daily report JSON uses `aicg.reporter.DAILY_REPORT_SCHEMA_VERSION`.
- Review output uses `REVIEW_RULESET_VERSION`.
- `doctor --self-check --json` fails when the local DB schema, schema files, or
  removed config fields drift from the supported release.

## Deprecation Policy

Stable items must receive at least one MINOR release of warning before removal.
The warning must be visible in docs and in at least one local diagnostic path
when possible. Removal happens in the next MINOR or a MAJOR, depending on
compatibility impact.

Removed in `v0.9`:

- `reasoning_output_per_mtok_usd` in local pricing files. Reasoning output is a
  subset of output tokens and is not priced separately. `doctor --self-check`
  fails when the removed field is still present.

## Freeze Rules

After `v0.9` release prep starts, accepted PR categories are:

- bugfix with regression test;
- docs;
- tests or fixtures;
- privacy/security hardening;
- performance fix that preserves behavior;
- release engineering checks.

Any behavior change needs an explicit maintainer exception and a note in the PR
describing the affected stable surface.

Database schema upgrades remain conservative. The supported local migration
path is:

```bash
python -m aicg rebuild --since all
```

`rebuild` preserves user-state tables such as `policy_acks`, `git_links`,
`report_snapshots`, and `alert_events`.
