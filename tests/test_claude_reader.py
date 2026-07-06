from pathlib import Path

from aicg.readers.claude_jsonl import ClaudeJsonlReader


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
