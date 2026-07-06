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
