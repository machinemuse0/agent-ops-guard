# AgentOps Guard Stability Policy

This document describes the public surface that `v0.8` intends to keep stable
before `v1.0`.

## CLI Surface

Stable:

- `aicg init`
- `aicg scan --since <window> --provider <name|all>`
- `aicg rebuild --since all`
- `aicg summary --format md|json|html`
- `aicg dashboard --period day|week|month --format html|json`
- `aicg alerts check --period day|week|month`
- `aicg schedule print --scheduler cron|launchd|systemd`
- `aicg review --session|--last|--since|--project`
- `aicg policy check|rules|ack`
- `aicg inspect session|source`
- `aicg export --kind sessions|turns|tool-events|issues|scan-errors`
- `aicg doctor --self-check --json`
- `aicg providers`
- `aicg providers verify <module_or_package>`

Experimental:

- `--allow-unverified` provider loading.
- Third-party provider plugins before they pass local conformance.
- `doctor --online` because it shells out to a locally installed Codex binary.
- HTML visual layout details. The data model is stable; CSS is not.

Exit-code policy:

- `policy check` and `alerts check` return `3` when thresholds or policy gates
  fail.
- `doctor --self-check` returns `3` when self-check status is `fail`.
- `summary` may print alert findings after writing snapshots, but still returns
  `0` when report generation succeeds so scheduled dashboard generation is not
  blocked by an alert condition.

Deprecated:

- `reasoning_output_per_mtok_usd` in local pricing files. Reasoning output is a
  subset of output tokens and is not priced separately.

## Data Surface

Stable:

- SQLite normalized tables for sessions, turns, tool events, issues, scan errors,
  policy findings, report snapshots, review findings, review evidence, and alert
  events.
- `schema_meta.version`.
- `schema_meta.hash_salt`.
- `report_snapshots.report_json`.
- `report_snapshots.dashboard_model_json`.
- JSON schema files in `schemas/*.schema.json`.

Experimental:

- Derived ranking order when metrics are tied.
- Recommendation wording in Markdown and HTML reports.
- Internal issue scoring thresholds.

## Plugin Surface

Stable:

- `aicg.readers.base.ProviderReader`
- `aicg.readers.base.Capabilities`
- `aicg.models.ParsedRecords`
- `aicg.models.NormalizedSession`
- `aicg.models.NormalizedTurn`
- `aicg.models.NormalizedToolEvent`
- `aicg.models.PolicyFinding`
- Entry point group `aicg.providers`
- `aicg.conformance.verify_reader`

Experimental:

- Additional optional provider metadata fields.
- Human-readable conformance Markdown wording.

## Deprecation Policy

Before `v1.0`, breaking changes may still happen, but `v0.8` marks the first
stability freeze. Stable CLI flags and JSON keys should receive one minor
release of overlap before removal. Deprecated fields remain readable during the
overlap window and are documented here.

Database schema upgrades are intentionally conservative. When hash semantics or
derived tables change, the supported path is:

```bash
python -m aicg rebuild --since all
```

`rebuild` preserves user-state tables such as `policy_acknowledgements` and
`alert_events`.

`summary` evaluates configured alerts and records `alert_events`, but the
automation-oriented non-zero threshold exit is reserved for `alerts check`.
