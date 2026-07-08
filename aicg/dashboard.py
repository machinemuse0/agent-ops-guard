from __future__ import annotations

import json
import sqlite3
from typing import Any

from .util import escape_html_text, sha256_text, utc_now_iso


DASHBOARD_SCHEMA_VERSION = 1


def build_dashboard_model(
    conn: sqlite3.Connection,
    *,
    period: str = "day",
    limit: int = 90,
    current_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshots = _snapshot_models(conn, period=period, limit=limit)
    if current_report is not None:
        current_snapshot = _snapshot_from_report(current_report, created_at=current_report.get("generatedAt"))
        snapshots = [snapshot for snapshot in snapshots if snapshot["periodStart"] != current_snapshot["periodStart"]]
        snapshots.append(current_snapshot)
        snapshots = sorted(snapshots, key=lambda item: str(item["periodStart"]))[-limit:]

    return {
        "schemaVersion": DASHBOARD_SCHEMA_VERSION,
        "generatedAt": utc_now_iso(),
        "period": period,
        "limit": limit,
        "currentReport": current_report,
        "snapshots": snapshots,
        "trends": _trend_model(snapshots),
        "reviewRecurringTop": _review_recurring_top(conn),
        "alertHistory": _alert_history(conn),
    }


def render_dashboard_json(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True)


def render_dashboard_html(model: dict[str, Any]) -> str:
    report = model.get("currentReport") if isinstance(model.get("currentReport"), dict) else None
    body = [
        _page_header("AgentOps Guard Dashboard", model.get("generatedAt"), model.get("period")),
        _overview_section(report),
        _trend_section(model.get("trends") if isinstance(model.get("trends"), dict) else {}),
        _project_section(report),
        _provider_section(report),
        _policy_section(report),
        _review_section(model.get("reviewRecurringTop") or []),
        _alert_section(model.get("alertHistory") or []),
        "</main>",
    ]
    return _html_document("AgentOps Guard Dashboard", "\n".join(body))


def render_summary_html(report: dict[str, Any]) -> str:
    body = [
        _page_header("AgentOps Guard Summary", report.get("generatedAt"), report.get("period", {}).get("type")),
        _overview_section(report),
        _project_section(report),
        _provider_section(report),
        _task_section(report),
        _policy_section(report),
        _issue_section(report),
        "</main>",
    ]
    return _html_document("AgentOps Guard Summary", "\n".join(body))


def _snapshot_models(conn: sqlite3.Connection, *, period: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT period_type, period_start, report_json, report_hash, created_at
        FROM report_snapshots
        WHERE period_type = ?
        ORDER BY period_start DESC
        LIMIT ?
        """,
        (period, limit),
    ).fetchall()
    snapshots = []
    for row in reversed(rows):
        try:
            report = json.loads(row["report_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        snapshot = _snapshot_from_report(report, created_at=row["created_at"])
        snapshot["reportHash"] = row["report_hash"]
        snapshots.append(snapshot)
    return snapshots


def _snapshot_from_report(report: dict[str, Any], *, created_at: str | None) -> dict[str, Any]:
    period = report.get("period") if isinstance(report.get("period"), dict) else {}
    period_start = period.get("start") or report.get("since") or report.get("generatedAt") or "unknown"
    overview = report.get("overview") if isinstance(report.get("overview"), dict) else {}
    waste = report.get("wasteBreakdown") if isinstance(report.get("wasteBreakdown"), dict) else {}
    report_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
    return {
        "periodStart": period_start,
        "createdAt": created_at,
        "reportHash": sha256_text(report_json),
        "overview": {
            "sessions": int(overview.get("sessions") or 0),
            "turns": int(overview.get("turns") or 0),
            "totalTokens": int(overview.get("totalTokens") or 0),
            "estimatedCostUsd": _number_or_none(overview.get("estimatedCostUsd")),
            "failedSessions": int(overview.get("failedSessions") or 0),
            "interruptedSessions": int(overview.get("interruptedSessions") or 0),
            "wasteRate": _number_or_none(waste.get("wasteRate")),
        },
    }


def _trend_model(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    def series(key: str) -> list[dict[str, Any]]:
        return [
            {
                "label": snapshot["periodStart"],
                "value": snapshot.get("overview", {}).get(key),
            }
            for snapshot in snapshots
        ]

    return {
        "sessions": series("sessions"),
        "totalTokens": series("totalTokens"),
        "estimatedCostUsd": series("estimatedCostUsd"),
        "wasteRate": series("wasteRate"),
    }


def _review_recurring_top(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT code,
               COUNT(*) AS findings,
               COUNT(DISTINCT session_id) AS sessions,
               MAX(CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS confidence_rank
        FROM review_findings
        GROUP BY code
        ORDER BY findings DESC, code
        LIMIT 10
        """
    ).fetchall()
    rank_to_confidence = {3: "high", 2: "medium", 1: "low"}
    return [
        {
            "code": row["code"],
            "findings": int(row["findings"]),
            "sessions": int(row["sessions"]),
            "confidence": rank_to_confidence.get(int(row["confidence_rank"] or 1), "low"),
        }
        for row in rows
    ]


def _alert_history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT period_type, period_start, alert_key, threshold_value, actual_value, created_at
        FROM alert_events
        ORDER BY created_at DESC
        LIMIT 20
        """
    ).fetchall()
    return [
        {
            "periodType": row["period_type"],
            "periodStart": row["period_start"],
            "key": row["alert_key"],
            "threshold": float(row["threshold_value"]),
            "actual": _number_or_none(row["actual_value"]),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def _html_document(title: str, body: str) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
            f"<title>{_h(title)}</title>",
            "<style>",
            _stylesheet(),
            "</style>",
            "</head>",
            "<body>",
            body,
            "</body>",
            "</html>",
            "",
        ]
    )


def _stylesheet() -> str:
    return """
:root { color-scheme: light; --ink: #1d252c; --muted: #61717d; --line: #d9e2e8; --panel: #f8fafb; --accent: #176b87; --warn: #9b3412; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: #ffffff; }
main { max-width: 1180px; margin: 0 auto; padding: 28px 20px 40px; }
header { border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 24px; }
h1 { font-size: 28px; line-height: 1.15; margin: 0 0 8px; letter-spacing: 0; }
h2 { font-size: 18px; margin: 28px 0 12px; letter-spacing: 0; }
p { margin: 6px 0; }
.muted { color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: var(--panel); min-height: 78px; }
.label { color: var(--muted); font-size: 12px; }
.value { font-size: 22px; line-height: 1.25; margin-top: 6px; overflow-wrap: anywhere; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }
.chart { border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
svg { max-width: 100%; height: auto; display: block; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th, td { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { font-size: 12px; color: var(--muted); font-weight: 600; }
code { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; font-size: 0.92em; }
.warn { color: var(--warn); font-weight: 600; }
""".strip()


def _page_header(title: str, generated_at: object, period: object) -> str:
    return (
        "<main>"
        "<header>"
        f"<h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Generated: {_h(generated_at or 'unknown')} | Period: {_h(period or 'ad hoc')}</p>"
        "</header>"
    )


def _overview_section(report: dict[str, Any] | None) -> str:
    overview = report.get("overview") if isinstance(report, dict) and isinstance(report.get("overview"), dict) else {}
    waste = report.get("wasteBreakdown") if isinstance(report, dict) and isinstance(report.get("wasteBreakdown"), dict) else {}
    cards = [
        ("Sessions", overview.get("sessions", 0)),
        ("Total tokens", _integer(overview.get("totalTokens"))),
        ("Estimated cost", _money(overview.get("estimatedCostUsd"))),
        ("Waste rate", _percent(waste.get("wasteRate"))),
        ("Failed", overview.get("failedSessions", 0)),
        ("Interrupted", overview.get("interruptedSessions", 0)),
    ]
    html_cards = "\n".join(
        f"<div class=\"card\"><div class=\"label\">{_h(label)}</div><div class=\"value\">{_h(value)}</div></div>"
        for label, value in cards
    )
    return f"<section><h2>Overview</h2><div class=\"grid\">{html_cards}</div></section>"


def _trend_section(trends: dict[str, Any]) -> str:
    return (
        "<section><h2>Trends</h2><div class=\"charts\">"
        + _chart_panel("Total tokens", trends.get("totalTokens") or [], value_kind="integer")
        + _chart_panel("Estimated cost", trends.get("estimatedCostUsd") or [], value_kind="money")
        + _chart_panel("Waste rate", trends.get("wasteRate") or [], value_kind="percent")
        + "</div>"
        + _trend_table(trends.get("totalTokens") or [], trends.get("estimatedCostUsd") or [], trends.get("wasteRate") or [])
        + "</section>"
    )


def _chart_panel(title: str, series: list[dict[str, Any]], *, value_kind: str) -> str:
    return f"<div class=\"chart\"><h2>{_h(title)}</h2>{_line_svg(series, title, value_kind=value_kind)}</div>"


def _line_svg(series: list[dict[str, Any]], title: str, *, value_kind: str) -> str:
    points = [(str(item.get("label") or ""), _number_or_none(item.get("value"))) for item in series]
    numeric = [(label, value) for label, value in points if value is not None]
    if not numeric:
        return '<p class="muted">No trend data available.</p>'
    width = 520
    height = 180
    left = 36
    right = 14
    top = 18
    bottom = 34
    values = [float(value) for _, value in numeric]
    min_value = min(values)
    max_value = max(values)
    span = max(max_value - min_value, 1.0)
    usable_w = width - left - right
    usable_h = height - top - bottom
    coords = []
    count = max(len(numeric) - 1, 1)
    for index, (_, value) in enumerate(numeric):
        x = left + (usable_w * index / count)
        y = top + usable_h - ((float(value) - min_value) / span * usable_h)
        coords.append((x, y, value))
    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y, _ in coords)
    first_label = numeric[0][0]
    last_label = numeric[-1][0]
    latest_value = _format_value(numeric[-1][1], value_kind)
    return "\n".join(
        [
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_h(title)} trend">',
            f"<title>{_h(title)} trend</title>",
            f'<line x1="{left}" y1="{top + usable_h}" x2="{width - right}" y2="{top + usable_h}" stroke="#d9e2e8" />',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + usable_h}" stroke="#d9e2e8" />',
            f'<polyline points="{polyline}" fill="none" stroke="#176b87" stroke-width="3" />',
            f'<text x="{left}" y="{height - 12}" font-size="11" fill="#61717d">{_h(_short_label(first_label))}</text>',
            f'<text x="{width - right}" y="{height - 12}" font-size="11" text-anchor="end" fill="#61717d">{_h(_short_label(last_label))}</text>',
            f'<text x="{width - right}" y="16" font-size="12" text-anchor="end" fill="#1d252c">{_h(latest_value)}</text>',
            "</svg>",
        ]
    )


def _trend_table(tokens: list[dict[str, Any]], costs: list[dict[str, Any]], waste: list[dict[str, Any]]) -> str:
    by_label: dict[str, dict[str, Any]] = {}
    for key, rows in (("tokens", tokens), ("cost", costs), ("waste", waste)):
        for item in rows:
            label = str(item.get("label") or "unknown")
            by_label.setdefault(label, {})[key] = item.get("value")
    if not by_label:
        return ""
    rows = []
    for label in sorted(by_label):
        values = by_label[label]
        rows.append(
            "<tr>"
            f"<td>{_h(label)}</td>"
            f"<td>{_h(_integer(values.get('tokens')))}</td>"
            f"<td>{_h(_money(values.get('cost')))}</td>"
            f"<td>{_h(_percent(values.get('waste')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Period</th><th>Total tokens</th><th>Estimated cost</th><th>Waste rate</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _project_section(report: dict[str, Any] | None) -> str:
    rows = report.get("projectRanking") if isinstance(report, dict) else []
    return _table_section(
        "Project ranking",
        ["Project", "Sessions", "Total tokens", "Failed", "Retries"],
        [
            [row.get("project"), row.get("sessions"), _integer(row.get("totalTokens")), row.get("failed"), row.get("retries")]
            for row in (rows or [])[:10]
        ],
        "No project data recorded.",
    )


def _provider_section(report: dict[str, Any] | None) -> str:
    rows = report.get("providerModelBreakdown") if isinstance(report, dict) else []
    return _table_section(
        "Provider and model",
        ["Provider", "Model", "Sessions", "Total tokens", "Output tokens"],
        [
            [row.get("provider"), row.get("model"), row.get("sessions"), _integer(row.get("totalTokens")), _integer(row.get("outputTokens"))]
            for row in (rows or [])[:10]
        ],
        "No provider/model data recorded.",
    )


def _task_section(report: dict[str, Any] | None) -> str:
    rows = report.get("taskRollups") if isinstance(report, dict) else []
    return _table_section(
        "Top expensive tasks",
        ["Session", "Project", "Model", "Task type", "Tokens", "Estimated cost"],
        [
            [row.get("sessionId"), row.get("projectPath"), row.get("model"), row.get("taskType"), _integer(row.get("totalTokens")), _money(row.get("estimatedCostUsd"))]
            for row in (rows or [])[:10]
        ],
        "No sessions found for this time window.",
    )


def _policy_section(report: dict[str, Any] | None) -> str:
    lifecycle = report.get("policyLifecycle") if isinstance(report, dict) and isinstance(report.get("policyLifecycle"), dict) else {}
    rows = [
        ["New", lifecycle.get("new", 0)],
        ["Open", lifecycle.get("open", 0)],
        ["Acknowledged", lifecycle.get("acked", 0)],
    ]
    for level, count in sorted((lifecycle.get("openByLevel") or {}).items()):
        rows.append([f"Open {level}", count])
    for level, count in sorted((lifecycle.get("newByLevel") or {}).items()):
        rows.append([f"New {level}", count])
    return _table_section("Policy findings", ["Metric", "Value"], rows, "No policy lifecycle data recorded.")


def _review_section(rows: list[dict[str, Any]]) -> str:
    return _table_section(
        "Review recurring diagnostics",
        ["Code", "Confidence", "Findings", "Sessions"],
        [[row.get("code"), row.get("confidence"), row.get("findings"), row.get("sessions")] for row in rows],
        "No recurring review diagnostics recorded.",
    )


def _alert_section(rows: list[dict[str, Any]]) -> str:
    return _table_section(
        "Alert history",
        ["Created", "Period", "Alert", "Actual", "Threshold"],
        [
            [
                row.get("createdAt"),
                f"{row.get('periodType')} {row.get('periodStart')}",
                row.get("key"),
                _format_value(row.get("actual"), "number"),
                _format_value(row.get("threshold"), "number"),
            ]
            for row in rows
        ],
        "No alert events recorded.",
    )


def _issue_section(report: dict[str, Any] | None) -> str:
    rows = report.get("highRiskIssues") if isinstance(report, dict) else []
    return _table_section(
        "High-risk issues",
        ["Severity", "Code", "Title", "Session"],
        [[row.get("severity"), row.get("code"), row.get("title"), row.get("sessionId")] for row in (rows or [])[:20]],
        "No high-risk issues detected.",
    )


def _table_section(title: str, headers: list[str], rows: list[list[Any]], empty_text: str) -> str:
    if not rows:
        return f"<section><h2>{_h(title)}</h2><p class=\"muted\">{_h(empty_text)}</p></section>"
    header_html = "".join(f"<th>{_h(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        row_html.append("<tr>" + "".join(f"<td>{_h(value)}</td>" for value in row) + "</tr>")
    return (
        f"<section><h2>{_h(title)}</h2>"
        f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(row_html)}</tbody></table>"
        "</section>"
    )


def _format_value(value: object, value_kind: str) -> str:
    if value_kind == "integer":
        return _integer(value)
    if value_kind == "money":
        return _money(value)
    if value_kind == "percent":
        return _percent(value)
    if value is None:
        return "unavailable"
    return f"{float(value):.6g}"


def _integer(value: object) -> str:
    if value is None:
        return "0"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _money(value: object) -> str:
    number = _number_or_none(value)
    if number is None:
        return "unavailable"
    return f"${number:.6f}"


def _percent(value: object) -> str:
    number = _number_or_none(value)
    if number is None:
        return "unavailable"
    return f"{number * 100:.2f}%"


def _number_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short_label(label: str) -> str:
    return label[:10]


def _h(value: object) -> str:
    return escape_html_text(value)
