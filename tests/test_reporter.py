from aicg.db import (
    connect,
    init_db,
    upsert_scan_error,
    upsert_session,
    upsert_tool_event,
    upsert_turn,
)
from aicg.models import NormalizedSession, NormalizedToolEvent, NormalizedTurn
from aicg.reporter import generate_markdown_report
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

    assert "Overview" in report
    assert "Project ranking" in report
    assert "Top expensive tasks" in report
    assert "Tool usage" in report
    assert "Recommended fixes" in report


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
