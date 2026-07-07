from aicg.db import (
    connect,
    init_db,
    upsert_scan_error,
    upsert_session,
    upsert_tool_event,
    upsert_turn,
    upsert_issue,
)
from aicg.models import Issue, NormalizedSession, NormalizedToolEvent, NormalizedTurn
from aicg.pricing import apply_pricing
from aicg.reporter import build_daily_report, generate_markdown_report, render_markdown_report
from aicg.util import utc_now_iso


def test_reporter_includes_required_sections(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-test",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt-5",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-test",
                session_id="session-test",
                provider="codex",
                started_at=now,
                ended_at=now,
                status="completed",
                model="gpt-5",
                input_tokens=100,
                output_tokens=25,
            ),
        )
        upsert_tool_event(
            conn,
            NormalizedToolEvent(
                id="tool-test",
                session_id="session-test",
                turn_id="turn-test",
                provider="codex",
                tool_type="shell",
                tool_name="shell",
                status="completed",
                started_at=now,
                ended_at=now,
                output_bytes=12,
            ),
        )
        conn.commit()

        report = generate_markdown_report(conn, since="24h")
        report_model = build_daily_report(conn, since="24h")

    assert "Overview" in report
    assert "Project ranking" in report
    assert "Top expensive tasks" in report
    assert "Tool usage" in report
    assert "Recommended fixes" in report
    assert report_model["schemaVersion"] == 3
    assert {
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
    } <= set(report_model)
    assert render_markdown_report(report_model) == report
    assert report_model["overview"]["sessions"] == 1
    assert report_model["overview"]["turns"] == 1


def test_reporter_deduplicates_session_counts_for_task_rollups(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-rollup",
                provider="codex",
                project_path="/tmp/project",
                started_at=now,
                status="completed",
                model="gpt-5",
                privacy_flags="POSSIBLE_SECRET",
                policy_flags="RESTRICTED_SERVICE_CALL",
                background_flag=True,
                retry_count=1,
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-one",
                session_id="session-rollup",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt-5",
                task_type="implementation",
                input_tokens=100,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-two",
                session_id="session-rollup",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt-5.5",
                task_type="review",
                input_tokens=200,
            ),
        )
        upsert_scan_error(
            conn,
            provider="codex",
            source_file="/tmp/project/bad.jsonl",
            error_type="RuntimeError",
            error_message="secret path /tmp/project/bad.jsonl",
        )
        conn.commit()

        report = generate_markdown_report(conn, since="24h")

    assert "- Sessions: 1" in report
    assert "Each row is a session/model/task_type task rollup." in report
    assert "Privacy `POSSIBLE_SECRET`: 1 session(s)" in report
    assert "Policy `RESTRICTED_SERVICE_CALL`: 1 session(s)" in report
    assert "Background sessions: 1;" in report
    assert "| `/tmp/project` | `session-rollup` | completed | 1 | 300 |" in report
    assert "- Scan source failures: 1" in report
    assert "failed source file(s)" in report


def test_reporter_marks_partial_pricing_coverage(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-partial-price",
                provider="codex",
                started_at=now,
                status="completed",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-priced",
                session_id="session-partial-price",
                provider="codex",
                started_at=now,
                status="completed",
                model="priced-model",
                input_tokens=1_000_000,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-unpriced",
                session_id="session-partial-price",
                provider="codex",
                started_at=now,
                status="completed",
                model="missing-model",
                input_tokens=1_000_000,
            ),
        )
        apply_pricing(
            conn,
            ["session-partial-price"],
            {"prices": {"codex:priced-model": {"input_per_mtok_usd": 1}}},
        )
        conn.commit()

        report_model = build_daily_report(conn, since="24h")
        report = render_markdown_report(report_model)

    assert report_model["overview"]["pricedTurns"] == 1
    assert report_model["overview"]["pricingCoverage"] == {
        "pricedTurns": 1,
        "totalTurns": 2,
        "complete": False,
    }
    assert "1/2 turns priced; unpriced turns excluded" in report


def test_waste_breakdown_does_not_double_count_overlapping_categories(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-failed-bloat",
                provider="codex",
                started_at=now,
                status="failed",
                model="priced",
                estimated_cost_usd=1.0,
                credit_estimate=1.0,
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-failed-bloat",
                session_id="session-failed-bloat",
                provider="codex",
                started_at=now,
                status="failed",
                model="priced",
                input_tokens=1_000_000,
                estimated_cost_usd=1.0,
                credit_estimate=1.0,
            ),
        )
        upsert_issue(
            conn,
            Issue(
                id="issue-bloat",
                session_id="session-failed-bloat",
                severity="medium",
                code="TOOL_OUTPUT_BLOAT",
                title="Tool output bloat",
                created_at=now,
            ),
        )
        conn.commit()

        report_model = build_daily_report(conn, since="24h")

    waste = report_model["wasteBreakdown"]
    assert waste["totalCostUsd"] == 1.0
    assert waste["failedCostUsd"] == 1.0
    assert waste["bloatCostEstimateUsd"] is None
    assert waste["wastedCostUsd"] == 1.0
    assert waste["invariantWasteLteTotal"] is True


def test_reporter_escapes_markdown_table_cells(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-md",
                provider="codex",
                project_path="/tmp/a|b",
                started_at=now,
                status="completed",
                model="gpt|5",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-md",
                session_id="session-md",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt|5",
                task_type="review|inject",
                input_tokens=100,
            ),
        )
        upsert_tool_event(
            conn,
            NormalizedToolEvent(
                id="tool-md",
                session_id="session-md",
                turn_id="turn-md",
                provider="codex",
                tool_type="shell",
                tool_name="name|with|pipes",
                status="completed",
                started_at=now,
                ended_at=now,
                output_bytes=1,
            ),
        )
        conn.commit()

        report = generate_markdown_report(conn, since="24h")

    assert "a\\|b" in report
    assert "gpt\\|5" in report
    assert "name\\|with\\|pipes" in report


def test_reporter_chunks_large_session_sets(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        for index in range(1001):
            session_id = f"session-{index}"
            upsert_session(
                conn,
                NormalizedSession(
                    id=session_id,
                    provider="codex",
                    started_at=now,
                    status="completed",
                    created_at=now,
                ),
            )
            upsert_turn(
                conn,
                NormalizedTurn(
                    id=f"turn-{index}",
                    session_id=session_id,
                    provider="codex",
                    started_at=now,
                    status="completed",
                    input_tokens=1,
                ),
            )
        conn.commit()

        report_model = build_daily_report(conn, since="24h")

    assert report_model["overview"]["sessions"] == 1001
    assert report_model["overview"]["turns"] == 1001
