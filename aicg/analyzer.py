from __future__ import annotations

import sqlite3

from .models import EvidencePointer, Issue
from .sqlite_utils import chunked
from .tokens import total_tokens
from .util import split_flags, stable_id, utc_now_iso


def analyze_sessions(
    conn: sqlite3.Connection, session_ids: list[str], config: dict
) -> list[Issue]:
    thresholds = config.get("thresholds", {})
    issues: list[Issue] = []
    for session_id in session_ids:
        session_row = conn.execute(
            """
            SELECT id, provider, project_path, status, model, retry_count,
                   background_flag, privacy_flags, policy_flags,
                   source_file_hash, source_line_start, source_line_end
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_row is None:
            continue

        token_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(input_uncached_tokens), 0) AS input_uncached_tokens,
                COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(reasoning_output_tokens), 0) AS reasoning_output_tokens,
                COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
                COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read_input_tokens,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_turns,
                COALESCE(SUM(retry_count), 0) AS retry_count,
                COALESCE(COUNT(DISTINCT CASE WHEN model IS NOT NULL AND model != 'unknown' THEN model END), 0) AS model_count,
                MIN(CASE WHEN error_message_hash IS NOT NULL THEN error_message_hash END) AS error_message_hash
            FROM turns
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        tool_row = conn.execute(
            """
            SELECT
                COALESCE(MAX(output_bytes), 0) AS max_tool_output,
                COALESCE(SUM(output_bytes), 0) AS total_tool_output,
                COALESCE(SUM(CASE WHEN tool_type = 'mcp' THEN 1 ELSE 0 END), 0) AS mcp_calls
            FROM tool_events
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

        input_tokens = int(token_row["input_tokens"])
        input_uncached_tokens = int(token_row["input_uncached_tokens"])
        cache_read_tokens = int(token_row["cache_read_input_tokens"])
        output_tokens = int(token_row["output_tokens"])
        token_values = {key: token_row[key] for key in token_row.keys()}
        token_values["provider"] = session_row["provider"]
        session_total_tokens = total_tokens(token_values)
        retry_count = max(int(token_row["retry_count"] or 0), int(session_row["retry_count"] or 0))
        max_tool_output = int(tool_row["max_tool_output"])
        total_tool_output = int(tool_row["total_tool_output"])
        mcp_calls = int(tool_row["mcp_calls"])

        if session_total_tokens > int(thresholds.get("high_session_tokens", 200000)):
            issues.append(
                _issue(
                    session_id,
                    "high",
                    "HIGH_COST_TASK",
                    "High cost task",
                    f"Session used {session_total_tokens} total tokens.",
                    "Split large tasks, reduce context, or move long artifacts into files.",
                    f"total_tokens={session_total_tokens}",
                    metric_name="total_tokens",
                    metric_value=session_total_tokens,
                    source_row=session_row,
                )
            )

        ratio_threshold = float(thresholds.get("high_output_token_ratio", 0.5))
        output_threshold = int(thresholds.get("high_output_tokens", 10000))
        if output_tokens > output_threshold and output_tokens / max(input_tokens, 1) > ratio_threshold:
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "HIGH_OUTPUT_TOKEN_RATIO",
                    "High output token ratio",
                    f"Output tokens were {output_tokens} against {input_tokens} input tokens.",
                    "Ask for tighter responses or redirect long artifacts into files.",
                    f"output_tokens={output_tokens};input_tokens={input_tokens}",
                    metric_name="output_tokens",
                    metric_value=output_tokens,
                    source_row=session_row,
                )
            )

        if retry_count >= int(thresholds.get("retry_loop_turns", 3)):
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "RETRY_LOOP",
                    "Possible retry loop",
                    f"Session had {retry_count} failed or retry turns.",
                    "Inspect repeated failures before continuing the same task.",
                    f"retry_count={retry_count}",
                    metric_name="retry_count",
                    metric_value=retry_count,
                    source_row=session_row,
                    message_hash=token_row["error_message_hash"],
                )
            )

        background_limit = int(thresholds.get("background_session_tokens", 50000))
        if int(session_row["background_flag"] or 0) and session_total_tokens > background_limit:
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "BACKGROUND_CONSUMPTION",
                    "Background consumption",
                    f"Background or subagent session used {session_total_tokens} total tokens.",
                    "Review whether background work should be capped or made explicit.",
                    f"total_tokens={session_total_tokens}",
                    metric_name="total_tokens",
                    metric_value=session_total_tokens,
                    source_row=session_row,
                )
            )

        if int(token_row["model_count"]) > 1:
            issues.append(
                _issue(
                    session_id,
                    "low",
                    "MODEL_SWITCH_ANOMALY",
                    "Model switch anomaly",
                    f"Session used {token_row['model_count']} different model values.",
                    "Check profile and model fallback settings for unexpected switches.",
                    f"model_count={token_row['model_count']}",
                    metric_name="model_count",
                    metric_value=token_row["model_count"],
                    source_row=session_row,
                )
            )

        cache_denominator = input_uncached_tokens + cache_read_tokens
        min_cache_input = int(thresholds.get("low_cache_hit_min_input_tokens", 50000))
        low_cache_ratio = float(thresholds.get("low_cache_hit_ratio", 0.2))
        if cache_denominator >= min_cache_input and cache_read_tokens / max(cache_denominator, 1) < low_cache_ratio:
            issues.append(
                _issue(
                    session_id,
                    "low",
                    "LOW_CACHE_HIT",
                    "Low cache hit ratio",
                    f"Cache-read input was {cache_read_tokens} of {cache_denominator} cache-eligible input tokens.",
                    "Review session organization and prompt reuse so large stable context can be cached.",
                    f"cache_read={cache_read_tokens};input_uncached={input_uncached_tokens}",
                    metric_name="cache_hit_ratio",
                    metric_value=f"{cache_read_tokens / max(cache_denominator, 1):.4f}",
                    source_row=session_row,
                )
            )

        single_limit = int(thresholds.get("single_tool_output_bloat_bytes", 102400))
        session_limit = int(thresholds.get("session_tool_output_bloat_bytes", 1048576))
        if max_tool_output > single_limit or total_tool_output > session_limit:
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "TOOL_OUTPUT_BLOAT",
                    "Tool output bloat",
                    f"Tool output reached {max_tool_output} bytes for one call and {total_tool_output} bytes total.",
                    "Prefer filtered commands and summarize large files before sending them to the agent.",
                    f"max_tool_output={max_tool_output};total_tool_output={total_tool_output}",
                    metric_name="total_tool_output",
                    metric_value=total_tool_output,
                    source_row=session_row,
                )
            )

        if mcp_calls > int(thresholds.get("mcp_overuse_count", 30)):
            issues.append(
                _issue(
                    session_id,
                    "low",
                    "MCP_OVERUSE",
                    "High MCP tool call count",
                    f"Session made {mcp_calls} MCP tool calls.",
                    "Batch related lookups and cache repeated tool results locally.",
                    f"mcp_calls={mcp_calls}",
                    metric_name="mcp_calls",
                    metric_value=mcp_calls,
                    source_row=session_row,
                )
            )

        privacy_flags = split_flags(session_row["privacy_flags"])
        policy_flags = split_flags(session_row["policy_flags"])
        if "RAW_PAYLOAD_RISK" in privacy_flags:
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "RAW_PAYLOAD_RISK",
                    "Raw payload risk",
                    "A large content-like payload was observed in local logs.",
                    "Prefer filtered captures and avoid persisting raw prompts or command output.",
                    "privacy_flags=RAW_PAYLOAD_RISK",
                    metric_name="privacy_flags",
                    metric_value="RAW_PAYLOAD_RISK",
                    source_row=session_row,
                )
            )
        if "POSSIBLE_SECRET" in privacy_flags:
            issues.append(
                _issue(
                    session_id,
                    "high",
                    "POSSIBLE_SECRET",
                    "Possible secret in logs",
                    "Secret-like text appeared in a captured argument, content, or tool payload.",
                    "Rotate exposed credentials if real, then remove or quarantine the raw local log.",
                    "privacy_flags=POSSIBLE_SECRET",
                    metric_name="privacy_flags",
                    metric_value="POSSIBLE_SECRET",
                    source_row=session_row,
                )
            )
        if "RESTRICTED_SERVICE_CALL" in policy_flags:
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "RESTRICTED_SERVICE_CALL",
                    "Restricted service call",
                    "A tool payload referenced a configured AI or remote provider endpoint.",
                    "Confirm the call is allowed by workspace policy before reusing this workflow.",
                    "policy_flags=RESTRICTED_SERVICE_CALL",
                    metric_name="policy_flags",
                    metric_value="RESTRICTED_SERVICE_CALL",
                    source_row=session_row,
                )
            )

    issues.extend(_project_failure_hotspots(conn, session_ids, thresholds))
    return issues


def _project_failure_hotspots(
    conn: sqlite3.Connection,
    session_ids: list[str],
    thresholds: dict,
) -> list[Issue]:
    if not session_ids:
        return []
    rows: list[sqlite3.Row] = []
    for batch in chunked(session_ids):
        placeholders = ",".join("?" for _ in batch)
        rows.extend(
            conn.execute(
                f"""
                SELECT id, COALESCE(project_path, 'unknown') AS project_path,
                       status, retry_count, source_file_hash,
                       source_line_start, source_line_end
                FROM sessions
                WHERE id IN ({placeholders})
                """,
                tuple(batch),
            ).fetchall()
        )
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        project = row["project_path"]
        bucket = grouped.setdefault(
            project,
            {
                "project_path": project,
                "sessions": set(),
                "failed_sessions": set(),
                "sample_session_id": row["id"],
                "source_file_hash": row["source_file_hash"],
                "source_line_start": row["source_line_start"],
                "source_line_end": row["source_line_end"],
            },
        )
        bucket["sessions"].add(row["id"])  # type: ignore[union-attr]
        if row["status"] == "failed" or int(row["retry_count"] or 0) > 0:
            bucket["failed_sessions"].add(row["id"])  # type: ignore[union-attr]
            bucket["sample_session_id"] = row["id"]
            bucket["source_file_hash"] = row["source_file_hash"]
            bucket["source_line_start"] = row["source_line_start"]
            bucket["source_line_end"] = row["source_line_end"]
    min_sessions = int(thresholds.get("project_hotspot_min_sessions", 2))
    min_rate = float(thresholds.get("project_hotspot_failure_rate", 0.5))
    issues: list[Issue] = []
    for row in grouped.values():
        sessions = len(row["sessions"])  # type: ignore[arg-type]
        failed = len(row["failed_sessions"])  # type: ignore[arg-type]
        if sessions >= min_sessions and failed / max(sessions, 1) >= min_rate:
            issues.append(
                _issue(
                    str(row["sample_session_id"]),
                    "medium",
                    "PROJECT_FAILURE_HOTSPOT",
                    "Project failure hotspot",
                    f"{failed}/{sessions} recent sessions failed or retried for {row['project_path']}.",
                    "Inspect project-specific setup, network, and permission failures before more agent runs.",
                    f"project_path={row['project_path']};failed_sessions={failed};sessions={sessions}",
                    metric_name="failed_sessions",
                    metric_value=failed,
                    source_row=row,
                )
            )
    return issues


def _issue(
    session_id: str,
    severity: str,
    code: str,
    title: str,
    detail: str,
    recommendation: str,
    evidence: str,
    *,
    metric_name: str,
    metric_value: object,
    source_row: sqlite3.Row | None = None,
    turn_id: str | None = None,
    tool_event_id: str | None = None,
    message_hash: str | None = None,
) -> Issue:
    issue_id = stable_id("issue", session_id, code, evidence)
    pointer = EvidencePointer(
        id=stable_id("evidence", issue_id, session_id, metric_name, metric_value),
        issue_id=issue_id,
        session_id=session_id,
        turn_id=turn_id,
        tool_event_id=tool_event_id,
        source_file_hash=_row_value(source_row, "source_file_hash"),
        source_line_start=_row_int(source_row, "source_line_start"),
        source_line_end=_row_int(source_row, "source_line_end"),
        metric_name=metric_name,
        metric_value=str(metric_value),
        message_hash=message_hash,
        created_at=utc_now_iso(),
    )
    return Issue(
        id=issue_id,
        session_id=session_id,
        severity=severity,
        code=code,
        title=title,
        detail=detail,
        recommendation=recommendation,
        evidence=evidence,
        created_at=utc_now_iso(),
        evidence_pointers=[pointer],
    )


def _row_value(row: sqlite3.Row | None, key: str) -> str | None:
    if row is None:
        return None
    try:
        value = row[key]
    except (KeyError, IndexError):
        return None
    return str(value) if value is not None else None


def _row_int(row: sqlite3.Row | None, key: str) -> int | None:
    value = _row_value(row, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
