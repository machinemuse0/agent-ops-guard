from __future__ import annotations

import sqlite3
from typing import Any

from .sqlite_utils import chunked
from .tokens import total_tokens as provider_total_tokens
from .util import escape_markdown_text, isoformat_utc, markdown_code, parse_since, split_flags, utc_now_iso


DAILY_REPORT_SCHEMA_VERSION = 3


def generate_markdown_report(
    conn: sqlite3.Connection, since: str = "24h", config: dict | None = None
) -> str:
    return render_markdown_report(build_daily_report(conn, since=since, config=config))


def build_daily_report(
    conn: sqlite3.Connection,
    since: str = "24h",
    config: dict | None = None,
    *,
    period: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    compare: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
            COALESCE(SUM(t.input_uncached_tokens), 0) AS input_uncached_tokens,
            COALESCE(SUM(t.cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(t.output_tokens), 0) AS output_tokens,
            COALESCE(SUM(t.reasoning_output_tokens), 0) AS reasoning_output_tokens,
            COALESCE(SUM(t.cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
            COALESCE(SUM(t.cache_read_input_tokens), 0) AS cache_read_input_tokens,
            SUM(t.estimated_cost_usd) AS estimated_cost_usd,
            COALESCE(SUM(t.credit_estimate), 0) AS credit_estimate,
            COALESCE(MAX(t.cost_source), s.cost_source) AS cost_source,
            COUNT(t.id) AS turn_count,
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
        SELECT i.id, i.session_id, i.severity, i.code, i.title, i.detail,
               i.recommendation, i.evidence,
               COUNT(ie.id) AS evidence_count
        FROM issues i
        LEFT JOIN issue_evidence ie ON ie.issue_id = i.id
        WHERE i.session_id IN ({placeholders})
        GROUP BY i.id, i.session_id, i.severity, i.code, i.title,
                 i.detail, i.recommendation, i.evidence
        ORDER BY CASE i.severity
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            ELSE 2
        END, i.code
        """,
    )
    scan_error_count = _scan_error_count(conn, cutoff_iso)
    policy_lifecycle = _policy_lifecycle_model(conn, cutoff_iso)
    git_activity = _git_activity_model(conn, cutoff_iso)

    total_turns = _count_for_sessions(conn, "turns", session_ids)
    total_tools = _count_for_sessions(conn, "tool_events", session_ids)
    total_tokens = sum(_total_tokens(row) for row in token_rows)
    total_output = sum(int(row["output_tokens"]) for row in token_rows)
    priced_turns = sum(int(row["priced_turns"]) for row in token_rows)
    estimated_cost = sum(float(row["estimated_cost_usd"] or 0) for row in token_rows)
    estimated_credits = sum(float(row["credit_estimate"] or 0) for row in token_rows)
    failed_sessions = sum(1 for row in sessions if row["status"] == "failed")
    interrupted_sessions = sum(1 for row in sessions if row["status"] == "interrupted")
    completed_sessions = sum(1 for row in sessions if row["status"] == "completed")
    retry_sessions = sum(1 for row in sessions if int(row["retry_count"]) > 0)
    background_sessions = sum(1 for row in sessions if int(row["background_flag"]) != 0)
    privacy_policy = _privacy_policy_findings_model(token_rows, issue_rows)
    consistency = _consistency_checks(session_ids, token_rows, privacy_policy)

    return {
        "schemaVersion": DAILY_REPORT_SCHEMA_VERSION,
        "generatedAt": utc_now_iso(),
        "since": since,
        "overview": {
            "sessions": len(session_ids),
            "turns": total_turns,
            "toolEvents": total_tools,
            "totalTokens": total_tokens,
            "outputTokens": total_output,
            "failedSessions": failed_sessions,
            "interruptedSessions": interrupted_sessions,
            "retrySessions": retry_sessions,
            "backgroundSessions": background_sessions,
            "estimatedCostUsd": estimated_cost if priced_turns else None,
            "creditEstimate": estimated_credits if priced_turns else None,
            "pricedTurns": priced_turns,
            "pricingCoverage": {
                "pricedTurns": priced_turns,
                "totalTurns": total_turns,
                "complete": priced_turns == total_turns if total_turns else False,
            },
            "scanSourceFailures": scan_error_count,
            "costPerSuccessfulSession": estimated_cost / completed_sessions if priced_turns and completed_sessions else None,
        },
        "projectRanking": _project_ranking_model(token_rows),
        "providerModelBreakdown": _provider_breakdown_model(token_rows),
        "taskRollups": _top_expensive_tasks_model(token_rows),
        "failureRetry": _failure_retry_model(token_rows),
        "backgroundAnomalies": _background_anomalies_model(token_rows, issue_rows),
        "toolUsage": _tool_summary_model(tool_rows),
        "privacyPolicyFindings": privacy_policy,
        "policyLifecycle": policy_lifecycle,
        "wasteBreakdown": _waste_breakdown_model(token_rows, issue_rows),
        "efficiency": _efficiency_model(token_rows),
        "gitActivity": git_activity,
        "highRiskIssues": [_issue_model(row) for row in issue_rows if row["severity"] == "high"],
        "lowerRiskIssueCount": sum(1 for row in issue_rows if row["severity"] != "high"),
        "recommendedFixes": _recommended_fixes_model(issue_rows, scan_error_count),
        "consistencyChecks": consistency,
        "period": {
            "type": period,
            "start": period_start,
            "end": period_end,
        },
        "compare": compare,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    overview = report["overview"]
    cost_line = _cost_line(overview)
    lines = [
        "# AgentOps Guard Daily Summary",
        "",
        f"Generated: {report['generatedAt']}",
        f"Since: {report['since']}",
        "",
        "## Overview",
        f"- Sessions: {overview['sessions']}",
        f"- Turns: {overview['turns']}",
        f"- Tool events: {overview['toolEvents']}",
        f"- Total tokens: {overview['totalTokens']}",
        f"- Output tokens: {overview['outputTokens']}",
        f"- Failed sessions: {overview['failedSessions']}",
        f"- Interrupted sessions: {overview.get('interruptedSessions', 0)}",
        f"- Retry sessions: {overview['retrySessions']}",
        f"- Background sessions: {overview['backgroundSessions']}",
        cost_line,
        f"- Scan source failures: {overview['scanSourceFailures']}",
        "",
        "## Project ranking",
        "",
    ]
    lines.extend(_project_ranking_markdown(report["projectRanking"]))
    lines.extend(["", "## Provider/model breakdown", ""])
    lines.extend(_provider_breakdown_markdown(report["providerModelBreakdown"]))
    lines.extend(["", "## Top expensive tasks", ""])
    lines.extend(_top_expensive_tasks_markdown(report["taskRollups"]))
    lines.extend(["", "## Failure and retry table", ""])
    lines.extend(_failure_retry_markdown(report["failureRetry"]))
    lines.extend(["", "## Background anomalies", ""])
    lines.extend(_background_anomalies_markdown(report["backgroundAnomalies"]))
    lines.extend(["", "## Tool usage summary", ""])
    lines.extend(_tool_summary_markdown(report["toolUsage"]))
    lines.extend(["", "## Privacy and policy findings", ""])
    lines.extend(_privacy_policy_findings_markdown(report["privacyPolicyFindings"]))
    lines.extend(["", "## Policy lifecycle", ""])
    lines.extend(_policy_lifecycle_markdown(report.get("policyLifecycle", {})))
    lines.extend(["", "## Waste breakdown", ""])
    lines.extend(_waste_breakdown_markdown(report.get("wasteBreakdown", {})))
    lines.extend(["", "## Efficiency", ""])
    lines.extend(_efficiency_markdown(report.get("efficiency", {})))
    lines.extend(["", "## Git activity", ""])
    lines.extend(_git_activity_markdown(report.get("gitActivity", [])))
    if report.get("compare"):
        lines.extend(["", "## Compare", ""])
        lines.extend(_compare_markdown(report["compare"]))
    lines.extend(["", "## High-risk issues", ""])
    high_issues = report["highRiskIssues"]
    if high_issues:
        for issue in high_issues:
            lines.append(f"- **{issue['severity']}** `{issue['code']}`: {issue['title']}")
            if issue["detail"]:
                lines.append(f"  Detail: {issue['detail']}")
            if issue["recommendation"]:
                lines.append(f"  Recommendation: {issue['recommendation']}")
            if issue["sessionId"]:
                lines.append(f"  Review: run `aicg review --session {issue['sessionId']}` for diagnosis.")
    elif report.get("lowerRiskIssueCount", 0):
        lines.append("No high-risk issues detected.")
        lines.append(f"{report['lowerRiskIssueCount']} lower-severity issue(s) are available in SQLite.")
    else:
        lines.append("No high-risk issues detected.")
    lines.extend(["", "## Recommended fixes", ""])
    lines.extend(f"- {item}" for item in report["recommendedFixes"])
    lines.append("")
    return "\n".join(lines)


def _rows_for_sessions(
    conn: sqlite3.Connection, session_ids: list[str], sql_template: str
) -> list[sqlite3.Row]:
    if not session_ids:
        return []
    rows: list[sqlite3.Row] = []
    for batch in chunked(session_ids):
        placeholders = ",".join("?" for _ in batch)
        sql = sql_template.format(placeholders=placeholders)
        rows.extend(conn.execute(sql, tuple(batch)).fetchall())
    return rows


def _count_for_sessions(conn: sqlite3.Connection, table: str, session_ids: list[str]) -> int:
    if table not in {"turns", "tool_events"}:
        raise ValueError(f"Unsupported table: {table}")
    if not session_ids:
        return 0
    total = 0
    for batch in chunked(session_ids):
        placeholders = ",".join("?" for _ in batch)
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE session_id IN ({placeholders})",
            tuple(batch),
        ).fetchone()
        total += int(row["count"])
    return total


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


def _total_tokens(row: sqlite3.Row | dict[str, Any]) -> int:
    return provider_total_tokens(row)


def _project_ranking_model(token_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in token_rows:
        project = row["project_path"]
        bucket = grouped.setdefault(
            project,
            {"project": project, "sessionIds": set(), "failedIds": set(), "totalTokens": 0, "retriesBySession": {}},
        )
        bucket["sessionIds"].add(row["session_id"])
        if row["session_status"] == "failed":
            bucket["failedIds"].add(row["session_id"])
        bucket["totalTokens"] += _total_tokens(row)
        bucket["retriesBySession"][row["session_id"]] = int(row["retry_count"])
    result = []
    for values in sorted(grouped.values(), key=lambda item: int(item["totalTokens"]), reverse=True)[:10]:
        result.append(
            {
                "project": values["project"],
                "sessions": len(values["sessionIds"]),
                "totalTokens": values["totalTokens"],
                "failed": len(values["failedIds"]),
                "retries": sum(values["retriesBySession"].values()),
            }
        )
    return result


def _provider_breakdown_model(token_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in token_rows:
        key = (row["provider"], row["model"])
        bucket = grouped.setdefault(
            key,
            {"provider": row["provider"], "model": row["model"], "sessionIds": set(), "totalTokens": 0, "outputTokens": 0},
        )
        bucket["sessionIds"].add(row["session_id"])
        bucket["totalTokens"] += _total_tokens(row)
        bucket["outputTokens"] += int(row["output_tokens"])
    return [
        {
            "provider": values["provider"],
            "model": values["model"],
            "sessions": len(values["sessionIds"]),
            "totalTokens": values["totalTokens"],
            "outputTokens": values["outputTokens"],
        }
        for _, values in sorted(grouped.items())
    ]


def _top_expensive_tasks_model(token_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in sorted(token_rows, key=_total_tokens, reverse=True)[:10]:
        priced_turns = int(row["priced_turns"])
        turn_count = int(row["turn_count"] or 0)
        result.append(
            {
                "taskRollup": f"{row['session_id']} / {row['model']} / {row['task_type']}",
                "sessionId": row["session_id"],
                "projectPath": row["project_path"],
                "provider": row["provider"],
                "model": row["model"],
                "taskType": row["task_type"],
                "totalTokens": _total_tokens(row),
                "estimatedCostUsd": float(row["estimated_cost_usd"] or 0) if priced_turns else None,
                "creditEstimate": float(row["credit_estimate"] or 0) if priced_turns else None,
                "turns": turn_count,
                "pricedTurns": priced_turns,
                "durationMs": int(row["duration_ms"]),
            }
        )
    return result


def _failure_retry_model(token_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return _session_rollups(
        row
        for row in token_rows
        if row["session_status"] == "failed" or int(row["retry_count"]) > 0
    )[:20]


def _background_anomalies_model(
    token_rows: list[sqlite3.Row],
    issue_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    sessions = _session_rollups(row for row in token_rows if int(row["background_flag"]) != 0)
    issue_count = sum(1 for row in issue_rows if row["code"] == "BACKGROUND_CONSUMPTION")
    return {"sessions": sessions[:10], "flaggedIssueCount": issue_count}


def _tool_summary_model(tool_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [
        {
            "provider": row["provider"],
            "toolType": row["tool_type"],
            "toolName": row["tool_name"],
            "calls": int(row["calls"]),
            "outputBytes": int(row["output_bytes"]),
        }
        for row in tool_rows
    ]


def _privacy_policy_findings_model(
    token_rows: list[sqlite3.Row],
    issue_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    privacy_counts: dict[str, set[str]] = {}
    policy_counts: dict[str, set[str]] = {}
    for row in token_rows:
        for flag in split_flags(row["privacy_flags"]):
            privacy_counts.setdefault(flag, set()).add(row["session_id"])
        for flag in split_flags(row["policy_flags"]):
            policy_counts.setdefault(flag, set()).add(row["session_id"])
    issue_codes = sorted(
        {
            row["code"]
            for row in issue_rows
            if row["code"] in {"RAW_PAYLOAD_RISK", "POSSIBLE_SECRET", "RESTRICTED_SERVICE_CALL"}
        }
    )
    return {
        "privacy": [
            {"flag": flag, "sessions": len(session_ids)}
            for flag, session_ids in sorted(privacy_counts.items())
        ],
        "policy": [
            {"flag": flag, "sessions": len(session_ids)}
            for flag, session_ids in sorted(policy_counts.items())
        ],
        "issueCodes": issue_codes,
    }


def _policy_lifecycle_model(conn: sqlite3.Connection, cutoff_iso: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN pf.first_seen_at >= ? THEN 1 ELSE 0 END), 0) AS new_count,
            COALESCE(SUM(CASE WHEN pa.finding_id IS NULL THEN 1 ELSE 0 END), 0) AS open_count,
            COALESCE(SUM(CASE WHEN pa.finding_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS acked_count
        FROM policy_findings pf
        LEFT JOIN policy_acks pa ON pa.finding_id = pf.id
        """,
        (cutoff_iso,),
    ).fetchone()
    by_level = conn.execute(
        """
        SELECT level, COUNT(*) AS count
        FROM policy_findings pf
        LEFT JOIN policy_acks pa ON pa.finding_id = pf.id
        WHERE pa.finding_id IS NULL
        GROUP BY level
        ORDER BY level
        """
    ).fetchall()
    return {
        "new": int(row["new_count"] or 0),
        "open": int(row["open_count"] or 0),
        "acked": int(row["acked_count"] or 0),
        "openByLevel": {item["level"]: int(item["count"]) for item in by_level},
    }


def _waste_breakdown_model(token_rows: list[sqlite3.Row], issue_rows: list[sqlite3.Row]) -> dict[str, Any]:
    total_cost = sum(float(row["estimated_cost_usd"] or 0) for row in token_rows)
    failed_ids = {row["session_id"] for row in token_rows if row["session_status"] == "failed"}
    interrupted_ids = {row["session_id"] for row in token_rows if row["session_status"] == "interrupted"}
    retry_ids = {
        row["session_id"]
        for row in token_rows
        if row["session_status"] not in {"failed", "interrupted"} and int(row["retry_count"] or 0) > 0
    }
    failed_cost = sum(
        float(row["estimated_cost_usd"] or 0)
        for row in token_rows
        if row["session_id"] in failed_ids
    )
    interrupted_cost = sum(
        float(row["estimated_cost_usd"] or 0)
        for row in token_rows
        if row["session_id"] in interrupted_ids
    )
    retry_cost = sum(
        float(row["estimated_cost_usd"] or 0)
        for row in token_rows
        if row["session_id"] in retry_ids
    )
    bloat_sessions = {
        row["session_id"]
        for row in issue_rows
        if row["code"] == "TOOL_OUTPUT_BLOAT"
    } - failed_ids - interrupted_ids - retry_ids
    bloat_cost = sum(
        float(row["estimated_cost_usd"] or 0)
        for row in token_rows
        if row["session_id"] in bloat_sessions
    )
    wasted_cost = failed_cost + interrupted_cost + retry_cost + bloat_cost
    return {
        "totalCostUsd": total_cost or None,
        "wastedCostUsd": wasted_cost or None,
        "wasteRate": wasted_cost / total_cost if total_cost else None,
        "failedCostUsd": failed_cost or None,
        "retryCostUsd": retry_cost or None,
        "interruptedCostUsd": interrupted_cost or None,
        "bloatCostEstimateUsd": bloat_cost or None,
        "invariantWasteLteTotal": wasted_cost <= total_cost + 1e-12,
    }


def _efficiency_model(token_rows: list[sqlite3.Row]) -> dict[str, Any]:
    input_uncached = sum(int(row["input_uncached_tokens"] or 0) for row in token_rows)
    cache_read = sum(int(row["cache_read_input_tokens"] or 0) for row in token_rows)
    ratio = cache_read / max(input_uncached + cache_read, 1)
    task_tokens: dict[str, list[int]] = {}
    for row in token_rows:
        task_tokens.setdefault(row["task_type"], []).append(_total_tokens(row))
    split_signals = []
    for task_type, values in task_tokens.items():
        sorted_values = sorted(values)
        p50 = _percentile(sorted_values, 0.50)
        p95 = _percentile(sorted_values, 0.95)
        split_signals.append(
            {
                "taskType": task_type,
                "p50Tokens": p50,
                "p95Tokens": p95,
                "p95P50Ratio": p95 / max(p50, 1),
            }
        )
    return {
        "cacheEfficiency": ratio,
        "taskSplitSignals": sorted(split_signals, key=lambda item: item["p95Tokens"], reverse=True)[:10],
    }


def _git_activity_model(conn: sqlite3.Connection, cutoff_iso: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT project_path, period_start, commits, merge_commits, insertions, deletions
        FROM git_activity
        WHERE period_start >= ?
        ORDER BY period_start DESC, project_path
        """,
        (cutoff_iso,),
    ).fetchall()
    return [
        {
            "projectPath": row["project_path"],
            "periodStart": row["period_start"],
            "commits": int(row["commits"] or 0),
            "mergeCommitCount": int(row["merge_commits"] or 0),
            "insertions": int(row["insertions"] or 0),
            "deletions": int(row["deletions"] or 0),
        }
        for row in rows
    ]


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * percentile)))
    return int(values[index])


def _issue_model(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "severity": row["severity"],
        "code": row["code"],
        "title": row["title"],
        "detail": row["detail"],
        "recommendation": row["recommendation"],
        "evidence": row["evidence"],
        "evidenceCount": int(row["evidence_count"] or 0),
    }


def _recommended_fixes_model(issue_rows: list[sqlite3.Row], scan_error_count: int) -> list[str]:
    lines: list[str] = []
    if scan_error_count:
        lines.append(
            f"Review {scan_error_count} failed source file(s); bad logs are isolated in `scan_errors` with hashed messages."
        )
    if not issue_rows:
        lines.append("Keep scanning daily and compare trend changes.")
        return lines
    seen: set[str] = set()
    for row in issue_rows:
        recommendation = row["recommendation"]
        if recommendation and recommendation not in seen:
            lines.append(str(recommendation))
            seen.add(str(recommendation))
    lines.append("Re-run `python -m aicg scan --since 24h` before tomorrow's report.")
    return lines


def _consistency_checks(
    session_ids: list[str],
    token_rows: list[sqlite3.Row],
    privacy_policy: dict[str, Any],
) -> dict[str, Any]:
    overview_session_count = len(set(session_ids))
    flagged_sessions = {
        row["session_id"]
        for row in token_rows
        if split_flags(row["privacy_flags"]) or split_flags(row["policy_flags"])
    }
    checks = {
        "overviewSessionsGteFlaggedSessions": overview_session_count >= len(flagged_sessions),
        "overviewSessionCount": overview_session_count,
        "flaggedSessionCount": len(flagged_sessions),
        "privacyFlagSessionMax": max((item["sessions"] for item in privacy_policy["privacy"]), default=0),
        "policyFlagSessionMax": max((item["sessions"] for item in privacy_policy["policy"]), default=0),
    }
    checks["status"] = "pass" if all(
        checks[key] is True
        for key in ("overviewSessionsGteFlaggedSessions",)
    ) else "fail"
    return checks


def _session_rollups(rows) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = grouped.setdefault(
            row["session_id"],
            {
                "sessionId": row["session_id"],
                "projectPath": row["project_path"],
                "provider": row["provider"],
                "model": row["model"],
                "status": row["session_status"],
                "retries": int(row["retry_count"]),
                "tokens": 0,
            },
        )
        bucket["tokens"] = int(bucket["tokens"]) + _total_tokens(row)
        bucket["retries"] = max(int(bucket["retries"]), int(row["retry_count"]))
    return sorted(grouped.values(), key=lambda item: int(item["tokens"]), reverse=True)


def _project_ranking_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No project data recorded."]
    lines = [
        "| Project | Sessions | Total tokens | Failed | Retries |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_md_code(row['project'])} | {row['sessions']} | {row['totalTokens']} | {row['failed']} | {row['retries']} |"
        )
    return lines


def _provider_breakdown_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No provider/model data recorded."]
    lines = [
        "| Provider | Model | Sessions | Total tokens | Output tokens |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_md_text(row['provider'])} | {_md_text(row['model'])} | {row['sessions']} | {row['totalTokens']} | {row['outputTokens']} |"
        )
    return lines


def _top_expensive_tasks_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No sessions found for this time window."]
    lines = [
        "Each row is a session/model/task_type task rollup.",
        "| Task rollup | Session | Project | Provider | Model | Task type | Tokens | Estimated USD | Credits | Duration ms |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        estimated_cost = _cost_or_partial(row)
        credits = _number_or_unavailable(row["creditEstimate"])
        lines.append(
            f"| {_md_code(row['taskRollup'])} | {_md_code(row['sessionId'])} | {_md_code(row['projectPath'])} | {_md_text(row['provider'])} | {_md_text(row['model'])} | {_md_text(row['taskType'])} | {row['totalTokens']} | {estimated_cost} | {credits} | {row['durationMs']} |"
        )
    return lines


def _failure_retry_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No failures or retries recorded."]
    lines = [
        "| Project | Session | Status | Retries | Tokens |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_md_code(row['projectPath'])} | {_md_code(row['sessionId'])} | {_md_text(row['status'])} | {row['retries']} | {row['tokens']} |"
        )
    return lines


def _background_anomalies_markdown(model: dict[str, Any]) -> list[str]:
    rows = model["sessions"]
    if not rows:
        return ["No background sessions recorded."]
    lines = [
        f"Background sessions: {len(rows)}; flagged consumption issues: {model['flaggedIssueCount']}."
    ]
    for row in rows:
        lines.append(
            f"- {_md_code(row['sessionId'])} ({_md_text(row['provider'])}/{_md_text(row['model'])}): {row['tokens']} tokens in {_md_code(row['projectPath'])}"
        )
    return lines


def _tool_summary_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No tool usage recorded."]
    lines = [
        "| Provider | Tool type | Tool name | Calls | Output bytes |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_md_text(row['provider'])} | {_md_text(row['toolType'])} | {_md_text(row['toolName'])} | {row['calls']} | {row['outputBytes']} |"
        )
    return lines


def _privacy_policy_findings_markdown(model: dict[str, Any]) -> list[str]:
    if not model["privacy"] and not model["policy"] and not model["issueCodes"]:
        return ["No privacy or policy findings recorded."]
    lines: list[str] = []
    for item in model["privacy"]:
        lines.append(f"- Privacy `{item['flag']}`: {item['sessions']} session(s)")
    for item in model["policy"]:
        lines.append(f"- Policy `{item['flag']}`: {item['sessions']} session(s)")
    return lines


def _policy_lifecycle_markdown(model: dict[str, Any]) -> list[str]:
    if not model:
        return ["No policy lifecycle data recorded."]
    lines = [
        f"- New: {model.get('new', 0)}",
        f"- Open: {model.get('open', 0)}",
        f"- Acknowledged: {model.get('acked', 0)}",
    ]
    by_level = model.get("openByLevel") or {}
    if by_level:
        lines.append("- Open by level: " + ", ".join(f"{key}={value}" for key, value in sorted(by_level.items())))
    return lines


def _waste_breakdown_markdown(model: dict[str, Any]) -> list[str]:
    if not model or model.get("totalCostUsd") is None:
        return ["Cost waste metrics unavailable until local prices or native costs are available."]
    return [
        f"- Total cost: {_money_or_unavailable(model.get('totalCostUsd'))}",
        f"- Wasted cost: {_money_or_unavailable(model.get('wastedCostUsd'))}",
        f"- Waste rate: {_percent_or_unavailable(model.get('wasteRate'))}",
        f"- Failed cost: {_money_or_unavailable(model.get('failedCostUsd'))}",
        f"- Retry cost: {_money_or_unavailable(model.get('retryCostUsd'))}",
        f"- Interrupted cost: {_money_or_unavailable(model.get('interruptedCostUsd'))}",
        f"- Bloat cost estimate: {_money_or_unavailable(model.get('bloatCostEstimateUsd'))}",
    ]


def _efficiency_markdown(model: dict[str, Any]) -> list[str]:
    if not model:
        return ["No efficiency data recorded."]
    lines = [f"- Cache efficiency: {_percent_or_unavailable(model.get('cacheEfficiency'))}"]
    signals = model.get("taskSplitSignals") or []
    if signals:
        lines.extend(["", "| Task type | P50 tokens | P95 tokens | P95/P50 |", "| --- | ---: | ---: | ---: |"])
        for row in signals:
            lines.append(
                f"| {_md_text(row['taskType'])} | {row['p50Tokens']} | {row['p95Tokens']} | {row['p95P50Ratio']:.2f} |"
            )
    return lines


def _git_activity_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No linked local git activity recorded."]
    lines = [
        "| Project | Period start | Commits | Merge commits | Insertions | Deletions |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_md_code(row['projectPath'])} | {_md_text(row['periodStart'])} | {row['commits']} | {row['mergeCommitCount']} | {row['insertions']} | {row['deletions']} |"
        )
    return lines


def _compare_markdown(model: dict[str, Any]) -> list[str]:
    if not model:
        return ["No previous period snapshot available."]
    return [
        f"- Previous period: {_md_text(model.get('previousPeriodStart'))}",
        f"- Sessions delta: {model.get('sessionsDelta', 'unavailable')}",
        f"- Total tokens delta: {model.get('totalTokensDelta', 'unavailable')}",
        f"- Estimated cost delta: {_money_or_unavailable(model.get('estimatedCostUsdDelta'))}",
    ]


def _money_or_unavailable(value: object) -> str:
    if value is None:
        return "unavailable"
    return f"${float(value):.6f}"


def _cost_line(overview: dict[str, Any]) -> str:
    if overview["estimatedCostUsd"] is None:
        return "- Estimated cost: unavailable unless local prices are configured."
    priced_turns = int(overview.get("pricedTurns") or 0)
    total_turns = int(overview.get("turns") or 0)
    cost = f"${overview['estimatedCostUsd']:.6f}"
    credits = f"{overview['creditEstimate']:.4f}"
    if total_turns and priced_turns < total_turns:
        return (
            f"- Estimated cost: at least {cost} ({credits} credits, {priced_turns}/{total_turns} turns priced; "
            "unpriced turns excluded)."
        )
    return f"- Estimated cost: {cost} ({credits} credits, configured local prices; {priced_turns}/{total_turns} turns priced)."


def _cost_or_partial(row: dict[str, Any]) -> str:
    value = row["estimatedCostUsd"]
    if value is None:
        return "unavailable"
    priced_turns = int(row.get("pricedTurns") or 0)
    turns = int(row.get("turns") or 0)
    rendered = _money_or_unavailable(value)
    if turns and priced_turns < turns:
        return f"{rendered} partial ({priced_turns}/{turns})"
    return rendered


def _number_or_unavailable(value: object) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value):.4f}"


def _percent_or_unavailable(value: object) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value) * 100:.2f}%"


def _md_text(value: object) -> str:
    return escape_markdown_text(value)


def _md_code(value: object) -> str:
    return markdown_code(value)
