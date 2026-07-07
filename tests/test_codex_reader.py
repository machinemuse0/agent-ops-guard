import json
from pathlib import Path

from aicg.readers.codex_jsonl import CodexJsonlReader


FIXTURES = Path(__file__).parent / "fixtures"


def test_codex_reader_parses_basic_exec_sample():
    records = CodexJsonlReader().read(FIXTURES / "codex_exec_sample.jsonl")

    assert len(records.sessions) == 1
    assert len(records.turns) == 1
    assert len(records.tool_events) == 1

    session = records.sessions[0]
    turn = records.turns[0]
    tool = records.tool_events[0]

    assert session.provider == "codex"
    assert session.raw_event_count == 5
    assert session.malformed_line_count == 0
    assert session.source_file_hash
    assert session.source_line_start == 1
    assert session.source_line_end == 5
    assert turn.source_file_hash == session.source_file_hash
    assert turn.source_line_start == 2
    assert turn.source_line_end == 5
    assert tool.source_file_hash == session.source_file_hash
    assert tool.source_line_start == 3
    assert tool.source_line_end == 4
    assert turn.input_tokens == 1200
    assert turn.cached_input_tokens == 100
    assert turn.output_tokens == 300
    assert turn.reasoning_output_tokens == 40
    assert turn.cache_creation_input_tokens == 10
    assert turn.cache_read_input_tokens == 20
    assert tool.tool_type == "shell"
    assert tool.status == "completed"
    assert tool.output_bytes == 2


def test_codex_reader_counts_malformed_lines():
    records = CodexJsonlReader().read(FIXTURES / "codex_exec_malformed_sample.jsonl")

    assert len(records.sessions) == 1
    assert records.malformed_line_count == 1
    assert records.sessions[0].malformed_line_count == 1


def test_codex_reader_parses_rollout_sample_without_raw_payload_storage():
    records = CodexJsonlReader().read(FIXTURES / "codex_rollout_sample.jsonl")

    assert len(records.sessions) == 1
    assert len(records.turns) == 1
    assert len(records.tool_events) == 1

    session = records.sessions[0]
    turn = records.turns[0]
    tool = records.tool_events[0]

    assert session.project_path == "/tmp/aicg-demo"
    assert session.model == "gpt-5.5"
    assert "POSSIBLE_SECRET" in (session.privacy_flags or "")
    assert "RESTRICTED_SERVICE_CALL" in (session.policy_flags or "")
    assert turn.input_tokens == 1000
    assert turn.cached_input_tokens == 100
    assert turn.output_tokens == 250
    assert turn.reasoning_output_tokens == 50
    assert tool.tool_name == "exec_command"
    assert tool.output_bytes == 2


def test_codex_reader_diffs_cumulative_total_token_usage(tmp_path):
    path = tmp_path / "codex-total-only.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "thread-total"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "turn-1"}},
            {
                "type": "event_msg",
                "timestamp": "2026-07-05T00:00:02Z",
                "payload": {
                    "type": "token_count",
                    "turn_id": "turn-1",
                    "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 10}},
                },
            },
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:03Z", "payload": {"turn_id": "turn-2"}},
            {
                "type": "event_msg",
                "timestamp": "2026-07-05T00:00:04Z",
                "payload": {
                    "type": "token_count",
                    "turn_id": "turn-2",
                    "info": {"total_token_usage": {"input_tokens": 150, "output_tokens": 15}},
                },
            },
        ],
    )

    records = CodexJsonlReader().read(path)

    assert [turn.input_tokens for turn in records.turns] == [0, 50]
    assert [turn.output_tokens for turn in records.turns] == [0, 5]
    assert records.turns[0].token_flags == "TOKEN_USAGE_UNRELIABLE"


def test_codex_reader_keeps_unfinished_turns_interrupted(tmp_path):
    path = tmp_path / "codex-interrupted.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "thread-running"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "turn-running"}},
        ],
    )

    records = CodexJsonlReader().read(path)

    assert records.sessions[0].status == "interrupted"
    assert records.turns[0].status == "interrupted"


def test_codex_reader_fallback_turn_ids_include_source_file_hash(tmp_path):
    first = tmp_path / "rollout-1.jsonl"
    second = tmp_path / "rollout-2.jsonl"
    _write_jsonl(
        first,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "thread-same"}},
            {"type": "turn.started", "timestamp": "2026-07-05T00:00:01Z", "payload": {}},
        ],
    )
    _write_jsonl(
        second,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:01:00Z", "payload": {"id": "thread-same"}},
            {"type": "turn.started", "timestamp": "2026-07-05T00:01:01Z", "payload": {}},
        ],
    )

    first_records = CodexJsonlReader().read(first)
    second_records = CodexJsonlReader().read(second)

    assert first_records.sessions[0].id == second_records.sessions[0].id
    assert first_records.turns[0].id != second_records.turns[0].id


def test_codex_reader_does_not_treat_output_url_mentions_as_service_calls(tmp_path):
    path = tmp_path / "codex-output-mention.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "thread-output"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "turn-output"}},
            {
                "type": "response_item",
                "timestamp": "2026-07-05T00:00:02Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-output",
                    "output": "docs mention https://api.openai.com/v1/responses",
                },
            },
        ],
    )

    records = CodexJsonlReader().read(path)

    assert records.sessions[0].policy_flags is None


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
