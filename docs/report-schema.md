# AgentOps Guard Report Schema

## Daily Report Schema v4

`summary` emits `schemaVersion = 4`.

Major fields:

- `overview`: session, turn, tool, token, interrupted, retry, background, cost,
  pricing coverage, and scan source failure counts.
- `projectRanking`: top projects by canonical total tokens.
- `providerModelBreakdown`: provider/model rollups using canonical totals.
- `taskRollups`: session/model/task_type rollups.
- `privacyPolicyFindings`: legacy session flag rollups.
- `policyLifecycle`: counts of new, open, and acknowledged policy findings.
  v0.7 adds `newByLevel` alongside `openByLevel` so alerts can distinguish new
  violations from existing acknowledged or open findings.
- `wasteBreakdown`: failed, retry, interrupted, and tool-output-bloat cost
  attribution. Cost fields are `null` when no local or native pricing exists.
- `efficiency`: cache efficiency and task P50/P95 token split signals.
- `gitActivity`: local-only git activity synced from linked repositories.
- `formatDrift`: format observation rows and warnings when unknown events may
  make token statistics incomplete.
- `period`: period metadata when `summary --period day|week|month` is used.
- `compare`: delta against the previous period snapshot when `--compare` is
  used and a previous snapshot exists.

## Export Format

JSON exports use:

```json
{
  "formatVersion": 2,
  "kind": "sessions",
  "rows": []
}
```

CSV exports keep a header row even when no data matches the requested window.

## Pricing

Cost is computed from the canonical buckets:

- `input_uncached_tokens * input_per_mtok_usd`
- `cache_read_input_tokens * cache_read_input_per_mtok_usd`
- `cache_creation_input_tokens * cache_creation_input_per_mtok_usd`
- `output_tokens * output_per_mtok_usd`

`reasoning_output_per_mtok_usd` was removed in v0.9 because reasoning output is
already included in `output_tokens`. `doctor --self-check` fails when local
pricing files still contain that field.

## Review Schema v1

`aicg review` emits `schemaVersion = 1` and `rulesetVersion = 2`.

Session review JSON uses:

```json
{
  "schemaVersion": 1,
  "rulesetVersion": 2,
  "kind": "session",
  "session": {},
  "findings": [],
  "beforeNextRun": []
}
```

Batch review uses `kind = "batch"` with `sessions` and flattened `findings`.
Project review uses `kind = "project"` with aggregated `patterns`.

Finding rows contain `code`, `confidence`, `detail`, `recommendation`, and
`evidence`. Evidence rows contain only ids, source hash, line range,
metric key/value, and message hash. Review outputs do not contain dynamic
timestamps or raw prompt/code/output excerpts.

Users can explicitly resolve a local source hash with:

```bash
python -m aicg inspect source <source_file_hash>
```

SQLite schema v6 introduced review diagnostics in derived tables
`review_findings` and `review_evidence`; current schemas retain them as derived
tables. `aicg rebuild` may delete and recreate them from normalized sessions,
turns, and tool events.

## Dashboard Schema v1

`aicg dashboard --format json` emits `schemaVersion = 1`.

Major fields:

- `currentReport`: the current period daily report model.
- `snapshots`: recent `report_snapshots` for the selected period, plus the
  current report.
- `trends`: series for sessions, total tokens, estimated cost, and waste rate.
- `reviewRecurringTop`: top recurring review finding codes from
  `review_findings`.
- `alertHistory`: recent append-only local `alert_events`.

`aicg dashboard --format html` renders the same model to a single static HTML
file. It uses inline CSS and inline SVG only. It does not load external
resources, start a server, or use a frontend dependency.

## SQLite Schema v9

Current SQLite schema version is `9`.

`schema_meta` includes:

- `schema_version`: current database schema version.
- `hash_salt`: a local random salt initialized with `secrets.token_hex(32)`.

All log, report, and config derived `*_hash` values use local HMAC-SHA256 once
the database salt is loaded. This prevents identical logs on different machines
from producing linkable hashes while keeping hashes stable on the same machine.

`report_snapshots.report_hash` is recomputed from `report_json` during upgrade
or rebuild. Historical hash values that cannot be recomputed exactly, such as
older `alert_events.config_hash`, are HMAC-wrapped with the local salt.

`format_observations` is a derived table populated during `scan`. It records
unknown event types and top-level field drift by provider. `rebuild` can delete
and recreate it from source logs.

The supported migration path from older local databases is:

```bash
python -m aicg rebuild --since all
```

## JSON Schema Files

`v0.9` ships lightweight local schema files as package data under
`aicg/schemas/*.schema.json`. The repo keeps matching copies under
`schemas/*.schema.json` for review and stability tests:

- `daily-report.schema.json`
- `dashboard-model.schema.json`
- `review-output.schema.json`
- `export-output.schema.json`
- `provider-capability.schema.json`

`doctor --self-check --json` parses these files and validates required top-level
keys for generated daily report and dashboard models without external
dependencies.

## Alerts Schema

SQLite schema v7 added user-state table `alert_events` and nullable
`report_snapshots.dashboard_model_json`; schema v9 retains both and adds the
derived `format_observations` table.

`alert_events` stores only local threshold metadata:

- period type and start
- alert key
- configured threshold value
- measured actual value
- report hash
- alert config hash
- created timestamp

`aicg rebuild` preserves `alert_events`. `summary --period day|week|month`
evaluates alerts after writing the report snapshot, and `aicg alerts check`
evaluates the same thresholds without writing a report file.
