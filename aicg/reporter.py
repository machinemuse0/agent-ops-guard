from __future__ import annotations

import sqlite3

from .util import isoformat_utc, parse_since, split_flags, utc_now_iso


def generate_markdown_report(
    conn: sqlite3.Connection, since: str = "24h", config: dict | None = None
) -> str:
    cutoff = parse_since(since)
    cutoff_iso = isoformat_utc(cutoff)
    sessions = conn.execute(
        """
        SELECT id, provider, COALESCE(project_path, 'unknown') AS project_path,
               COALESCE(model, 'unknown') AS model, COALESCE(task_type, 'unknown') AS task_type,
               status, COALESCE(started_at, created_at) AS started_at,
               COALESCE(duration_ms, 0) AS duration_ms,
               COALESCE(retry_count, 0) AS retry_count,
               COALESCE(background_flag, 0) AS background_flag,
               privacy_flags, policy_flags
        FROM sessions
        WHERE COALESCE(started_at, created_at) >= ?
        ORDER BY COALESCE(started_at, created_at) DESC
        """,
        (cutoff_iso,),
    ).fetchall()
    session_ids = [row["id"] for row in sessions]
    token_rows = _rows_for_sessions(
        conn,
        session_ids,
        """
        SELECT
            s.id AS session_id,
            s.provider AS provider,
            COALESCE(s.project_path, 'unknown') AS project_path,
            COALESCE(t.model, s.model, 'unknown') AS model,
            COALESCE(t.task_type, s.task_type, 'unknown') AS task_type,
            s.status AS session_status,
            COALESCE(s.duration_ms, 0) AS duration_ms,
            COALESCE(s.retry_count, 0) AS retry_count,
            COALESCE(s.background_flag, 0) AS background_flag,
            s.privacy_flags AS privacy_flags,
            s.policy_flags AS policy_flags,
            COALESCE(SUM(t.input_tokens), 0) AS input_tokens,
            COALESCE(SUM(t.cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(t.output_tokens), 0) AS output_tokens,
            COALESCE(SUM(t.reasoning_output_tokens), 0) AS reasoning_output_tokens,
            COALESCE(SUM(t.cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
            COALESCE(SUM(t.cache_read_input_tokens), 0) AS cache_read_input_tokens,
            SUM(t.estimated_cost_usd) AS estimated_cost_usd,
            COALESCE(SUM(t.credit_estimate), 0) AS credit_estimate,
            COALESCE(SUM(CASE WHEN t.estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END), 0) AS priced_turns
        FROM sessions s
        LEFT JOIN turns t ON t.session_id = s.id
        WHERE s.id IN ({placeholders})
        GROUP BY s.id, s.provider, COALESCE(s.project_path, 'unknown'),
                 COALESCE(t.model, s.model, 'unknown'),
                 COALESCE(t.task_type, s.task_type, 'unknown')
        """,
    )
    tool_rows = _rows_for_sessions(
        conn,
        session_ids,
        """
        SELECT provider, COALESCE(tool_type, 'unknown') AS tool_type,
               COALESCE(tool_name, 'unknown') AS tool_name,
               COUNT(*) AS calls,
               COALESCE(SUM(output_bytes), 0) AS output_bytes
        FROM tool_events
        WHERE session_id IN ({placeholders})
        GROUP BY provider, COALESCE(tool_type, 'unknown'), COALESCE(tool_name, 'unknown')
        ORDER BY calls DESC, output_bytes DESC
        """,
    )
    issue_rows = _rows_for_sessions(
        conn,
        session_ids,
        """
        SELECT severity, code, title, detail, recommendation, evidence
        FROM issues
        WHERE session_id IN ({placeholders})
        ORDER BY CASE severity
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            ELSE 2
        END, code
        """,
    )
    scan_error_count = _scan_error_count(conn, cutoff_iso)

    total_turns = _count_for_sessions(conn, "turns", session_ids)
    total_tools = _count_for_sessions(conn, "tool_events", session_ids)
    total_tokens = sum(_total_tokens(row) for row in token_rows)
    total_output = sum(int(row["output_tokens"]) for row in token_rows)
    priced_turns = sum(int(row["priced_turns"]) for row in token_rows)
    estimated_cost = sum(float(row["estimated_cost_usd"] or 0) for row in token_rows)
    estimated_credits = sum(float(row["credit_estimate"] or 0) for row in token_rows)
    failed_sessions = sum(1 for row in sessions if row["status"] == "failed")
    retry_sessions = sum(1 for row in sessions if int(row["retry_count"]) > 0)
    background_sessions = sum(1 for row in sessions if int(row["background_flag"]) != 0)
    high_issues = [row for row in issue_rows if row["severity"] == "high"]
    _validate_session_count_consistency(session_ids, token_rows)
    cost_line = (
        f"- Estimated cost: ${estimated_cost:.6f} ({estimated_credits:.4f} credits, configured local prices)."
        if priced_turns
        else "- Estimated cost: unavailable unless local prices are configured."
    )

    lines = [
        "# AgentOps Guard Daily Summary",
        "",
        f"Generated: {utc_now_iso()}",
        f"Since: {since}",
        "",
        "## Overview",
        f"- Sessions: {len(session_ids)}",
        f"- Turns: {total_turns}",
        f"- Tool events: {total_tools}",
        f"- Total tokens: {total_tokens}",
        f"- Output tokens: {total_output}",
        f"- Failed sessions: {failed_sessions}",
        f"- Retry sessions: {retry_sessions}",
        f"- Background sessions: {background_sessions}",
        cost_line,
        f"- Scan source failures: {scan_error_count}",
        "",
        "## Project ranking",
        "",
    ]
    lines.extend(_project_ranking(token_rows))

    lines.extend(["", "## Provider/model breakdown", ""])
    lines.extend(_provider_breakdown(token_rows))

    lines.extend(["", "## Top expensive tasks", ""])
    lines.extend(_top_expensive_tasks(token_rows))

    lines.extend(["", "## Failure and retry table", ""])
    lines.extend(_failure_retry_table(token_rows))

    lines.extend(["", "## Background anomalies", ""])
    lines.extend(_background_anomalies(token_rows, issue_rows))

    lines.extend(["", "## Tool usage summary", ""])
    lines.extend(_tool_summary(tool_rows))

    lines.extend(["", "## Privacy and policy findings", ""])
    lines.extend(_privacy_policy_findings(token_rows, issue_rows))

    lines.extend(["", "## High-risk issues", ""])
    if high_issues:
        for row in high_issues:
            lines.append(f"- **{row['severity']}** `{row['code']}`: {row['title']}")
            if row["detail"]:
                lines.append(f"  Detail: {row['detail']}")
            if row["recommendation"]:
                lines.append(f"  Recommendation: {row['recommendation']}")
    elif issue_rows:
        lines.append("No high-risk issues detected.")
        lines.append(f"{len(issue_rows)} lower-severity issue(s) are available in SQLite.")
    else:
        lines.append("No high-risk issues detected.")

    lines.extend(["", "## Recommended fixes", ""])
    lines.extend(_recommended_fixes(issue_rows, scan_error_count))
    lines.append("")
    return "\n".join(lines)


def _rows_for_sessions(
    conn: sqlite3.Connection, session_ids: list[str], sql_template: str
) -> list[sqlite3.Row]:
    if not session_ids:
        return []
    placeholders = ",".join("?" for _ in session_ids)
    sql = sql_template.format(placeholders=placeholders)
    return list(conn.execute(sql, tuple(session_ids)).fetchall())


def _count_for_sessions(conn: sqlite3.Connection, table: str, session_ids: list[str]) -> int:
    if table not in {"turns", "tool_events"}:
        raise ValueError(f"Unsupported table: {table}")
    if not session_ids:
        return 0
    placeholders = ",".join("?" for _ in session_ids)
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE session_id IN ({placeholders})",
        tuple(session_ids),
    ).fetchone()
    return int(row["count"])


def _scan_error_count(conn: sqlite3.Connection, cutoff_iso: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT source_file) AS count
        FROM scan_errors
        WHERE created_at >= ?
        """,
        (cutoff_iso,),
    ).fetchone()
    return int(row["count"] or 0)


def _total_tokens(row: sqlite3.Row) -> int:
    return sum(
        int(row[name])
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


def _project_ranking(token_rows: list[sqlite3.Row]) -> list[str]:
    if not token_rows:
        return ["No project data recorded."]
    grouped: dict[str, dict[str, object]] = {}
    for row in token_rows:
        project = row["project_path"]
        bucket = grouped.setdefault(
            project,
            {"session_ids": set(), "failed_ids": set(), "tokens": 0, "retry_by_session": {}},
        )
        bucket["session_ids"].add(row["session_id"])
        if row["session_status"] == "failed":
            bucket["failed_ids"].add(row["session_id"])
        bucket["tokens"] += _total_tokens(row)
        bucket["retry_by_session"][row["session_id"]] = int(row["retry_count"])
    lines = [
        "| Project | Sessions | Total tokens | Failed | Retries |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for project, values in sorted(grouped.items(), key=lambda item: int(item[1]["tokens"]), reverse=True)[:10]:
        lines.append(
            f"| `{project}` | {len(values['session_ids'])} | {values['tokens']} | {len(values['failed_ids'])} | {sum(values['retry_by_session'].values())} |"
        )
    return lines


def _provider_breakdown(token_rows: list[sqlite3.Row]) -> list[str]:
    if not token_rows:
        return ["No provider/model data recorded."]
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in token_rows:
        key = (row["provider"], row["model"])
        bucket = grouped.setdefault(key, {"session_ids": set(), "tokens": 0, "output": 0})
        bucket["session_ids"].add(row["session_id"])
        bucket["tokens"] += _total_tokens(row)
        bucket["output"] += int(row["output_tokens"])
    lines = [
        "| Provider | Model | Sessions | Total tokens | Output tokens |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for (provider, model), values in sorted(grouped.items()):
        lines.append(
            f"| {provider} | {model} | {len(values['session_ids'])} | {values['tokens']} | {values['output']} |"
        )
    return lines


def _top_expensive_tasks(token_rows: list[sqlite3.Row]) -> list[str]:
    if not token_rows:
        return ["No sessions found for this time window."]
    lines = [
        "Each row is a session/model/task_type task rollup.",
        "| Task rollup | Session | Project | Provider | Model | Task type | Tokens | Estimated USD | Credits | Duration ms |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(token_rows, key=_total_tokens, reverse=True)[:10]:
        rollup = f"{row['session_id']} / {row['model']} / {row['task_type']}"
        estimated_cost = _money_or_unavailable(row["estimated_cost_usd"], int(row["priced_turns"]))
        credits = _number_or_unavailable(row["credit_estimate"], int(row["priced_turns"]))
        lines.append(
            f"| `{rollup}` | `{row['session_id']}` | `{row['project_path']}` | {row['provider']} | {row['model']} | {row['task_type']} | {_total_tokens(row)} | {estimated_cost} | {credits} | {row['duration_ms']} |"
        )
    return lines


def _failure_retry_table(token_rows: list[sqlite3.Row]) -> list[str]:
    rows = _session_rollups(
        row
        for row in token_rows
        if row["session_status"] == "failed" or int(row["retry_count"]) > 0
    )
    if not rows:
        return ["No failures or retries recorded."]
    lines = [
        "| Project | Session | Status | Retries | Tokens |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in rows[:20]:
        lines.append(
            f"| `{row['project_path']}` | `{row['session_id']}` | {row['session_status']} | {row['retry_count']} | {row['tokens']} |"
        )
    return lines


def _background_anomalies(
    token_rows: list[sqlite3.Row],
    issue_rows: list[sqlite3.Row],
) -> list[str]:
    rows = _session_rollups(row for row in token_rows if int(row["background_flag"]) != 0)
    issue_count = sum(1 for row in issue_rows if row["code"] == "BACKGROUND_CONSUMPTION")
    if not rows:
        return ["No background sessions recorded."]
    lines = [f"Background sessions: {len(rows)}; flagged consumption issues: {issue_count}."]
    for row in sorted(rows, key=lambda item: int(item["tokens"]), reverse=True)[:10]:
        lines.append(
            f"- `{row['session_id']}` ({row['provider']}/{row['model']}): {row['tokens']} tokens in `{row['project_path']}`"
        )
    return lines


def _tool_summary(tool_rows: list[sqlite3.Row]) -> list[str]:
    if not tool_rows:
        return ["No tool usage recorded."]
    lines = [
        "| Provider | Tool type | Tool name | Calls | Output bytes |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in tool_rows:
        lines.append(
            f"| {row['provider']} | {row['tool_type']} | {row['tool_name']} | {row['calls']} | {row['output_bytes']} |"
        )
    return lines


def _privacy_policy_findings(
    token_rows: list[sqlite3.Row],
    issue_rows: list[sqlite3.Row],
) -> list[str]:
    privacy_counts: dict[str, set[str]] = {}
    policy_counts: dict[str, set[str]] = {}
    for row in token_rows:
        for flag in split_flags(row["privacy_flags"]):
            privacy_counts.setdefault(flag, set()).add(row["session_id"])
        for flag in split_flags(row["policy_flags"]):
            policy_counts.setdefault(flag, set()).add(row["session_id"])
    issue_codes = {
        row["code"]
        for row in issue_rows
        if row["code"] in {"RAW_PAYLOAD_RISK", "POSSIBLE_SECRET", "RESTRICTED_SERVICE_CALL"}
    }
    if not privacy_counts and not policy_counts and not issue_codes:
        return ["No privacy or policy findings recorded."]
    lines: list[str] = []
    for flag, session_ids in sorted(privacy_counts.items()):
        lines.append(f"- Privacy `{flag}`: {len(session_ids)} session(s)")
    for flag, session_ids in sorted(policy_counts.items()):
        lines.append(f"- Policy `{flag}`: {len(session_ids)} session(s)")
    return lines


def _recommended_fixes(issue_rows: list[sqlite3.Row], scan_error_count: int) -> list[str]:
    lines: list[str] = []
    if scan_error_count:
        lines.append(
            f"- Review {scan_error_count} failed source file(s); bad logs are isolated in `scan_errors` with hashed messages."
        )
    if not issue_rows:
        lines.append("- Keep scanning daily and compare trend changes.")
        return lines
    seen: set[str] = set()
    for row in issue_rows:
        recommendation = row["recommendation"]
        if recommendation and recommendation not in seen:
            lines.append(f"- {recommendation}")
            seen.add(recommendation)
    lines.append("- Re-run `python -m aicg scan --since 24h` before tomorrow's report.")
    return lines


def _session_rollups(rows) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        bucket = grouped.setdefault(
            row["session_id"],
            {
                "session_id": row["session_id"],
                "project_path": row["project_path"],
                "provider": row["provider"],
                "model": row["model"],
                "session_status": row["session_status"],
                "retry_count": int(row["retry_count"]),
                "tokens": 0,
            },
        )
        bucket["tokens"] = int(bucket["tokens"]) + _total_tokens(row)
        bucket["retry_count"] = max(int(bucket["retry_count"]), int(row["retry_count"]))
    return sorted(grouped.values(), key=lambda item: int(item["tokens"]), reverse=True)


def _money_or_unavailable(value: object, priced_turns: int) -> str:
    if priced_turns <= 0:
        return "unavailable"
    return f"${float(value or 0):.6f}"


def _number_or_unavailable(value: object, priced_turns: int) -> str:
    if priced_turns <= 0:
        return "unavailable"
    return f"{float(value or 0):.4f}"


def _validate_session_count_consistency(
    session_ids: list[str],
    token_rows: list[sqlite3.Row],
) -> None:
    overview_session_count = len(set(session_ids))
    flagged_sessions = {
        row["session_id"]
        for row in token_rows
        if split_flags(row["privacy_flags"]) or split_flags(row["policy_flags"])
    }
    if len(flagged_sessions) > overview_session_count:
        raise RuntimeError("internal report inconsistency: flagged session count exceeds overview sessions")
