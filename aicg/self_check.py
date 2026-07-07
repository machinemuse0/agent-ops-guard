from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path
from typing import Any

from .config import app_paths
from .db import CURRENT_SCHEMA_VERSION, REQUIRED_COLUMNS as DB_REQUIRED_COLUMNS
from .reporter import build_daily_report
from .util import utc_now_iso


REQUIRED_TABLES = {
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
}


REQUIRED_COLUMNS = DB_REQUIRED_COLUMNS


REQUIRED_INDEXES = {
    "idx_sessions_started_at",
    "idx_turns_session_id",
    "idx_tool_events_session_id",
    "idx_issues_session_id",
    "idx_issue_evidence_issue_id",
    "idx_scan_errors_created_at",
    "idx_turns_native_turn_key",
    "idx_scan_state_provider",
    "idx_policy_findings_session_id",
    "idx_git_activity_project_period",
}


PRICE_FIELDS = {
    "input_per_mtok_usd",
    "cached_input_per_mtok_usd",
    "output_per_mtok_usd",
    "reasoning_output_per_mtok_usd",
    "cache_creation_input_per_mtok_usd",
    "cache_read_input_per_mtok_usd",
    "credit_per_usd",
}


def run_self_check(app_dir: Path | None = None) -> dict[str, Any]:
    paths = app_paths(app_dir)
    checks: list[dict[str, Any]] = []
    checks.extend(_check_config(paths["config"]))
    checks.extend(_check_db(paths["db"]))
    status = _overall_status(checks)
    return {
        "generatedAt": utc_now_iso(),
        "status": status,
        "checks": checks,
    }


def _check_config(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return [_check("config.exists", "warning", f"config missing: {path}")]
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [_check("config.parse", "fail", "config parse failed", errorType=type(exc).__name__)]
    checks = [_check("config.parse", "pass", "config parses")]
    prices = data.get("prices") if isinstance(data.get("prices"), dict) else {}
    bad_fields = []
    for key, value in prices.items():
        if not isinstance(value, dict):
            bad_fields.append(f"{key}:not_table")
            continue
        for field in PRICE_FIELDS:
            if field in value and not _is_number(value[field]):
                bad_fields.append(f"{key}.{field}")
    if bad_fields:
        checks.append(
            _check(
                "config.prices_numeric",
                "fail",
                "price table contains non-numeric fields",
                fields=bad_fields,
            )
        )
    else:
        checks.append(_check("config.prices_numeric", "pass", "price fields are numeric"))
    return checks


def _check_db(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return [_check("db.exists", "fail", f"SQLite DB missing: {path}")]
    checks = [_check("db.exists", "pass", "SQLite DB exists")]
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return checks + [_check("db.open", "fail", "SQLite DB open failed", errorType=type(exc).__name__)]
    with conn:
        tables = _sqlite_names(conn, "table")
        missing_tables = sorted(REQUIRED_TABLES - tables)
        checks.append(
            _check(
                "db.required_tables",
                "fail" if missing_tables else "pass",
                "required tables present" if not missing_tables else "required tables missing",
                missing=missing_tables,
            )
        )
        for table, required_columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                continue
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing_columns = sorted(required_columns - columns)
            checks.append(
                _check(
                    f"db.columns.{table}",
                    "fail" if missing_columns else "pass",
                    f"{table} columns present" if not missing_columns else f"{table} columns missing",
                    missing=missing_columns,
                )
            )
        checks.append(_check_schema_version(conn, tables))
        indexes = _sqlite_names(conn, "index")
        missing_indexes = sorted(REQUIRED_INDEXES - indexes)
        checks.append(
            _check(
                "db.required_indexes",
                "fail" if missing_indexes else "pass",
                "required indexes present" if not missing_indexes else "required indexes missing",
                missing=missing_indexes,
            )
        )
        if "scan_errors" in tables:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_errors)")}
            checks.append(
                _check(
                    "privacy.scan_errors_no_raw_message",
                    "fail" if "error_message" in columns else "pass",
                    "scan_errors stores hashes only" if "error_message" not in columns else "scan_errors has raw message column",
                )
            )
        if "policy_findings" in tables:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(policy_findings)")}
            checks.append(
                _check(
                    "privacy.policy_findings_hash_only",
                    "fail" if "detail" in columns or "raw_payload" in columns else "pass",
                    "policy_findings stores hashes only" if "detail" not in columns and "raw_payload" not in columns else "policy_findings has raw detail column",
                )
            )
        checks.append(_check_report_consistency(conn, tables))
    return checks


def _check_schema_version(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "schema_meta" not in tables:
        return _check("db.schema_version", "fail", "schema_meta missing")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return _check("db.schema_version", "fail", "schema_version missing")
    try:
        version = int(row["value"])
    except (TypeError, ValueError):
        return _check("db.schema_version", "fail", "schema_version is not an integer", value=row["value"])
    return _check(
        "db.schema_version",
        "pass" if version == CURRENT_SCHEMA_VERSION else "fail",
        f"schema_version={version}",
        expected=CURRENT_SCHEMA_VERSION,
        actual=version,
    )


def _check_report_consistency(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if not {"sessions", "turns", "tool_events", "issues", "issue_evidence", "scan_errors"} <= tables:
        return _check("report.consistency", "fail", "report consistency skipped because required tables are missing")
    try:
        report = build_daily_report(conn, since="9999d")
    except Exception as exc:
        return _check("report.consistency", "fail", "daily report model failed", errorType=type(exc).__name__)
    expected_sessions = int(conn.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()["count"])
    actual_sessions = int(report["overview"]["sessions"])
    required_keys = {
        "schemaVersion",
        "generatedAt",
        "since",
        "overview",
        "projectRanking",
        "providerModelBreakdown",
        "taskRollups",
        "failureRetry",
        "backgroundAnomalies",
        "toolUsage",
        "privacyPolicyFindings",
        "policyLifecycle",
        "wasteBreakdown",
        "efficiency",
        "gitActivity",
        "highRiskIssues",
        "recommendedFixes",
        "consistencyChecks",
    }
    missing_keys = sorted(required_keys - set(report.keys()))
    status = "pass" if actual_sessions == expected_sessions and not missing_keys else "fail"
    return _check(
        "report.consistency",
        status,
        "daily report counts are reproducible" if status == "pass" else "daily report consistency failed",
        expectedSessions=expected_sessions,
        actualSessions=actual_sessions,
        missingKeys=missing_keys,
    )


def _sqlite_names(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (kind,),
        ).fetchall()
    }


def _overall_status(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "fail"
    if any(check["status"] == "warning" for check in checks):
        return "warning"
    return "pass"


def _check(check_id: str, status: str, summary: str, **extra: Any) -> dict[str, Any]:
    item = {"id": check_id, "status": status, "summary": summary}
    item.update(extra)
    return item


def _is_number(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value))
    except (TypeError, ValueError):
        return False
    return True
