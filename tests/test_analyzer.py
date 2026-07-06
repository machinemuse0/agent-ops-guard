from pathlib import Path

from aicg.analyzer import analyze_sessions
from aicg.db import connect, import_records, init_db, replace_issues_for_sessions
from aicg.readers.codex_jsonl import CodexJsonlReader


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
            for table in ("sessions", "turns", "tool_events", "issues")
            for row in conn.execute(f"SELECT * FROM {table}").fetchall()
        )

    assert "HIGH_COST_TASK" in codes
    assert "POSSIBLE_SECRET" in codes
    assert "RESTRICTED_SERVICE_CALL" in codes
    assert "sk-test012345678901234567890123456789" not in stored_text
