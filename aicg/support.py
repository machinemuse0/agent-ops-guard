from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

from . import __version__
from .config import app_paths
from .db import CURRENT_SCHEMA_VERSION, connect
from .review import REVIEW_RULESET_VERSION
from .self_check import run_self_check
from .util import SECRET_PATTERNS, SENSITIVE_PATH_PATTERNS, redact_secrets, utc_now_iso


BUNDLE_SCHEMA_VERSION = 1
ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|private/tmp|tmp)/[^\s\"'<>|,;)]+")


class SupportBundleError(ValueError):
    pass


def build_support_bundle(app_dir: Path | None = None, *, include_config: bool = False) -> dict[str, Any]:
    paths = app_paths(app_dir)
    with connect(paths["db"]) as conn:
        bundle: dict[str, Any] = {
            "schemaVersion": BUNDLE_SCHEMA_VERSION,
            "generatedAt": utc_now_iso(),
            "aicg": {
                "version": __version__,
                "schemaVersion": CURRENT_SCHEMA_VERSION,
                "rulesetVersion": REVIEW_RULESET_VERSION,
            },
            "doctorSelfCheck": _safe_self_check(run_self_check(app_dir)),
            "formatObservations": _format_observations(conn),
            "tableRows": _table_rows(conn),
            "performance": {
                "recentScanDurationMs": None,
                "recentSummaryDurationMs": None,
                "source": "not_recorded",
            },
            "anonymousStats": _anonymous_stats(conn),
            "includesConfig": include_config,
            "sharing": {
                "automaticUpload": False,
                "reviewRequired": True,
            },
        }
    if include_config:
        bundle["config"] = _safe_config(paths["config"])
    return _sanitize_bundle(bundle)


def write_support_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    leaks = bundle_leak_findings(text)
    if leaks:
        raise SupportBundleError(f"support bundle failed privacy self-check: {'; '.join(leaks[:5])}")
    path.write_text(text, encoding="utf-8")


def bundle_leak_findings(text: str) -> list[str]:
    findings: list[str] = []
    if "hash_salt" in text:
        findings.append("contains hash_salt")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        findings.append("contains secret pattern")
    if any(pattern.search(text) for pattern in SENSITIVE_PATH_PATTERNS):
        findings.append("contains sensitive path pattern")
    if ABSOLUTE_PATH_PATTERN.search(text):
        findings.append("contains absolute path")
    return findings


def _format_observations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "provider": row["provider"],
            "observationKey": row["observation_key"],
            "count": int(row["count"] or 0),
            "firstSeenAt": row["first_seen_at"],
            "lastSeenAt": row["last_seen_at"],
        }
        for row in conn.execute(
            """
            SELECT provider, observation_key, count, first_seen_at, last_seen_at
            FROM format_observations
            ORDER BY provider, observation_key
            """
        ).fetchall()
    ]


def _table_rows(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row["name"])
        counts[name] = int(conn.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()["count"] or 0)
    return counts


def _anonymous_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    provider_rows = conn.execute(
        """
        SELECT provider, COUNT(*) AS sessions
        FROM sessions
        GROUP BY provider
        ORDER BY provider
        """
    ).fetchall()
    token_rows = conn.execute(
        """
        SELECT
            COALESCE(input_uncached_tokens, 0)
            + COALESCE(cache_creation_input_tokens, 0)
            + COALESCE(cache_read_input_tokens, 0)
            + COALESCE(output_tokens, 0) AS tokens
        FROM turns
        ORDER BY tokens
        """
    ).fetchall()
    values = [int(row["tokens"] or 0) for row in token_rows]
    return {
        "providerShare": [
            {"provider": row["provider"], "sessions": int(row["sessions"] or 0)}
            for row in provider_rows
        ],
        "tokenDistribution": {
            "count": len(values),
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
        },
    }


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, round((len(values) - 1) * percentile)))
    return values[index]


def _safe_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing"}
    try:
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {"status": "parse_error", "errorType": type(exc).__name__}
    return {"status": "included", "value": loaded}


def _safe_self_check(model: dict[str, Any]) -> dict[str, Any]:
    model = _sanitize_bundle(model)
    for check in model.get("checks", []) if isinstance(model.get("checks"), list) else []:
        if isinstance(check, dict) and check.get("id") == "db.hash_salt":
            check["id"] = "db.local_hash_key"
            check["summary"] = str(check.get("summary") or "").replace("hash_salt", "local hash key")
    return _sanitize_bundle(model)


def _sanitize_bundle(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = str(key).replace("hash_salt", "local_hash_key")
            sanitized[safe_key] = _sanitize_bundle(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_bundle(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    text = redact_secrets(value).replace("hash_salt", "local_hash_key")
    return ABSOLUTE_PATH_PATTERN.sub("/REDACTED/path", text)
