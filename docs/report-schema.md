# AgentOps Guard Report Schema

## Daily Report Schema v3

`summary` emits `schemaVersion = 3`.

Major fields:

- `overview`: session, turn, tool, token, interrupted, retry, background, cost,
  pricing coverage, and scan source failure counts.
- `projectRanking`: top projects by canonical total tokens.
- `providerModelBreakdown`: provider/model rollups using canonical totals.
- `taskRollups`: session/model/task_type rollups.
- `privacyPolicyFindings`: legacy session flag rollups.
- `policyLifecycle`: counts of new, open, and acknowledged policy findings.
- `wasteBreakdown`: failed, retry, interrupted, and tool-output-bloat cost
  attribution. Cost fields are `null` when no local or native pricing exists.
- `efficiency`: cache efficiency and task P50/P95 token split signals.
- `gitActivity`: local-only git activity synced from linked repositories.
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

`reasoning_output_per_mtok_usd` is deprecated because reasoning output is
already included in `output_tokens`.
