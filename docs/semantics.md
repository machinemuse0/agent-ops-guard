# AgentOps Guard Semantics

## Token Buckets

All report totals use disjoint canonical buckets:

```text
total_tokens = input_uncached_tokens
             + cache_read_input_tokens
             + cache_creation_input_tokens
             + output_tokens
```

`reasoning_output_tokens` is informational only. It is a subset of
`output_tokens` and is never added to totals or priced separately.

Provider mapping:

- Codex/OpenAI style logs: raw `input_tokens` can include cached input, so
  `input_uncached_tokens = input_tokens - cached_input_tokens`, and cached
  input maps to `cache_read_input_tokens`.
- Claude logs: raw `input_tokens` is already uncached input, so it maps directly
  to `input_uncached_tokens`.
- Codex `token_count`: `last_token_usage` is accepted directly. When only
  cumulative `total_token_usage` exists, AgentOps Guard diffs it against the
  previous cumulative value. If no previous value exists, that turn is recorded
  as zero token usage with `TOKEN_USAGE_UNRELIABLE`.

## Status And Retry

- `completed`: explicit completion evidence exists.
- `failed`: explicit failure evidence exists.
- `interrupted`: EOF is reached while the session or a turn is still running.
- `unknown`: no reliable status evidence exists.

Report failure rate should use `completed + failed` as the denominator.
`interrupted` is shown separately.

## Identity

- `sessions.native_session_id` stores the provider-native session id when
  available.
- `sessions.lineage_id` groups related resumes/imports.
- `sessions.parent_session_id` is reserved for explicit resume parent links.
- `turns.native_turn_key` is unique when provider-native turn/message identity is
  available. Duplicate native turn keys update the earliest stored turn instead
  of double-counting usage.
- Codex fallback turn keys include the source file hash and file-local index.

## Policy Surface

`tool_events.call_target` stores comma-separated host names extracted from tool
call arguments. It never stores full URLs, query strings, request bodies, or raw
command output.

Policy severity is tied to surface:

- `call`: active tool invocation, can produce `violation`.
- `output`: tool output, can produce `warning`.
- `mention`: assistant/user text mention, can produce `needs_review` only when
  mention scanning is enabled.

## Review Diagnostics

`aicg review` is deterministic for the same DB and
`REVIEW_RULESET_VERSION`. Review output intentionally omits `generatedAt`.

Review diagnoses failure shape from normalized metadata only. It never opens,
copies, quotes, or redacts raw prompt/code/output content. Evidence pointers use
source file hash, line ranges, metric key/value, and message hash.
Resolving a source hash to the local path is an explicit user action via
`python -m aicg inspect source <source_file_hash>`.

Initial review codes:

- `REPEATED_IDENTICAL_FAILURE`
- `EDIT_RETRY_CHURN`
- `CONTEXT_OVERLOAD`
- `TOOL_OUTPUT_FLOOD`
- `MISSING_SETUP`
- `NO_PROGRESS_THRASHING`
- `MODEL_FALLBACK_DEGRADATION`
- `INTERRUPTED_TAIL`

Confidence is a fixed evidence-strength label: `high`, `medium`, or `low`.
It is not a probability.

Ruleset v2 narrows high-noise cases:

- context growth ignores zero-start and `TOKEN_USAGE_UNRELIABLE` token points;
- setup diagnosis treats ordinary non-zero shell exits as weak evidence unless
  they are paired with stronger provider/session failure signals;
- no-progress diagnosis only counts mutating file tools, not read-only file
  inspection;
- model fallback diagnosis requires minimum before/after samples and absolute
  after-switch failures;
- project review defaults to the most recent 200 matching sessions. Use
  `aicg review --project <path> --limit 0` for an explicit full-history review.
