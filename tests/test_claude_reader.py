import json
from pathlib import Path

from aicg.db import connect, count_rows, import_records, init_db
from aicg.readers.claude_jsonl import ClaudeJsonlReader
from aicg.util import stable_id


FIXTURES = Path(__file__).parent / "fixtures"


def test_claude_reader_parses_transcript_usage_and_tools():
    records = ClaudeJsonlReader().read(FIXTURES / "claude_transcript_sample.jsonl")

    assert len(records.sessions) == 1
    assert len(records.turns) == 2
    assert len(records.tool_events) == 1

    session = records.sessions[0]
    first_turn = records.turns[0]
    second_turn = records.turns[1]
    tool = records.tool_events[0]

    assert session.provider == "claude"
    assert session.project_path == "/tmp/aicg-demo"
    assert session.model == "claude-sonnet-4"
    assert first_turn.input_tokens == 200
    assert first_turn.cache_creation_input_tokens == 20
    assert first_turn.cache_read_input_tokens == 30
    assert first_turn.output_tokens == 40
    assert second_turn.input_tokens == 10
    assert tool.tool_type == "shell"
    assert tool.tool_name == "Bash"
    assert tool.status == "completed"
    assert tool.output_bytes == 2


def test_claude_reader_updates_session_id_after_summary_line(tmp_path):
    path = tmp_path / "claude-summary-first.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "summary", "timestamp": "2026-07-05T00:00:00Z", "summary": "snapshot"},
            {
                "type": "assistant",
                "sessionId": "real-session",
                "timestamp": "2026-07-05T00:00:01Z",
                "uuid": "msg-1",
                "message": {
                    "id": "msg-1",
                    "role": "assistant",
                    "model": "claude-sonnet-4",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ],
    )

    records = ClaudeJsonlReader().read(path)

    assert records.sessions[0].id == stable_id("session", "claude", "real-session")
    assert records.turns[0].session_id == records.sessions[0].id


def test_claude_resume_history_message_ids_dedupe_across_sessions(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    first = tmp_path / "resume-1.jsonl"
    second = tmp_path / "resume-2.jsonl"
    assistant_event = {
        "type": "assistant",
        "timestamp": "2026-07-05T00:00:01Z",
        "uuid": "same-message",
        "message": {
            "id": "same-message",
            "role": "assistant",
            "model": "claude-sonnet-4",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    }
    _write_jsonl(first, [{**assistant_event, "sessionId": "session-one"}])
    _write_jsonl(second, [{**assistant_event, "sessionId": "session-two"}])

    with connect(db_path) as conn:
        import_records(conn, ClaudeJsonlReader().read(first))
        import_records(conn, ClaudeJsonlReader().read(second))
        conn.commit()
        usage = conn.execute("SELECT SUM(input_tokens) AS input_tokens FROM turns").fetchone()

        assert count_rows(conn, "turns") == 1
        assert usage["input_tokens"] == 100


def test_claude_reader_detects_sidechain_after_first_event(tmp_path):
    path = tmp_path / "claude-sidechain.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "sessionId": "sidechain-session",
                "timestamp": "2026-07-05T00:00:00Z",
                "message": {"role": "user", "content": "run"},
            },
            {
                "type": "assistant",
                "sessionId": "sidechain-session",
                "timestamp": "2026-07-05T00:00:01Z",
                "isSidechain": True,
                "uuid": "side-msg",
                "message": {
                    "id": "side-msg",
                    "role": "assistant",
                    "model": "claude-sonnet-4",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ],
    )

    records = ClaudeJsonlReader().read(path)

    assert records.sessions[0].background_flag is True
    assert records.turns[0].background_flag is True


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
