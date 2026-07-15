# Accuracy Audit Methodology

Status: release-gate methodology for `v1.0`.

AgentOps Guard claims local cost visibility. That claim is only useful if users
can reproduce the accuracy check instead of trusting a statement in README.

## Window

- Audit window: 30 consecutive days.
- Participants: maintainer plus at least 3 beta users.
- Providers: at minimum Codex/OpenAI usage and Claude usage when participants
  have those logs.
- Local tool version: record `python -m aicg --version`.

## Local Procedure

For each participant:

```bash
python -m aicg rebuild --since all
python -m aicg summary --since 30d --format json --out accuracy-local.json
python -m aicg export --kind sessions --format json --since 30d --redact-paths --out accuracy-sessions.json
python -m aicg support bundle --out accuracy-bundle.json
```

Participants review all generated artifacts manually before sharing. Raw logs,
raw prompts, raw code, raw command output, secrets, and unredacted local paths
must not be shared.

## Official Comparison

Compare local totals with the provider official usage or billing page for the
same 30-day window. The comparison uses canonical token buckets:

- `input_uncached_tokens`
- `cache_creation_input_tokens`
- `cache_read_input_tokens`
- `output_tokens`

`reasoning_output_tokens` is a subset of output and is not added to total token
count. Cost comparison uses the local exact-match price table at the time of the
audit.

## Attribution Categories

Every difference over threshold must be attributed to one or more categories:

- retention truncation: provider or local logs do not cover the full window;
- unrecognized event: `format_observations` shows unknown events or fields;
- provider not yet billed: provider official page lags local logs;
- stale price table: local price config does not match provider pricing;
- unsupported provider dialect: reader or usage import lacks evidence for that
  provider shape;
- unresolved: blocks `v1.0`.

## Required Reader Checks

Before marking a participant comparison attributed or passed:

- Review `formatDrift.observations` and `doctor --json` for Claude
  `unknown_event_type:*` rows. Known Claude side events
  (`attachment`, `ai-title`, `last-prompt`, `summary`) should not appear as
  drift; if they do, the reader contract has regressed.
- Inspect turns tagged `CLAUDE_SIDE_EVENT_USAGE`. These rows exist only when a
  known Claude side event carries provider `usage`, and they are a likely source
  to explain Claude local-vs-official variance.
- Inspect the highest-token turns in the 30-day window. Any single turn near or
  above 1,000,000 total tokens must be checked for duplicate accumulation versus
  legitimate `cache_read_input_tokens` growth.

Local SQL helper:

```sql
SELECT id, provider, model,
       input_uncached_tokens + cache_creation_input_tokens
       + cache_read_input_tokens + output_tokens AS total_tokens,
       cache_read_input_tokens,
       token_flags
FROM turns
ORDER BY total_tokens DESC
LIMIT 20;
```

## Release Gate

`v1.0` may release only when:

- 30-day total token deviation median is `<= 5%`;
- every participant-level deviation above `5%` has an attribution;
- no case remains `unresolved`;
- `docs/accuracy-report-1.0.md` links the evidence summary and reproduction
  steps.

Until then, `scripts/check_release_ready.py --target 1.0.0` must remain
`blocked`.
