import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from aicg.readers import ClaudeJsonlReader, CodexJsonlReader


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_builtin_readers_are_idempotent():
    cases = [
        (CodexJsonlReader(), FIXTURES / "codex_exec_sample.jsonl"),
        (ClaudeJsonlReader(), FIXTURES / "claude_transcript_sample.jsonl"),
    ]
    for reader, path in cases:
        first = reader.read(path)
        second = reader.read(path)
        assert _stable_records(first) == _stable_records(second)


def test_token_buckets_are_non_negative_and_reasoning_is_subset():
    cases = [
        CodexJsonlReader().read(FIXTURES / "codex_exec_sample.jsonl"),
        ClaudeJsonlReader().read(FIXTURES / "claude_transcript_sample.jsonl"),
    ]
    for records in cases:
        for turn in records.turns:
            assert turn.input_uncached_tokens >= 0
            assert turn.cache_read_input_tokens >= 0
            assert turn.cache_creation_input_tokens >= 0
            assert turn.output_tokens >= 0
            assert turn.reasoning_output_tokens <= turn.output_tokens


def test_truncated_codex_fixture_is_interrupted(tmp_path):
    path = tmp_path / "truncated.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "thread"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "turn"}},
        ],
    )

    records = CodexJsonlReader().read(path)

    assert records.sessions[0].status == "interrupted"
    assert records.turns[0].status == "interrupted"


def test_bad_lines_do_not_raise_and_are_counted(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b'{"type":"session_meta","payload":{"id":"x"}}\n{"bad"\xff\n')

    records = CodexJsonlReader().read(path)

    assert records.malformed_line_count == 1
    assert records.sessions[0].malformed_line_count == 1


def test_fake_secret_not_copied_to_normalized_records(tmp_path):
    path = tmp_path / "secret.jsonl"
    secret = "sk-test012345678901234567890123456789"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "thread"}},
            {
                "type": "response_item",
                "timestamp": "2026-07-05T00:00:01Z",
                "payload": {"type": "function_call_output", "call_id": "call", "output": secret},
            },
        ],
    )

    records = CodexJsonlReader().read(path)

    assert secret not in json.dumps(_stable_records(records), sort_keys=True)


def _stable_records(records):
    data = {}
    for key in ("sessions", "turns", "tool_events"):
        items = []
        for item in getattr(records, key):
            item_dict = asdict(item) if is_dataclass(item) else dict(item)
            item_dict.pop("created_at", None)
            items.append(item_dict)
        data[key] = items
    data["malformed_line_count"] = records.malformed_line_count
    data["source_file_hash"] = records.source_file_hash
    return data


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
