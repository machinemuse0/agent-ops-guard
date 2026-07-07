from __future__ import annotations

from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def total_tokens(row: Any) -> int:
    input_uncached = _value(row, "input_uncached_tokens")
    if input_uncached is None:
        provider = str(_value(row, "provider") or "").lower()
        input_tokens = _int_value(row, "input_tokens")
        if provider in {"codex", "openai"}:
            input_uncached_tokens = max(input_tokens - _int_value(row, "cached_input_tokens"), 0)
        else:
            input_uncached_tokens = input_tokens
    else:
        input_uncached_tokens = _int_value(row, "input_uncached_tokens")
    output_tokens = _int_value(row, "output_tokens")
    return (
        input_uncached_tokens
        + _int_value(row, "cache_creation_input_tokens")
        + _int_value(row, "cache_read_input_tokens")
        + output_tokens
    )


def _int_value(row: Any, key: str) -> int:
    value = _value(row, key)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None
