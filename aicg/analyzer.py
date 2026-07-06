from __future__ import annotations

import sqlite3

from .models import Issue
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
                   background_flag, privacy_flags, policy_flags
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
                COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(reasoning_output_tokens), 0) AS reasoning_output_tokens,
                COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
                COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read_input_tokens,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_turns,
                COALESCE(COUNT(DISTINCT COALESCE(model, 'unknown')), 0) AS model_count
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
        output_tokens = int(token_row["output_tokens"])
        total_tokens = sum(
            int(token_row[name])
            for name in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        failed_turns = int(token_row["failed_turns"])
        retry_count = max(failed_turns, int(session_row["retry_count"] or 0))
        max_tool_output = int(tool_row["max_tool_output"])
        total_tool_output = int(tool_row["total_tool_output"])
        mcp_calls = int(tool_row["mcp_calls"])

        if total_tokens > int(thresholds.get("high_session_tokens", 200000)):
            issues.append(
                _issue(
                    session_id,
                    "high",
                    "HIGH_COST_TASK",
                    "High cost task",
                    f"Session used {total_tokens} total tokens.",
                    "Split large tasks, reduce context, or move long artifacts into files.",
                    f"total_tokens={total_tokens}",
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
                )
            )

        background_limit = int(thresholds.get("background_session_tokens", 50000))
        if int(session_row["background_flag"] or 0) and total_tokens > background_limit:
            issues.append(
                _issue(
                    session_id,
                    "medium",
                    "BACKGROUND_CONSUMPTION",
                    "Background consumption",
                    f"Background or subagent session used {total_tokens} total tokens.",
                    "Review whether background work should be capped or made explicit.",
                    f"total_tokens={total_tokens}",
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
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"""
        SELECT COALESCE(project_path, 'unknown') AS project_path,
               COUNT(*) AS sessions,
               COALESCE(SUM(CASE WHEN status = 'failed' OR retry_count > 0 THEN 1 ELSE 0 END), 0) AS failed_sessions,
               MIN(id) AS sample_session_id
        FROM sessions
        WHERE id IN ({placeholders})
        GROUP BY COALESCE(project_path, 'unknown')
        """,
        tuple(session_ids),
    ).fetchall()
    min_sessions = int(thresholds.get("project_hotspot_min_sessions", 2))
    min_rate = float(thresholds.get("project_hotspot_failure_rate", 0.5))
    issues: list[Issue] = []
    for row in rows:
        sessions = int(row["sessions"])
        failed = int(row["failed_sessions"])
        if sessions >= min_sessions and failed / max(sessions, 1) >= min_rate:
            issues.append(
                _issue(
                    row["sample_session_id"],
                    "medium",
                    "PROJECT_FAILURE_HOTSPOT",
                    "Project failure hotspot",
                    f"{failed}/{sessions} recent sessions failed or retried for {row['project_path']}.",
                    "Inspect project-specific setup, network, and permission failures before more agent runs.",
                    f"project_path={row['project_path']};failed_sessions={failed};sessions={sessions}",
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
) -> Issue:
    return Issue(
        id=stable_id("issue", session_id, code, evidence),
        session_id=session_id,
        severity=severity,
        code=code,
        title=title,
        detail=detail,
        recommendation=recommendation,
        evidence=evidence,
        created_at=utc_now_iso(),
    )
