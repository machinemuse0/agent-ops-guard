import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aicg.db import connect, init_db, upsert_report_snapshot, upsert_session, upsert_tool_event, upsert_turn
from aicg.models import NormalizedSession, NormalizedToolEvent, NormalizedTurn
from aicg.schedule import render_schedule_text
from aicg.util import sha256_text, utc_now_iso


def test_summary_html_and_dashboard_escape_injected_log_strings(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()
    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-html",
                provider="codex",
                project_path="/tmp/a</td><script>alert(1)</script>",
                started_at=now,
                status="completed",
                model="<script>alert(1)</script>",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-html",
                session_id="session-html",
                provider="codex",
                started_at=now,
                status="completed",
                model="<script>alert(1)</script>",
                task_type="![x](https://evil.example/x.png)",
                input_tokens=100,
                output_tokens=25,
            ),
        )
        upsert_tool_event(
            conn,
            NormalizedToolEvent(
                id="tool-html",
                session_id="session-html",
                turn_id="turn-html",
                provider="codex",
                tool_type="shell",
                tool_name="</td><script>alert(1)</script>",
                status="completed",
                started_at=now,
                ended_at=now,
                output_bytes=1,
            ),
        )
        conn.commit()

    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]
    summary = subprocess.run(
        base + ["summary", "--period", "day", "--format", "html", "--out", "daily.html"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    dashboard = subprocess.run(
        base + ["dashboard", "--period", "day", "--out", "dashboard.html"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert summary.returncode == 0, summary.stderr
    assert dashboard.returncode == 0, dashboard.stderr
    summary_html = (aicg_home / "reports" / "daily.html").read_text(encoding="utf-8")
    dashboard_html = (aicg_home / "reports" / "dashboard.html").read_text(encoding="utf-8")
    combined = summary_html + dashboard_html
    assert "<script" not in combined.lower()
    assert "</td><script" not in combined.lower()
    assert "&lt;/td&gt;" in combined
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in combined
    assert "<img" not in combined.lower()
    assert not re.search(r"\b(?:src|href)\s*=", dashboard_html, re.IGNORECASE)
    assert "url(" not in dashboard_html.lower()
    assert "<svg" in dashboard_html
    assert "Total tokens" in dashboard_html
    assert "125" in dashboard_html
    with sqlite3.connect(db_path) as conn:
        cached = conn.execute("SELECT dashboard_model_json FROM report_snapshots").fetchone()[0]
    assert cached
    assert json.loads(cached)["schemaVersion"] == 1


def test_dashboard_90_snapshots_is_small_and_fast(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        for index in range(90):
            report = {
                "schemaVersion": 3,
                "generatedAt": f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
                "since": "24h",
                "overview": {
                    "sessions": index + 1,
                    "turns": index + 2,
                    "toolEvents": 0,
                    "totalTokens": (index + 1) * 100,
                    "outputTokens": 0,
                    "failedSessions": 0,
                    "interruptedSessions": 0,
                    "retrySessions": 0,
                    "backgroundSessions": 0,
                    "estimatedCostUsd": 0.01 * index,
                    "creditEstimate": 0,
                    "pricedTurns": 1,
                    "pricingCoverage": {"pricedTurns": 1, "totalTurns": 1, "complete": True},
                    "scanSourceFailures": 0,
                    "costPerSuccessfulSession": None,
                },
                "projectRanking": [],
                "providerModelBreakdown": [],
                "taskRollups": [],
                "failureRetry": [],
                "backgroundAnomalies": {"sessions": [], "flaggedIssueCount": 0},
                "toolUsage": [],
                "privacyPolicyFindings": {"privacy": [], "policy": [], "issueCodes": []},
                "policyLifecycle": {"new": 0, "open": 0, "acked": 0, "openByLevel": {}, "newByLevel": {}},
                "wasteBreakdown": {"wasteRate": index / 1000, "totalCostUsd": 0.01 * index},
                "efficiency": {},
                "gitActivity": [],
                "highRiskIssues": [],
                "lowerRiskIssueCount": 0,
                "recommendedFixes": [],
                "consistencyChecks": {},
                "period": {"type": "day", "start": f"2026-04-{index + 1:02d}T00:00:00+00:00", "end": None},
                "compare": None,
            }
            payload = json.dumps(report, sort_keys=True)
            upsert_report_snapshot(
                conn,
                period_type="day",
                period_start=report["period"]["start"],
                report_json=payload,
                report_hash=sha256_text(payload),
            )
        conn.commit()

    env = {**os.environ, "AICG_HOME": str(tmp_path)}
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "aicg", "dashboard", "--period", "day", "--limit", "90", "--out", "dash.html"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    elapsed = time.perf_counter() - start
    output = tmp_path / "reports" / "dash.html"
    assert result.returncode == 0, result.stderr
    assert elapsed < 3
    assert output.stat().st_size < 2 * 1024 * 1024


def test_alerts_check_and_summary_append_events(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    init_db(db_path)
    (aicg_home / "config.toml").write_text(
        """
[alerts]
interrupted_sessions_max = 0
new_policy_violations_max = 0
""",
        encoding="utf-8",
    )
    now = utc_now_iso()
    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-alert",
                provider="codex",
                started_at=now,
                status="interrupted",
                created_at=now,
            ),
        )
        conn.commit()
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]

    check = subprocess.run(base + ["alerts", "check", "--period", "day"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    summary = subprocess.run(
        base + ["summary", "--period", "day", "--format", "json", "--out", "daily.json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    dashboard = subprocess.run(base + ["dashboard", "--period", "day"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert check.returncode == 3
    assert "interrupted_sessions_max" in check.stdout
    assert summary.returncode == 0
    assert "interrupted_sessions_max" in summary.stdout
    assert dashboard.returncode == 0, dashboard.stderr
    html = (aicg_home / "reports" / "dashboard.html").read_text(encoding="utf-8")
    assert "interrupted_sessions_max" in html
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 1


def test_alerts_check_without_config_exits_zero(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    subprocess.run([sys.executable, "-m", "aicg", "init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)

    result = subprocess.run(
        [sys.executable, "-m", "aicg", "alerts", "check", "--period", "day"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "no [alerts] thresholds configured" in result.stdout


def test_schedule_print_outputs_installable_text():
    cron = render_schedule_text(
        scheduler="cron",
        time_value="09:00",
        commands_value="scan,summary,dashboard",
        python_executable="/usr/bin/python3",
        aicg_home="/tmp/aicg home",
    )
    launchd = render_schedule_text(
        scheduler="launchd",
        time_value="09:05",
        commands_value="scan",
        python_executable="/usr/bin/python3",
        aicg_home="/tmp/aicg",
    )
    systemd = render_schedule_text(
        scheduler="systemd",
        time_value="09:10",
        commands_value="dashboard",
        python_executable="/usr/bin/python3",
        aicg_home="/tmp/aicg",
    )

    assert cron.startswith("0 9 * * * ")
    assert "AICG_HOME='/tmp/aicg home'" in cron
    assert "/usr/bin/python3 -m aicg scan --since 24h --provider all" in cron
    assert "StartCalendarInterval" in launchd
    assert "<integer>5</integer>" in launchd
    assert "[Timer]" in systemd
    assert "OnCalendar=*-*-* 09:10:00" in systemd
    with pytest.raises(ValueError, match="HH:MM"):
        render_schedule_text(scheduler="cron", time_value="25:00", commands_value="scan")
