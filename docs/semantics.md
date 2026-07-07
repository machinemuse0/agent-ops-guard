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
