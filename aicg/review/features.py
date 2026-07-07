from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TurnFeature:
    id: str
    started_at: str | None
    ended_at: str | None
    status: str | None
    model: str | None
    input_uncached_tokens: int
    cache_read_input_tokens: int
    output_tokens: int
    token_flags: str | None
    error_message_hash: str | None
    source_file_hash: str | None
    source_line_start: int | None
    source_line_end: int | None

    @property
    def cache_ratio(self) -> float:
        denominator = self.input_uncached_tokens + self.cache_read_input_tokens
        return self.cache_read_input_tokens / denominator if denominator else 0.0

    @property
    def has_unreliable_tokens(self) -> bool:
        return "TOKEN_USAGE_UNRELIABLE" in {
            flag.strip() for flag in (self.token_flags or "").split(",") if flag.strip()
        }


@dataclass(frozen=True)
class ToolFeature:
    id: str
    turn_id: str | None
    started_at: str | None
    ended_at: str | None
    tool_type: str | None
    tool_name: str | None
    status: str | None
    output_bytes: int
    exit_code: int | None
    source_file_hash: str | None
    source_line_start: int | None
    source_line_end: int | None


@dataclass(frozen=True)
class SessionFeatures:
    id: str
    provider: str
    project_path: str | None
    status: str | None
    model: str | None
    started_at: str | None
    ended_at: str | None
    source_file_hash: str | None
    source_line_start: int | None
    source_line_end: int | None
    turns: list[TurnFeature]
    tools: list[ToolFeature]


def load_session_features(conn: sqlite3.Connection, session_id: str) -> SessionFeatures | None:
    session = conn.execute(
        """
        SELECT id, provider, project_path, status, model, started_at, ended_at,
               source_file_hash, source_line_start, source_line_end
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        return None
    turns = [
        TurnFeature(
            id=row["id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            model=row["model"],
            input_uncached_tokens=int(row["input_uncached_tokens"] or 0),
            cache_read_input_tokens=int(row["cache_read_input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            token_flags=row["token_flags"],
            error_message_hash=row["error_message_hash"],
            source_file_hash=row["source_file_hash"],
            source_line_start=row["source_line_start"],
            source_line_end=row["source_line_end"],
        )
        for row in conn.execute(
            """
            SELECT id, started_at, ended_at, status, model,
                   input_uncached_tokens, cache_read_input_tokens, output_tokens,
                   token_flags, error_message_hash, source_file_hash, source_line_start,
                   source_line_end
            FROM turns
            WHERE session_id = ?
            ORDER BY COALESCE(started_at, ended_at, ''), COALESCE(source_line_start, 0), id
            """,
            (session_id,),
        ).fetchall()
    ]
    tools = [
        ToolFeature(
            id=row["id"],
            turn_id=row["turn_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            tool_type=row["tool_type"],
            tool_name=row["tool_name"],
            status=row["status"],
            output_bytes=int(row["output_bytes"] or 0),
            exit_code=row["exit_code"],
            source_file_hash=row["source_file_hash"],
            source_line_start=row["source_line_start"],
            source_line_end=row["source_line_end"],
        )
        for row in conn.execute(
            """
            SELECT id, turn_id, started_at, ended_at, tool_type, tool_name,
                   status, output_bytes, exit_code, source_file_hash,
                   source_line_start, source_line_end
            FROM tool_events
            WHERE session_id = ?
            ORDER BY COALESCE(started_at, ended_at, ''), COALESCE(source_line_start, 0), id
            """,
            (session_id,),
        ).fetchall()
    ]
    return SessionFeatures(
        id=session["id"],
        provider=session["provider"],
        project_path=session["project_path"],
        status=session["status"],
        model=session["model"],
        started_at=session["started_at"],
        ended_at=session["ended_at"],
        source_file_hash=session["source_file_hash"],
        source_line_start=session["source_line_start"],
        source_line_end=session["source_line_end"],
        turns=turns,
        tools=tools,
    )


def timeline_key(item: TurnFeature | ToolFeature) -> tuple[str, str, int, str]:
    return (
        item.started_at or item.ended_at or "9999-12-31T23:59:59Z",
        item.source_file_hash or "",
        int(item.source_line_start or 0),
        item.id,
    )


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
