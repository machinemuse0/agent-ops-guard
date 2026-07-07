from pathlib import Path

from aicg.analyzer import analyze_sessions
from aicg.db import connect, import_records, init_db, replace_issues_for_sessions, upsert_session, upsert_turn
from aicg.models import NormalizedSession, NormalizedTurn
from aicg.readers.codex_jsonl import CodexJsonlReader
from aicg.util import utc_now_iso


FIXTURES = Path(__file__).parent / "fixtures"


def test_analyzer_flags_privacy_and_policy_issues(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    records = CodexJsonlReader().read(FIXTURES / "codex_rollout_sample.jsonl")

    with connect(db_path) as conn:
        import_records(conn, records)
        session_ids = [session.id for session in records.sessions]
        issues = analyze_sessions(conn, session_ids, {"thresholds": {"high_session_tokens": 100}})
        replace_issues_for_sessions(conn, session_ids, issues)
        conn.commit()

        codes = {
            row["code"]
            for row in conn.execute("SELECT code FROM issues").fetchall()
        }
        stored_text = "\n".join(
            str(tuple(row))
            for table in ("sessions", "turns", "tool_events", "issues", "issue_evidence")
            for row in conn.execute(f"SELECT * FROM {table}").fetchall()
        )
        evidence_count = conn.execute("SELECT COUNT(*) AS count FROM issue_evidence").fetchone()["count"]
        evidence_rows = conn.execute(
            "SELECT source_file_hash, metric_name, metric_value FROM issue_evidence"
        ).fetchall()

    assert "HIGH_COST_TASK" in codes
    assert "POSSIBLE_SECRET" in codes
    assert "RESTRICTED_SERVICE_CALL" in codes
    assert evidence_count >= len(codes)
    assert all(row["source_file_hash"] for row in evidence_rows)
    assert all(row["metric_name"] and row["metric_value"] for row in evidence_rows)
    assert "sk-test012345678901234567890123456789" not in stored_text


def test_analyzer_uses_provider_aware_token_totals_for_codex(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-token-semantics",
                provider="codex",
                started_at=now,
                status="completed",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-token-semantics",
                session_id="session-token-semantics",
                provider="codex",
                started_at=now,
                status="completed",
                input_tokens=100,
                cached_input_tokens=100,
                output_tokens=40,
                reasoning_output_tokens=40,
            ),
        )
        conn.commit()

        issues = analyze_sessions(
            conn,
            ["session-token-semantics"],
            {"thresholds": {"high_session_tokens": 150}},
        )

    assert "HIGH_COST_TASK" not in {issue.code for issue in issues}


def test_analyzer_ignores_unknown_model_for_switch_detection(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-models",
                provider="codex",
                started_at=now,
                status="completed",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-model-unknown",
                session_id="session-models",
                provider="codex",
                started_at=now,
                status="completed",
                model=None,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-model-known",
                session_id="session-models",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt-5",
            ),
        )
        conn.commit()

        issues = analyze_sessions(conn, ["session-models"], {"thresholds": {}})

    assert "MODEL_SWITCH_ANOMALY" not in {issue.code for issue in issues}
