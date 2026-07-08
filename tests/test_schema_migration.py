import sqlite3

import pytest

from aicg.db import CURRENT_SCHEMA_VERSION, connect, count_rows, init_db, reset_derived_tables


def test_init_db_migrates_v011_database_to_schema_v2(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    _create_v011_database(db_path)

    init_db(db_path)
    init_db(db_path)

    with connect(db_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        turn_columns = {row["name"] for row in conn.execute("PRAGMA table_info(turns)")}
        tool_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_events)")}
        evidence_columns = {row["name"] for row in conn.execute("PRAGMA table_info(issue_evidence)")}
        snapshot_columns = {row["name"] for row in conn.execute("PRAGMA table_info(report_snapshots)")}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

        assert int(version) == CURRENT_SCHEMA_VERSION
        assert count_rows(conn, "sessions") == 1
        assert count_rows(conn, "turns") == 1
        assert count_rows(conn, "issues") == 1
        assert count_rows(conn, "scan_errors") == 1
        assert count_rows(conn, "alert_events") == 0
        assert {"source_line_start", "source_line_end"} <= session_columns
        assert {"source_file_hash", "source_line_start", "source_line_end"} <= turn_columns
        assert {"source_file_hash", "source_line_start", "source_line_end"} <= tool_columns
        assert {
            "issue_id",
            "source_file_hash",
            "metric_name",
            "metric_value",
            "message_hash",
        } <= evidence_columns
        assert "dashboard_model_json" in snapshot_columns
        assert "alert_events" in tables


def test_init_db_rejects_newer_schema_version(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(CURRENT_SCHEMA_VERSION + 1),),
        )
        conn.commit()

    with pytest.raises(ValueError, match="newer than supported"):
        init_db(db_path)


def test_schema_v7_upgrade_requires_explicit_rebuild_and_preserves_alert_events(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO alert_events (
                id, period_type, period_start, alert_key, threshold_value,
                actual_value, report_hash, config_hash, created_at
            ) VALUES ('alert-one', 'day', '2026-07-07T00:00:00+08:00',
                      'interrupted_sessions_max', 0, 1, 'hash', 'config', '2026-07-07T00:00:00Z')
            """
        )
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(CURRENT_SCHEMA_VERSION - 1),),
        )
        conn.commit()

    with pytest.raises(ValueError, match="older than supported"):
        init_db(db_path)
    init_db(db_path, allow_schema_upgrade=True)
    with connect(db_path) as conn:
        reset_derived_tables(conn)
        conn.commit()
        version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()["value"]
        assert int(version) == CURRENT_SCHEMA_VERSION
        assert count_rows(conn, "alert_events") == 1


def test_old_schema_open_without_rebuild_does_not_partially_mutate(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO schema_meta VALUES ('schema_version', '7', 'n', 'n');
            CREATE TABLE report_snapshots (
                period_type TEXT NOT NULL,
                period_start TEXT NOT NULL,
                report_json TEXT NOT NULL,
                report_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (period_type, period_start)
            );
            """
        )
    conn.close()

    with pytest.raises(ValueError, match="older than supported"):
        init_db(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(report_snapshots)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        meta = dict(conn.execute("SELECT key, value FROM schema_meta"))
    assert "dashboard_model_json" not in snapshot_columns
    assert "alert_events" not in tables
    assert "hash_salt" not in meta


def _create_v011_database(db_path):
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
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
            CREATE TABLE turns (
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
            CREATE TABLE tool_events (
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
            CREATE TABLE issues (
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
            CREATE TABLE scan_errors (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                source_file TEXT NOT NULL,
                error_type TEXT,
                error_message_hash TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions (id, provider, started_at, status, created_at)
            VALUES ('session-old', 'codex', '2026-07-01T00:00:00Z', 'completed', '2026-07-01T00:00:00Z');
            INSERT INTO turns (id, session_id, provider, status, input_tokens)
            VALUES ('turn-old', 'session-old', 'codex', 'completed', 100);
            INSERT INTO issues (id, session_id, severity, code, title, evidence, created_at)
            VALUES ('issue-old', 'session-old', 'low', 'OLD', 'Old issue', 'metric=1', '2026-07-01T00:00:00Z');
            INSERT INTO scan_errors (id, provider, source_file, error_type, error_message_hash, created_at)
            VALUES ('scan-error-old', 'codex', '/tmp/bad.jsonl', 'RuntimeError', 'abc', '2026-07-01T00:00:00Z');
            """
        )
    conn.close()
