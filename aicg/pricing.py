from __future__ import annotations

import sqlite3
from typing import Any


TOKEN_PRICE_FIELDS = (
    ("input_tokens", "input_per_mtok_usd"),
    ("cached_input_tokens", "cached_input_per_mtok_usd"),
    ("output_tokens", "output_per_mtok_usd"),
    ("reasoning_output_tokens", "reasoning_output_per_mtok_usd"),
    ("cache_creation_input_tokens", "cache_creation_input_per_mtok_usd"),
    ("cache_read_input_tokens", "cache_read_input_per_mtok_usd"),
)


def apply_pricing(
    conn: sqlite3.Connection,
    session_ids: list[str],
    config: dict[str, Any],
) -> dict[str, int]:
    unique_ids = sorted(set(session_ids))
    if not unique_ids:
        return {"turns_priced": 0, "sessions_priced": 0}

    prices = config.get("prices") if isinstance(config.get("prices"), dict) else {}
    placeholders = ",".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"""
        SELECT
            t.id AS turn_id,
            t.session_id AS session_id,
            t.provider AS provider,
            COALESCE(t.model, s.model) AS model,
            t.input_tokens,
            t.cached_input_tokens,
            t.output_tokens,
            t.reasoning_output_tokens,
            t.cache_creation_input_tokens,
            t.cache_read_input_tokens
        FROM turns t
        JOIN sessions s ON s.id = t.session_id
        WHERE t.session_id IN ({placeholders})
        """,
        tuple(unique_ids),
    ).fetchall()

    turns_priced = 0
    for row in rows:
        price = _price_for(prices, row["provider"], row["model"])
        if price is None:
            estimated_cost = None
            credit_estimate = None
        else:
            estimated_cost = _turn_cost(row, price)
            credit_estimate = estimated_cost * _number(price.get("credit_per_usd"), default=1.0)
            turns_priced += 1
        conn.execute(
            """
            UPDATE turns
            SET estimated_cost_usd = ?, credit_estimate = ?
            WHERE id = ?
            """,
            (estimated_cost, credit_estimate, row["turn_id"]),
        )

    sessions_priced = 0
    for session_id in unique_ids:
        aggregate = conn.execute(
            """
            SELECT
                SUM(estimated_cost_usd) AS estimated_cost_usd,
                SUM(credit_estimate) AS credit_estimate,
                SUM(CASE WHEN estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS priced_turns
            FROM turns
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        priced_turns = int(aggregate["priced_turns"] or 0)
        if priced_turns:
            sessions_priced += 1
            conn.execute(
                """
                UPDATE sessions
                SET estimated_cost_usd = ?, credit_estimate = ?
                WHERE id = ?
                """,
                (aggregate["estimated_cost_usd"], aggregate["credit_estimate"], session_id),
            )
        else:
            conn.execute(
                """
                UPDATE sessions
                SET estimated_cost_usd = NULL, credit_estimate = NULL
                WHERE id = ?
                """,
                (session_id,),
            )

    return {"turns_priced": turns_priced, "sessions_priced": sessions_priced}


def _price_for(prices: dict[str, Any], provider: str | None, model: str | None) -> dict[str, Any] | None:
    if not provider or not model:
        return None
    price = prices.get(f"{provider}:{model}")
    return price if isinstance(price, dict) else None


def _turn_cost(row: sqlite3.Row, price: dict[str, Any]) -> float:
    total = 0.0
    for token_column, price_key in TOKEN_PRICE_FIELDS:
        total += int(row[token_column] or 0) / 1_000_000 * _number(price.get(price_key))
    return total


def _number(value: Any, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return default
