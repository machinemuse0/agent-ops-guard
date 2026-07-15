# ADR 0001: Canonical Token Semantics

Status: accepted

## Decision

AgentOps Guard stores provider token usage in canonical mutually exclusive
buckets:

- `input_uncached_tokens`
- `cache_creation_input_tokens`
- `cache_read_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`

Totals use the first four buckets. `reasoning_output_tokens` is a subset of
output and is never added again.

## Rationale

Providers report cache and reasoning tokens differently. A canonical bucket
model keeps cross-provider reports comparable and prevents double counting.

## Consequences

Readers must normalize provider-native fields into canonical buckets. Pricing
uses exact local `provider:model` tables and reports unavailable cost when a
price is missing.
