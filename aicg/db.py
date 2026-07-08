from __future__ import annotations

import sqlite3
import shutil
import secrets
from pathlib import Path

from .models import (
    AlertEvent,
    EvidencePointer,
    Issue,
    NormalizedSession,
    NormalizedToolEvent,
    NormalizedTurn,
    ParsedRecords,
    PolicyFinding,
    ReviewEvidence,
    ReviewFinding,
)
from .sqlite_utils import chunked
from .util import set_hash_salt, sha256_text, stable_id, utc_now_iso


CURRENT_SCHEMA_VERSION = 8


DERIVED_TABLES = {
    "sessions",
    "turns",
    "tool_events",
    "issues",
    "issue_evidence",
    "scan_errors",
    "scan_state",
    "policy_findings",
    "review_findings",
    "review_evidence",
    "git_activity",
}


USER_TABLES = {"runs", "report_snapshots", "policy_acks", "git_links", "alert_events"}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    native_session_id TEXT,
    lineage_id TEXT,
    parent_session_id TEXT,
    project_path TEXT,
    source_file TEXT,
    source_file_hash TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
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
    cost_source TEXT,
    wasted_cost_usd REAL,
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
    native_turn_key TEXT,
    source_file_hash TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    started_at TEXT,
    ended_at TEXT,
    status TEXT,
    model TEXT,
    task_type TEXT,
    duration_ms INTEGER,
    retry_count INTEGER DEFAULT 0,
    background_flag INTEGER DEFAULT 0,
    input_uncached_tokens INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    cached_input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    reasoning_output_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL,
    credit_estimate REAL,
    cost_source TEXT,
    privacy_flags TEXT,
    policy_flags TEXT,
    token_flags TEXT,
    error_type TEXT,
    error_message_hash TEXT
);

CREATE TABLE IF NOT EXISTS tool_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    provider TEXT NOT NULL,
    source_file_hash TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    tool_type TEXT,
    tool_name TEXT,
    status TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,
    output_bytes INTEGER DEFAULT 0,
    exit_code INTEGER,
    call_target TEXT
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

CREATE TABLE IF NOT EXISTS issue_evidence (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL,
    session_id TEXT,
    turn_id TEXT,
    tool_event_id TEXT,
    source_file_hash TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    metric_name TEXT,
    metric_value TEXT,
    message_hash TEXT,
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

CREATE TABLE IF NOT EXISTS scan_state (
    source_file TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    file_hash TEXT,
    file_size INTEGER,
    mtime REAL,
    last_line INTEGER,
    last_scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_findings (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    tool_event_id TEXT,
    rule_id TEXT NOT NULL,
    level TEXT NOT NULL,
    surface TEXT NOT NULL,
    detail_hash TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_acks (
    finding_id TEXT PRIMARY KEY,
    reason TEXT,
    acked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_findings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ruleset_version INTEGER NOT NULL,
    code TEXT NOT NULL,
    confidence TEXT NOT NULL,
    detail TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_evidence (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    tool_event_id TEXT,
    source_file_hash TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    metric_name TEXT,
    metric_value TEXT,
    message_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_snapshots (
    period_type TEXT NOT NULL,
    period_start TEXT NOT NULL,
    report_json TEXT NOT NULL,
    report_hash TEXT NOT NULL,
    dashboard_model_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (period_type, period_start)
);

CREATE TABLE IF NOT EXISTS alert_events (
    id TEXT PRIMARY KEY,
    period_type TEXT NOT NULL,
    period_start TEXT NOT NULL,
    alert_key TEXT NOT NULL,
    threshold_value REAL NOT NULL,
    actual_value REAL,
    report_hash TEXT,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_links (
    repo_path TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    linked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_activity (
    project_path TEXT NOT NULL,
    period_start TEXT NOT NULL,
    commits INTEGER DEFAULT 0,
    merge_commits INTEGER DEFAULT 0,
    insertions INTEGER DEFAULT 0,
    deletions INTEGER DEFAULT 0,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (project_path, period_start)
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_source_hash ON sessions(source_file_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_project_path ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_provider_model ON sessions(provider, model);
CREATE INDEX IF NOT EXISTS idx_turns_session_id ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(status);
CREATE INDEX IF NOT EXISTS idx_tool_events_session_id ON tool_events(session_id);
CREATE INDEX IF NOT EXISTS idx_issues_session_id ON issues(session_id);
CREATE INDEX IF NOT EXISTS idx_issue_evidence_issue_id ON issue_evidence(issue_id);
CREATE INDEX IF NOT EXISTS idx_issue_evidence_session_id ON issue_evidence(session_id);
CREATE INDEX IF NOT EXISTS idx_scan_errors_created_at ON scan_errors(created_at);
CREATE INDEX IF NOT EXISTS idx_scan_errors_source ON scan_errors(provider, source_file);
CREATE INDEX IF NOT EXISTS idx_scan_state_provider ON scan_state(provider);
CREATE INDEX IF NOT EXISTS idx_policy_findings_session_id ON policy_findings(session_id);
CREATE INDEX IF NOT EXISTS idx_policy_findings_level ON policy_findings(level);
CREATE INDEX IF NOT EXISTS idx_review_findings_session_id ON review_findings(session_id);
CREATE INDEX IF NOT EXISTS idx_review_findings_code ON review_findings(code);
CREATE INDEX IF NOT EXISTS idx_review_evidence_finding_id ON review_evidence(finding_id);
CREATE INDEX IF NOT EXISTS idx_review_evidence_session_id ON review_evidence(session_id);
CREATE INDEX IF NOT EXISTS idx_git_activity_project_period ON git_activity(project_path, period_start);
CREATE INDEX IF NOT EXISTS idx_alert_events_period_created ON alert_events(period_type, created_at);
"""


COLUMN_MIGRATIONS = {
    "sessions": {
        "native_session_id": "native_session_id TEXT",
        "lineage_id": "lineage_id TEXT",
        "parent_session_id": "parent_session_id TEXT",
        "task_type": "task_type TEXT",
        "duration_ms": "duration_ms INTEGER",
        "source_line_start": "source_line_start INTEGER",
        "source_line_end": "source_line_end INTEGER",
        "retry_count": "retry_count INTEGER DEFAULT 0",
        "background_flag": "background_flag INTEGER DEFAULT 0",
        "estimated_cost_usd": "estimated_cost_usd REAL",
        "credit_estimate": "credit_estimate REAL",
        "cost_source": "cost_source TEXT",
        "wasted_cost_usd": "wasted_cost_usd REAL",
        "privacy_flags": "privacy_flags TEXT",
        "policy_flags": "policy_flags TEXT",
    },
    "turns": {
        "native_turn_key": "native_turn_key TEXT",
        "task_type": "task_type TEXT",
        "source_file_hash": "source_file_hash TEXT",
        "source_line_start": "source_line_start INTEGER",
        "source_line_end": "source_line_end INTEGER",
        "duration_ms": "duration_ms INTEGER",
        "retry_count": "retry_count INTEGER DEFAULT 0",
        "background_flag": "background_flag INTEGER DEFAULT 0",
        "input_uncached_tokens": "input_uncached_tokens INTEGER DEFAULT 0",
        "estimated_cost_usd": "estimated_cost_usd REAL",
        "credit_estimate": "credit_estimate REAL",
        "cost_source": "cost_source TEXT",
        "privacy_flags": "privacy_flags TEXT",
        "policy_flags": "policy_flags TEXT",
        "token_flags": "token_flags TEXT",
    },
    "tool_events": {
        "source_file_hash": "source_file_hash TEXT",
        "source_line_start": "source_line_start INTEGER",
        "source_line_end": "source_line_end INTEGER",
        "call_target": "call_target TEXT",
    },
    "report_snapshots": {
        "dashboard_model_json": "dashboard_model_json TEXT",
    },
}


REQUIRED_COLUMNS = {
    "schema_meta": {"key", "value", "created_at", "updated_at"},
    "sessions": {
        "id",
        "provider",
        "native_session_id",
        "lineage_id",
        "parent_session_id",
        "project_path",
        "source_file",
        "source_file_hash",
        "source_line_start",
        "source_line_end",
        "started_at",
        "ended_at",
        "status",
        "model",
        "task_type",
        "duration_ms",
        "retry_count",
        "background_flag",
        "estimated_cost_usd",
        "credit_estimate",
        "cost_source",
        "wasted_cost_usd",
        "privacy_flags",
        "policy_flags",
        "raw_event_count",
        "malformed_line_count",
        "created_at",
    },
    "turns": {
        "id",
        "session_id",
        "provider",
        "native_turn_key",
        "source_file_hash",
        "source_line_start",
        "source_line_end",
        "started_at",
        "ended_at",
        "status",
        "model",
        "task_type",
        "duration_ms",
        "retry_count",
        "background_flag",
        "input_uncached_tokens",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "estimated_cost_usd",
        "credit_estimate",
        "cost_source",
        "privacy_flags",
        "policy_flags",
        "token_flags",
        "error_type",
        "error_message_hash",
    },
    "tool_events": {
        "id",
        "session_id",
        "turn_id",
        "provider",
        "source_file_hash",
        "source_line_start",
        "source_line_end",
        "tool_type",
        "tool_name",
        "status",
        "started_at",
        "ended_at",
        "duration_ms",
        "output_bytes",
        "exit_code",
        "call_target",
    },
    "issues": {"id", "session_id", "severity", "code", "title", "detail", "recommendation", "evidence", "created_at"},
    "issue_evidence": {
        "id",
        "issue_id",
        "session_id",
        "turn_id",
        "tool_event_id",
        "source_file_hash",
        "source_line_start",
        "source_line_end",
        "metric_name",
        "metric_value",
        "message_hash",
        "created_at",
    },
    "runs": {"id", "command", "provider", "started_at", "ended_at", "exit_code", "raw_path"},
    "scan_errors": {"id", "provider", "source_file", "error_type", "error_message_hash", "created_at"},
    "scan_state": {"source_file", "provider", "file_hash", "file_size", "mtime", "last_line", "last_scanned_at"},
    "policy_findings": {
        "id",
        "session_id",
        "tool_event_id",
        "rule_id",
        "level",
        "surface",
        "detail_hash",
        "first_seen_at",
        "last_seen_at",
    },
    "policy_acks": {"finding_id", "reason", "acked_at"},
    "review_findings": {
        "id",
        "session_id",
        "ruleset_version",
        "code",
        "confidence",
        "detail",
        "recommendation",
        "created_at",
    },
    "review_evidence": {
        "id",
        "finding_id",
        "session_id",
        "turn_id",
        "tool_event_id",
        "source_file_hash",
        "source_line_start",
        "source_line_end",
        "metric_name",
        "metric_value",
        "message_hash",
        "created_at",
    },
    "report_snapshots": {
        "period_type",
        "period_start",
        "report_json",
        "report_hash",
        "dashboard_model_json",
        "created_at",
    },
    "alert_events": {
        "id",
        "period_type",
        "period_start",
        "alert_key",
        "threshold_value",
        "actual_value",
        "report_hash",
        "config_hash",
        "created_at",
    },
    "git_links": {"repo_path", "project_path", "linked_at"},
    "git_activity": {
        "project_path",
        "period_start",
        "commits",
        "merge_commits",
        "insertions",
        "deletions",
        "synced_at",
    },
}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def init_db(db_path: Path, *, allow_schema_upgrade: bool = False) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        _preflight_schema_version(conn, allow_schema_upgrade=allow_schema_upgrade)
        conn.executescript(SCHEMA_SQL)
        _ensure_columns(conn)
        _ensure_indexes(conn)
        _ensure_schema_meta(conn, allow_schema_upgrade=allow_schema_upgrade)


def _preflight_schema_version(conn: sqlite3.Connection, *, allow_schema_upgrade: bool) -> None:
    if not _table_exists(conn, "schema_meta"):
        return
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return
    try:
        version = int(row["value"])
    except (TypeError, ValueError) as exc:
        raise ValueError("schema_version is not an integer") from exc
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"database schema_version {version} is newer than supported {CURRENT_SCHEMA_VERSION}")
    if version < CURRENT_SCHEMA_VERSION and not allow_schema_upgrade:
        raise ValueError(f"database schema_version {version} is older than supported {CURRENT_SCHEMA_VERSION}; run: aicg rebuild")


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_native_turn_key ON turns(native_turn_key) WHERE native_turn_key IS NOT NULL"
    )


def _ensure_schema_meta(conn: sqlite3.Connection, *, allow_schema_upgrade: bool = False) -> None:
    now = utc_now_iso()
    _assert_required_columns(conn)
    upgraded = False
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO schema_meta (key, value, created_at, updated_at)
            VALUES ('schema_version', ?, ?, ?)
            """,
            (str(CURRENT_SCHEMA_VERSION), now, now),
        )
    else:
        try:
            version = int(row["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError("schema_version is not an integer") from exc
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"database schema_version {version} is newer than supported {CURRENT_SCHEMA_VERSION}"
            )
        if version < CURRENT_SCHEMA_VERSION:
            if not allow_schema_upgrade:
                raise ValueError(
                    f"database schema_version {version} is older than supported {CURRENT_SCHEMA_VERSION}; run: aicg rebuild"
                )
            conn.execute(
                """
                UPDATE schema_meta
                SET value = ?, updated_at = ?
                WHERE key = 'schema_version'
                """,
                (str(CURRENT_SCHEMA_VERSION), now),
            )
            upgraded = True
    conn.execute(
        """
        INSERT INTO schema_meta (key, value, created_at, updated_at)
        VALUES ('app_name', 'aicg', ?, ?)
        ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (now, now),
    )
    salt = _ensure_hash_salt(conn, now)
    set_hash_salt(salt)
    if upgraded:
        _rehash_user_state(conn)


def _ensure_hash_salt(conn: sqlite3.Connection, now: str) -> str:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'hash_salt'").fetchone()
    if row is not None and row["value"]:
        return str(row["value"])
    salt = secrets.token_hex(32)
    conn.execute(
        """
        INSERT INTO schema_meta (key, value, created_at, updated_at)
        VALUES ('hash_salt', ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (salt, now, now),
    )
    return salt


def _rehash_user_state(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "report_snapshots"):
        for row in conn.execute("SELECT period_type, period_start, report_json FROM report_snapshots").fetchall():
            conn.execute(
                """
                UPDATE report_snapshots
                SET report_hash = ?
                WHERE period_type = ? AND period_start = ?
                """,
                (sha256_text(row["report_json"]), row["period_type"], row["period_start"]),
            )
    if _table_exists(conn, "alert_events"):
        for row in conn.execute("SELECT id, report_hash, config_hash FROM alert_events").fetchall():
            conn.execute(
                """
                UPDATE alert_events
                SET report_hash = ?, config_hash = ?
                WHERE id = ?
                """,
                (
                    sha256_text(f"legacy-alert-report:{row['report_hash']}") if row["report_hash"] else None,
                    sha256_text(f"legacy-alert-config:{row['config_hash']}"),
                    row["id"],
                ),
            )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _assert_required_columns(conn: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    for table, required_columns in REQUIRED_COLUMNS.items():
        if table not in tables:
            raise ValueError(f"required table missing: {table}")
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required_columns - existing)
        if missing:
            raise ValueError(f"required column(s) missing in {table}: {', '.join(missing)}")


def upsert_session(conn: sqlite3.Connection, session: NormalizedSession) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            id, provider, native_session_id, lineage_id, parent_session_id,
            project_path, source_file, source_file_hash,
            source_line_start, source_line_end, started_at, ended_at, status,
            model, task_type, duration_ms,
            retry_count, background_flag, estimated_cost_usd, credit_estimate,
            cost_source, wasted_cost_usd,
            privacy_flags, policy_flags, raw_event_count, malformed_line_count,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            provider=excluded.provider,
            native_session_id=excluded.native_session_id,
            lineage_id=excluded.lineage_id,
            parent_session_id=excluded.parent_session_id,
            project_path=excluded.project_path,
            source_file=excluded.source_file,
            source_file_hash=excluded.source_file_hash,
            source_line_start=excluded.source_line_start,
            source_line_end=excluded.source_line_end,
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
            cost_source=excluded.cost_source,
            wasted_cost_usd=excluded.wasted_cost_usd,
            privacy_flags=excluded.privacy_flags,
            policy_flags=excluded.policy_flags,
            raw_event_count=excluded.raw_event_count,
            malformed_line_count=excluded.malformed_line_count
        """,
        (
            session.id,
            session.provider,
            session.native_session_id,
            session.lineage_id,
            session.parent_session_id,
            session.project_path,
            session.source_file,
            session.source_file_hash,
            session.source_line_start,
            session.source_line_end,
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
            session.cost_source,
            session.wasted_cost_usd,
            session.privacy_flags,
            session.policy_flags,
            session.raw_event_count,
            session.malformed_line_count,
            session.created_at,
        ),
    )


def upsert_turn(conn: sqlite3.Connection, turn: NormalizedTurn) -> None:
    if turn.input_uncached_tokens == 0 and turn.input_tokens:
        cached = min(max(turn.cached_input_tokens, 0), max(turn.input_tokens, 0))
        turn.input_uncached_tokens = max(turn.input_tokens - cached, 0)
        if turn.cache_read_input_tokens == 0 and cached:
            turn.cache_read_input_tokens = cached
    if turn.native_turn_key:
        existing = conn.execute(
            "SELECT id, session_id FROM turns WHERE native_turn_key = ?",
            (turn.native_turn_key,),
        ).fetchone()
        if existing is not None and existing["id"] != turn.id:
            turn.id = existing["id"]
            turn.session_id = existing["session_id"]
    conn.execute(
        """
        INSERT INTO turns (
            id, session_id, provider, native_turn_key, source_file_hash, source_line_start,
            source_line_end, started_at, ended_at, status, model, task_type,
            duration_ms, retry_count, background_flag,
            input_uncached_tokens, input_tokens, cached_input_tokens, output_tokens,
            reasoning_output_tokens, cache_creation_input_tokens,
            cache_read_input_tokens, estimated_cost_usd, credit_estimate,
            cost_source, privacy_flags, policy_flags, token_flags,
            error_type, error_message_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            provider=excluded.provider,
            native_turn_key=COALESCE(turns.native_turn_key, excluded.native_turn_key),
            source_file_hash=excluded.source_file_hash,
            source_line_start=excluded.source_line_start,
            source_line_end=excluded.source_line_end,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            status=excluded.status,
            model=excluded.model,
            task_type=excluded.task_type,
            duration_ms=excluded.duration_ms,
            retry_count=excluded.retry_count,
            background_flag=excluded.background_flag,
            input_uncached_tokens=excluded.input_uncached_tokens,
            input_tokens=excluded.input_tokens,
            cached_input_tokens=excluded.cached_input_tokens,
            output_tokens=excluded.output_tokens,
            reasoning_output_tokens=excluded.reasoning_output_tokens,
            cache_creation_input_tokens=excluded.cache_creation_input_tokens,
            cache_read_input_tokens=excluded.cache_read_input_tokens,
            estimated_cost_usd=excluded.estimated_cost_usd,
            credit_estimate=excluded.credit_estimate,
            cost_source=excluded.cost_source,
            privacy_flags=excluded.privacy_flags,
            policy_flags=excluded.policy_flags,
            token_flags=excluded.token_flags,
            error_type=excluded.error_type,
            error_message_hash=excluded.error_message_hash
        """,
        (
            turn.id,
            turn.session_id,
            turn.provider,
            turn.native_turn_key,
            turn.source_file_hash,
            turn.source_line_start,
            turn.source_line_end,
            turn.started_at,
            turn.ended_at,
            turn.status,
            turn.model,
            turn.task_type,
            turn.duration_ms,
            turn.retry_count,
            int(turn.background_flag),
            turn.input_uncached_tokens,
            turn.input_tokens,
            turn.cached_input_tokens,
            turn.output_tokens,
            turn.reasoning_output_tokens,
            turn.cache_creation_input_tokens,
            turn.cache_read_input_tokens,
            turn.estimated_cost_usd,
            turn.credit_estimate,
            turn.cost_source,
            turn.privacy_flags,
            turn.policy_flags,
            turn.token_flags,
            turn.error_type,
            turn.error_message_hash,
        ),
    )


def upsert_tool_event(conn: sqlite3.Connection, event: NormalizedToolEvent) -> None:
    conn.execute(
        """
        INSERT INTO tool_events (
            id, session_id, turn_id, provider, tool_type, tool_name, status,
            source_file_hash, source_line_start, source_line_end,
            started_at, ended_at, duration_ms, output_bytes, exit_code, call_target
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            turn_id=excluded.turn_id,
            provider=excluded.provider,
            source_file_hash=excluded.source_file_hash,
            source_line_start=excluded.source_line_start,
            source_line_end=excluded.source_line_end,
            tool_type=excluded.tool_type,
            tool_name=excluded.tool_name,
            status=excluded.status,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            duration_ms=excluded.duration_ms,
            output_bytes=excluded.output_bytes,
            exit_code=excluded.exit_code,
            call_target=excluded.call_target
        """,
        (
            event.id,
            event.session_id,
            event.turn_id,
            event.provider,
            event.tool_type,
            event.tool_name,
            event.status,
            event.source_file_hash,
            event.source_line_start,
            event.source_line_end,
            event.started_at,
            event.ended_at,
            event.duration_ms,
            event.output_bytes,
            event.exit_code,
            event.call_target,
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
    conn.execute("DELETE FROM issue_evidence WHERE issue_id = ?", (issue.id,))
    for pointer in issue.evidence_pointers:
        upsert_evidence_pointer(conn, pointer)


def upsert_evidence_pointer(conn: sqlite3.Connection, pointer: EvidencePointer) -> None:
    conn.execute(
        """
        INSERT INTO issue_evidence (
            id, issue_id, session_id, turn_id, tool_event_id, source_file_hash,
            source_line_start, source_line_end, metric_name, metric_value,
            message_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            issue_id=excluded.issue_id,
            session_id=excluded.session_id,
            turn_id=excluded.turn_id,
            tool_event_id=excluded.tool_event_id,
            source_file_hash=excluded.source_file_hash,
            source_line_start=excluded.source_line_start,
            source_line_end=excluded.source_line_end,
            metric_name=excluded.metric_name,
            metric_value=excluded.metric_value,
            message_hash=excluded.message_hash,
            created_at=excluded.created_at
        """,
        (
            pointer.id,
            pointer.issue_id,
            pointer.session_id,
            pointer.turn_id,
            pointer.tool_event_id,
            pointer.source_file_hash,
            pointer.source_line_start,
            pointer.source_line_end,
            pointer.metric_name,
            pointer.metric_value,
            pointer.message_hash,
            pointer.created_at,
        ),
    )


def import_records(conn: sqlite3.Connection, records: ParsedRecords) -> dict[str, int]:
    for session in records.sessions:
        upsert_session(conn, session)
    for turn in records.turns:
        upsert_turn(conn, turn)
    for event in records.tool_events:
        upsert_tool_event(conn, event)
    for finding in records.policy_findings:
        upsert_policy_finding(conn, finding)
    return {
        "sessions": len(records.sessions),
        "turns": len(records.turns),
        "tool_events": len(records.tool_events),
    }


def reset_derived_tables(conn: sqlite3.Connection) -> None:
    for table in sorted(DERIVED_TABLES):
        conn.execute(f"DELETE FROM {table}")


def backup_database(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    backup_path = db_path.with_name(f"{db_path.name}.bak-{utc_now_iso().replace(':', '').replace('Z', 'Z')}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def replace_issues_for_sessions(
    conn: sqlite3.Connection, session_ids: list[str], issues: list[Issue]
) -> None:
    if session_ids:
        for batch in chunked(session_ids):
            placeholders = ",".join("?" for _ in batch)
            conn.execute(
                f"DELETE FROM issues WHERE session_id IN ({placeholders})",
                tuple(batch),
            )
            conn.execute(
                f"DELETE FROM issue_evidence WHERE session_id IN ({placeholders})",
                tuple(batch),
            )
    for issue in issues:
        upsert_issue(conn, issue)


def replace_review_findings_for_sessions(
    conn: sqlite3.Connection,
    session_ids: list[str],
    findings: list[ReviewFinding],
) -> None:
    if session_ids:
        for batch in chunked(session_ids):
            placeholders = ",".join("?" for _ in batch)
            existing = conn.execute(
                f"SELECT id FROM review_findings WHERE session_id IN ({placeholders})",
                tuple(batch),
            ).fetchall()
            finding_ids = [row["id"] for row in existing]
            for finding_batch in chunked(finding_ids):
                finding_placeholders = ",".join("?" for _ in finding_batch)
                conn.execute(
                    f"DELETE FROM review_evidence WHERE finding_id IN ({finding_placeholders})",
                    tuple(finding_batch),
                )
            conn.execute(
                f"DELETE FROM review_findings WHERE session_id IN ({placeholders})",
                tuple(batch),
            )
    for finding in findings:
        upsert_review_finding(conn, finding)


def upsert_review_finding(conn: sqlite3.Connection, finding: ReviewFinding) -> None:
    conn.execute(
        """
        INSERT INTO review_findings (
            id, session_id, ruleset_version, code, confidence, detail,
            recommendation, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            ruleset_version=excluded.ruleset_version,
            code=excluded.code,
            confidence=excluded.confidence,
            detail=excluded.detail,
            recommendation=excluded.recommendation,
            created_at=excluded.created_at
        """,
        (
            finding.id,
            finding.session_id,
            finding.ruleset_version,
            finding.code,
            finding.confidence,
            finding.detail,
            finding.recommendation,
            finding.created_at,
        ),
    )
    for pointer in finding.evidence_pointers:
        upsert_review_evidence(conn, pointer)


def upsert_review_evidence(conn: sqlite3.Connection, pointer: ReviewEvidence) -> None:
    conn.execute(
        """
        INSERT INTO review_evidence (
            id, finding_id, session_id, turn_id, tool_event_id, source_file_hash,
            source_line_start, source_line_end, metric_name, metric_value,
            message_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            finding_id=excluded.finding_id,
            session_id=excluded.session_id,
            turn_id=excluded.turn_id,
            tool_event_id=excluded.tool_event_id,
            source_file_hash=excluded.source_file_hash,
            source_line_start=excluded.source_line_start,
            source_line_end=excluded.source_line_end,
            metric_name=excluded.metric_name,
            metric_value=excluded.metric_value,
            message_hash=excluded.message_hash,
            created_at=excluded.created_at
        """,
        (
            pointer.id,
            pointer.finding_id,
            pointer.session_id,
            pointer.turn_id,
            pointer.tool_event_id,
            pointer.source_file_hash,
            pointer.source_line_start,
            pointer.source_line_end,
            pointer.metric_name,
            pointer.metric_value,
            pointer.message_hash,
            pointer.created_at,
        ),
    )


def insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    command: str,
    provider: str,
    started_at: str,
    ended_at: str,
    exit_code: int,
    raw_path: str | None,
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


def clear_scan_error(conn: sqlite3.Connection, *, provider: str, source_file: str) -> None:
    conn.execute(
        "DELETE FROM scan_errors WHERE provider = ? AND source_file = ?",
        (provider, source_file),
    )


def scan_state_for(conn: sqlite3.Connection, source_file: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT source_file, provider, file_hash, file_size, mtime, last_line, last_scanned_at
        FROM scan_state
        WHERE source_file = ?
        """,
        (source_file,),
    ).fetchone()


def upsert_scan_state(
    conn: sqlite3.Connection,
    *,
    provider: str,
    source_file: str,
    file_hash: str,
    file_size: int,
    mtime: float,
    last_line: int,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO scan_state (
            source_file, provider, file_hash, file_size, mtime, last_line, last_scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_file) DO UPDATE SET
            provider=excluded.provider,
            file_hash=excluded.file_hash,
            file_size=excluded.file_size,
            mtime=excluded.mtime,
            last_line=excluded.last_line,
            last_scanned_at=excluded.last_scanned_at
        """,
        (source_file, provider, file_hash, file_size, mtime, last_line, now),
    )


def upsert_policy_finding(conn: sqlite3.Connection, finding: PolicyFinding) -> None:
    conn.execute(
        """
        INSERT INTO policy_findings (
            id, session_id, tool_event_id, rule_id, level, surface, detail_hash,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            tool_event_id=excluded.tool_event_id,
            rule_id=excluded.rule_id,
            level=excluded.level,
            surface=excluded.surface,
            detail_hash=excluded.detail_hash,
            last_seen_at=excluded.last_seen_at
        """,
        (
            finding.id,
            finding.session_id,
            finding.tool_event_id,
            finding.rule_id,
            finding.level,
            finding.surface,
            finding.detail_hash,
            finding.first_seen_at,
            finding.last_seen_at,
        ),
    )


def ack_policy_finding(conn: sqlite3.Connection, finding_id: str, reason: str | None) -> None:
    conn.execute(
        """
        INSERT INTO policy_acks (finding_id, reason, acked_at)
        VALUES (?, ?, ?)
        ON CONFLICT(finding_id) DO UPDATE SET
            reason=excluded.reason,
            acked_at=excluded.acked_at
        """,
        (finding_id, reason, utc_now_iso()),
    )


def upsert_report_snapshot(
    conn: sqlite3.Connection,
    *,
    period_type: str,
    period_start: str,
    report_json: str,
    report_hash: str,
    dashboard_model_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO report_snapshots (
            period_type, period_start, report_json, report_hash, dashboard_model_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(period_type, period_start) DO UPDATE SET
            report_json=excluded.report_json,
            report_hash=excluded.report_hash,
            dashboard_model_json=excluded.dashboard_model_json,
            created_at=excluded.created_at
        """,
        (period_type, period_start, report_json, report_hash, dashboard_model_json, utc_now_iso()),
    )


def insert_alert_events(conn: sqlite3.Connection, events: list[AlertEvent]) -> None:
    for event in events:
        conn.execute(
            """
            INSERT INTO alert_events (
                id, period_type, period_start, alert_key, threshold_value,
                actual_value, report_hash, config_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.period_type,
                event.period_start,
                event.alert_key,
                event.threshold_value,
                event.actual_value,
                event.report_hash,
                event.config_hash,
                event.created_at,
            ),
        )


def upsert_git_link(conn: sqlite3.Connection, *, repo_path: str, project_path: str) -> None:
    conn.execute(
        """
        INSERT INTO git_links (repo_path, project_path, linked_at)
        VALUES (?, ?, ?)
        ON CONFLICT(repo_path) DO UPDATE SET
            project_path=excluded.project_path,
            linked_at=excluded.linked_at
        """,
        (repo_path, project_path, utc_now_iso()),
    )


def upsert_git_activity(
    conn: sqlite3.Connection,
    *,
    project_path: str,
    period_start: str,
    commits: int,
    merge_commits: int,
    insertions: int,
    deletions: int,
) -> None:
    conn.execute(
        """
        INSERT INTO git_activity (
            project_path, period_start, commits, merge_commits, insertions, deletions, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_path, period_start) DO UPDATE SET
            commits=excluded.commits,
            merge_commits=excluded.merge_commits,
            insertions=excluded.insertions,
            deletions=excluded.deletions,
            synced_at=excluded.synced_at
        """,
        (project_path, period_start, commits, merge_commits, insertions, deletions, utc_now_iso()),
    )


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    allowed = {
        "schema_meta",
        "sessions",
        "turns",
        "tool_events",
        "issues",
        "issue_evidence",
        "runs",
        "scan_errors",
        "scan_state",
        "policy_findings",
        "policy_acks",
        "report_snapshots",
        "git_links",
        "git_activity",
        "alert_events",
    }
    if table not in allowed:
        raise ValueError(f"Unsupported table: {table}")
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])
