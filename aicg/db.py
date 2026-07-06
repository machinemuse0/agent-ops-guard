from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    Issue,
    NormalizedSession,
    NormalizedToolEvent,
    NormalizedTurn,
    ParsedRecords,
)
from .util import sha256_text, stable_id, utc_now_iso


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    project_path TEXT,
    source_file TEXT,
    source_file_hash TEXT,
    started_at TEXT,
    ended_at TEXT,
    status TEXT,
    model TEXT,
    task_type TEXT,
    duration_ms INTEGER,
    retry_count INTEGER DEFAULT 0,
    background_flag INTEGER DEFAULT 0,
    estimated_cost_usd REAL,
    credit_estimate REAL,
    privacy_flags TEXT,
    policy_flags TEXT,
    raw_event_count INTEGER DEFAULT 0,
    malformed_line_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    status TEXT,
    model TEXT,
    task_type TEXT,
    duration_ms INTEGER,
    retry_count INTEGER DEFAULT 0,
    background_flag INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    cached_input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    reasoning_output_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL,
    credit_estimate REAL,
    privacy_flags TEXT,
    policy_flags TEXT,
    error_type TEXT,
    error_message_hash TEXT
);

CREATE TABLE IF NOT EXISTS tool_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    provider TEXT NOT NULL,
    tool_type TEXT,
    tool_name TEXT,
    status TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,
    output_bytes INTEGER DEFAULT 0,
    exit_code INTEGER
);

CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    severity TEXT,
    code TEXT,
    title TEXT NOT NULL,
    detail TEXT,
    recommendation TEXT,
    evidence TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    command TEXT,
    provider TEXT,
    started_at TEXT,
    ended_at TEXT,
    exit_code INTEGER,
    raw_path TEXT
);

CREATE TABLE IF NOT EXISTS scan_errors (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    source_file TEXT NOT NULL,
    error_type TEXT,
    error_message_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_source_hash ON sessions(source_file_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_project_path ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_provider_model ON sessions(provider, model);
CREATE INDEX IF NOT EXISTS idx_turns_session_id ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(status);
CREATE INDEX IF NOT EXISTS idx_tool_events_session_id ON tool_events(session_id);
CREATE INDEX IF NOT EXISTS idx_issues_session_id ON issues(session_id);
CREATE INDEX IF NOT EXISTS idx_scan_errors_created_at ON scan_errors(created_at);
CREATE INDEX IF NOT EXISTS idx_scan_errors_source ON scan_errors(provider, source_file);
"""


COLUMN_MIGRATIONS = {
    "sessions": {
        "task_type": "task_type TEXT",
        "duration_ms": "duration_ms INTEGER",
        "retry_count": "retry_count INTEGER DEFAULT 0",
        "background_flag": "background_flag INTEGER DEFAULT 0",
        "estimated_cost_usd": "estimated_cost_usd REAL",
        "credit_estimate": "credit_estimate REAL",
        "privacy_flags": "privacy_flags TEXT",
        "policy_flags": "policy_flags TEXT",
    },
    "turns": {
        "task_type": "task_type TEXT",
        "duration_ms": "duration_ms INTEGER",
        "retry_count": "retry_count INTEGER DEFAULT 0",
        "background_flag": "background_flag INTEGER DEFAULT 0",
        "estimated_cost_usd": "estimated_cost_usd REAL",
        "credit_estimate": "credit_estimate REAL",
        "privacy_flags": "privacy_flags TEXT",
        "policy_flags": "policy_flags TEXT",
    },
}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def upsert_session(conn: sqlite3.Connection, session: NormalizedSession) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            id, provider, project_path, source_file, source_file_hash,
            started_at, ended_at, status, model, task_type, duration_ms,
            retry_count, background_flag, estimated_cost_usd, credit_estimate,
            privacy_flags, policy_flags, raw_event_count, malformed_line_count,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            provider=excluded.provider,
            project_path=excluded.project_path,
            source_file=excluded.source_file,
            source_file_hash=excluded.source_file_hash,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            status=excluded.status,
            model=excluded.model,
            task_type=excluded.task_type,
            duration_ms=excluded.duration_ms,
            retry_count=excluded.retry_count,
            background_flag=excluded.background_flag,
            estimated_cost_usd=excluded.estimated_cost_usd,
            credit_estimate=excluded.credit_estimate,
            privacy_flags=excluded.privacy_flags,
            policy_flags=excluded.policy_flags,
            raw_event_count=excluded.raw_event_count,
            malformed_line_count=excluded.malformed_line_count
        """,
        (
            session.id,
            session.provider,
            session.project_path,
            session.source_file,
            session.source_file_hash,
            session.started_at,
            session.ended_at,
            session.status,
            session.model,
            session.task_type,
            session.duration_ms,
            session.retry_count,
            int(session.background_flag),
            session.estimated_cost_usd,
            session.credit_estimate,
            session.privacy_flags,
            session.policy_flags,
            session.raw_event_count,
            session.malformed_line_count,
            session.created_at,
        ),
    )


def upsert_turn(conn: sqlite3.Connection, turn: NormalizedTurn) -> None:
    conn.execute(
        """
        INSERT INTO turns (
            id, session_id, provider, started_at, ended_at, status, model,
            task_type, duration_ms, retry_count, background_flag,
            input_tokens, cached_input_tokens, output_tokens,
            reasoning_output_tokens, cache_creation_input_tokens,
            cache_read_input_tokens, estimated_cost_usd, credit_estimate,
            privacy_flags, policy_flags, error_type, error_message_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            provider=excluded.provider,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            status=excluded.status,
            model=excluded.model,
            task_type=excluded.task_type,
            duration_ms=excluded.duration_ms,
            retry_count=excluded.retry_count,
            background_flag=excluded.background_flag,
            input_tokens=excluded.input_tokens,
            cached_input_tokens=excluded.cached_input_tokens,
            output_tokens=excluded.output_tokens,
            reasoning_output_tokens=excluded.reasoning_output_tokens,
            cache_creation_input_tokens=excluded.cache_creation_input_tokens,
            cache_read_input_tokens=excluded.cache_read_input_tokens,
            estimated_cost_usd=excluded.estimated_cost_usd,
            credit_estimate=excluded.credit_estimate,
            privacy_flags=excluded.privacy_flags,
            policy_flags=excluded.policy_flags,
            error_type=excluded.error_type,
            error_message_hash=excluded.error_message_hash
        """,
        (
            turn.id,
            turn.session_id,
            turn.provider,
            turn.started_at,
            turn.ended_at,
            turn.status,
            turn.model,
            turn.task_type,
            turn.duration_ms,
            turn.retry_count,
            int(turn.background_flag),
            turn.input_tokens,
            turn.cached_input_tokens,
            turn.output_tokens,
            turn.reasoning_output_tokens,
            turn.cache_creation_input_tokens,
            turn.cache_read_input_tokens,
            turn.estimated_cost_usd,
            turn.credit_estimate,
            turn.privacy_flags,
            turn.policy_flags,
            turn.error_type,
            turn.error_message_hash,
        ),
    )


def upsert_tool_event(conn: sqlite3.Connection, event: NormalizedToolEvent) -> None:
    conn.execute(
        """
        INSERT INTO tool_events (
            id, session_id, turn_id, provider, tool_type, tool_name, status,
            started_at, ended_at, duration_ms, output_bytes, exit_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            turn_id=excluded.turn_id,
            provider=excluded.provider,
            tool_type=excluded.tool_type,
            tool_name=excluded.tool_name,
            status=excluded.status,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            duration_ms=excluded.duration_ms,
            output_bytes=excluded.output_bytes,
            exit_code=excluded.exit_code
        """,
        (
            event.id,
            event.session_id,
            event.turn_id,
            event.provider,
            event.tool_type,
            event.tool_name,
            event.status,
            event.started_at,
            event.ended_at,
            event.duration_ms,
            event.output_bytes,
            event.exit_code,
        ),
    )


def upsert_issue(conn: sqlite3.Connection, issue: Issue) -> None:
    conn.execute(
        """
        INSERT INTO issues (
            id, session_id, severity, code, title, detail, recommendation,
            evidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            severity=excluded.severity,
            code=excluded.code,
            title=excluded.title,
            detail=excluded.detail,
            recommendation=excluded.recommendation,
            evidence=excluded.evidence,
            created_at=excluded.created_at
        """,
        (
            issue.id,
            issue.session_id,
            issue.severity,
            issue.code,
            issue.title,
            issue.detail,
            issue.recommendation,
            issue.evidence,
            issue.created_at,
        ),
    )


def import_records(conn: sqlite3.Connection, records: ParsedRecords) -> dict[str, int]:
    for session in records.sessions:
        upsert_session(conn, session)
    for turn in records.turns:
        upsert_turn(conn, turn)
    for event in records.tool_events:
        upsert_tool_event(conn, event)
    return {
        "sessions": len(records.sessions),
        "turns": len(records.turns),
        "tool_events": len(records.tool_events),
    }


def replace_issues_for_sessions(
    conn: sqlite3.Connection, session_ids: list[str], issues: list[Issue]
) -> None:
    if session_ids:
        placeholders = ",".join("?" for _ in session_ids)
        conn.execute(
            f"DELETE FROM issues WHERE session_id IN ({placeholders})",
            tuple(session_ids),
        )
    for issue in issues:
        upsert_issue(conn, issue)


def insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    command: str,
    provider: str,
    started_at: str,
    ended_at: str,
    exit_code: int,
    raw_path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            id, command, provider, started_at, ended_at, exit_code, raw_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            command=excluded.command,
            provider=excluded.provider,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            exit_code=excluded.exit_code,
            raw_path=excluded.raw_path
        """,
        (run_id, command, provider, started_at, ended_at, exit_code, raw_path),
    )


def upsert_scan_error(
    conn: sqlite3.Connection,
    *,
    provider: str,
    source_file: str,
    error_type: str,
    error_message: str,
) -> str:
    error_hash = sha256_text(error_message)
    error_id = stable_id("scan_error", provider, source_file, error_type, error_hash)
    conn.execute(
        """
        INSERT INTO scan_errors (
            id, provider, source_file, error_type, error_message_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            created_at=excluded.created_at
        """,
        (
            error_id,
            provider,
            source_file,
            error_type,
            error_hash,
            utc_now_iso(),
        ),
    )
    return error_id


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    allowed = {"sessions", "turns", "tool_events", "issues", "runs", "scan_errors"}
    if table not in allowed:
        raise ValueError(f"Unsupported table: {table}")
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])
