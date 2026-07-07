import json

import pytest

from aicg.db import connect, init_db, replace_review_findings_for_sessions, upsert_session, upsert_tool_event, upsert_turn
from aicg.models import NormalizedSession, NormalizedToolEvent, NormalizedTurn
from aicg.review import build_session_review, build_session_review_model, load_review_config, render_review_json
from aicg.util import sha256_text


def test_review_detects_repeated_identical_failure_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        for index in range(3):
            _turn(conn, "s-trigger", index, status="failed", error_hash="err-a")
        _session(conn, "s-boundary", status="failed")
        for index in range(2):
            _turn(conn, "s-boundary", index, status="failed", error_hash="err-a")
        conn.commit()

        assert _codes(conn, "s-trigger") == {"REPEATED_IDENTICAL_FAILURE"}
        assert "REPEATED_IDENTICAL_FAILURE" not in _codes(conn, "s-boundary")


def test_review_ignores_sparse_identical_failure_hashes(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-sparse", status="failed")
        _turn(conn, "s-sparse", 0, status="failed", error_hash="wrapper-failed")
        _turn(conn, "s-sparse", 1, status="failed", error_hash="root-a")
        _turn(conn, "s-sparse", 2, status="failed", error_hash="wrapper-failed")
        _turn(conn, "s-sparse", 3, status="failed", error_hash="root-b")
        _turn(conn, "s-sparse", 4, status="failed", error_hash="wrapper-failed")
        conn.commit()

        assert "REPEATED_IDENTICAL_FAILURE" not in _codes(conn, "s-sparse")


def test_review_detects_edit_retry_churn_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        _turn(conn, "s-trigger", 0, status="failed")
        for index in range(3):
            _tool(conn, "s-trigger", index, tool_type="file", tool_name="apply_patch", status="failed")
        _session(conn, "s-boundary", status="failed")
        _turn(conn, "s-boundary", 0, status="failed")
        for index in range(2):
            _tool(conn, "s-boundary", index, tool_type="file", tool_name="apply_patch", status="failed")
        conn.commit()

        assert "EDIT_RETRY_CHURN" in _codes(conn, "s-trigger")
        assert "EDIT_RETRY_CHURN" not in _codes(conn, "s-boundary")


def test_review_detects_context_overload_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        values = [(100, 900), (200, 700), (400, 400), (800, 200), (1600, 10)]
        for index, (uncached, cache_read) in enumerate(values):
            _turn(conn, "s-trigger", index, status="failed", input_uncached=uncached, cache_read=cache_read)
        _session(conn, "s-boundary", status="failed")
        for index, uncached in enumerate([100, 200, 150, 400, 800]):
            _turn(conn, "s-boundary", index, status="failed", input_uncached=uncached, cache_read=500)
        conn.commit()

        assert "CONTEXT_OVERLOAD" in _codes(conn, "s-trigger")
        assert "CONTEXT_OVERLOAD" not in _codes(conn, "s-boundary")


def test_review_context_overload_ignores_zero_start_and_unreliable_tokens(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-zero", status="failed")
        for index, (uncached, cache_read) in enumerate([(0, 900), (200, 700), (400, 400), (800, 200), (1600, 10)]):
            _turn(conn, "s-zero", index, status="failed", input_uncached=uncached, cache_read=cache_read)
        _session(conn, "s-unreliable", status="failed")
        values = [(100, 900), (200, 700), (400, 400), (800, 200), (1600, 10)]
        for index, (uncached, cache_read) in enumerate(values):
            flags = "TOKEN_USAGE_UNRELIABLE" if index == 0 else None
            _turn(conn, "s-unreliable", index, status="failed", input_uncached=uncached, cache_read=cache_read, token_flags=flags)
        conn.commit()

        assert "CONTEXT_OVERLOAD" not in _codes(conn, "s-zero")
        assert "CONTEXT_OVERLOAD" not in _codes(conn, "s-unreliable")


def test_review_detects_tool_output_flood_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        _turn(conn, "s-trigger", 0, status="completed", input_uncached=100)
        _tool(conn, "s-trigger", 0, tool_type="shell", tool_name="exec_command", status="completed", output_bytes=200000)
        _turn(conn, "s-trigger", 1, status="failed", input_uncached=300)
        _session(conn, "s-boundary", status="completed")
        _turn(conn, "s-boundary", 0, status="completed", input_uncached=100)
        _tool(conn, "s-boundary", 0, tool_type="shell", tool_name="exec_command", status="completed", output_bytes=200000)
        _turn(conn, "s-boundary", 1, status="completed", input_uncached=120)
        conn.commit()

        assert "TOOL_OUTPUT_FLOOD" in _codes(conn, "s-trigger")
        assert "TOOL_OUTPUT_FLOOD" not in _codes(conn, "s-boundary")


def test_review_detects_missing_setup_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        _turn(conn, "s-trigger", 0, status="failed")
        _tool(conn, "s-trigger", 0, tool_type="shell", tool_name="exec_command", status="failed", exit_code=127)
        _tool(conn, "s-trigger", 1, tool_type="shell", tool_name="exec_command", status="failed", exit_code=1)
        _session(conn, "s-boundary", status="failed")
        _turn(conn, "s-boundary", 0, status="failed")
        _tool(conn, "s-boundary", 0, tool_type="shell", tool_name="exec_command", status="failed", exit_code=1)
        conn.commit()

        assert "MISSING_SETUP" in _codes(conn, "s-trigger")
        assert "MISSING_SETUP" not in _codes(conn, "s-boundary")


def test_review_missing_setup_ignores_exploratory_exit_1(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-explore", status="failed")
        _turn(conn, "s-explore", 0, status="failed")
        _tool(conn, "s-explore", 0, tool_type="shell", tool_name="exec_command", status="failed", exit_code=1)
        _tool(conn, "s-explore", 1, tool_type="shell", tool_name="exec_command", status="failed", exit_code=1)
        _session(conn, "s-hard", status="failed")
        _turn(conn, "s-hard", 0, status="failed")
        _tool(conn, "s-hard", 0, tool_type="shell", tool_name="exec_command", status="failed", exit_code=127)
        _tool(conn, "s-hard", 1, tool_type="shell", tool_name="exec_command", status="failed", exit_code=126)
        conn.commit()

        assert "MISSING_SETUP" not in _codes(conn, "s-explore")
        hard = _findings(conn, "s-hard")
        assert hard["MISSING_SETUP"].confidence == "high"


def test_review_detects_no_progress_thrashing_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        for index in range(20):
            _turn(conn, "s-trigger", index, status="failed")
        _tool(conn, "s-trigger", 0, tool_type="file", tool_name="apply_patch", status="failed")
        _session(conn, "s-boundary", status="completed")
        for index in range(20):
            _turn(conn, "s-boundary", index, status="completed")
        conn.commit()

        assert "NO_PROGRESS_THRASHING" in _codes(conn, "s-trigger")
        assert "NO_PROGRESS_THRASHING" not in _codes(conn, "s-boundary")


def test_review_no_progress_ignores_read_only_sessions(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-no-edits", status="interrupted")
        for index in range(22):
            _turn(conn, "s-no-edits", index, status="interrupted")
        _session(conn, "s-claude-read", status="failed", provider="claude")
        for index in range(22):
            _turn(conn, "s-claude-read", index, status="failed", provider="claude")
            _tool(
                conn,
                "s-claude-read",
                index,
                tool_type="file",
                tool_name="Read",
                status="completed",
                provider="claude",
                turn_id=f"s-claude-read-turn-{index}",
            )
        conn.commit()

        assert "NO_PROGRESS_THRASHING" not in _codes(conn, "s-no-edits")
        assert "NO_PROGRESS_THRASHING" not in _codes(conn, "s-claude-read")


def test_review_detects_model_fallback_degradation_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="failed")
        _turn(conn, "s-trigger", 0, status="completed", model="model-a")
        _turn(conn, "s-trigger", 1, status="completed", model="model-a")
        _turn(conn, "s-trigger", 2, status="failed", model="model-b")
        _turn(conn, "s-trigger", 3, status="failed", model="model-b")
        _turn(conn, "s-trigger", 4, status="completed", model="model-b")
        _session(conn, "s-boundary", status="failed")
        _turn(conn, "s-boundary", 0, status="completed", model="model-a")
        _turn(conn, "s-boundary", 1, status="completed", model="model-a")
        _turn(conn, "s-boundary", 2, status="failed", model="model-b")
        _turn(conn, "s-boundary", 3, status="completed", model="model-b")
        _turn(conn, "s-boundary", 4, status="completed", model="model-b")
        conn.commit()

        assert "MODEL_FALLBACK_DEGRADATION" in _codes(conn, "s-trigger")
        assert "MODEL_FALLBACK_DEGRADATION" not in _codes(conn, "s-boundary")


def test_review_model_fallback_ignores_single_failed_turn_after_switch(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-switch", status="failed")
        _turn(conn, "s-switch", 0, status="completed", model="model-a")
        _turn(conn, "s-switch", 1, status="completed", model="model-a")
        _turn(conn, "s-switch", 2, status="failed", model="model-b")
        conn.commit()

        assert "MODEL_FALLBACK_DEGRADATION" not in _codes(conn, "s-switch")


def test_review_detects_interrupted_tail_and_boundary(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-trigger", status="interrupted")
        _turn(conn, "s-trigger", 0, status="interrupted")
        _tool(conn, "s-trigger", 1, tool_type="shell", tool_name="exec_command", status="running", ended_at=None)
        _session(conn, "s-boundary", status="interrupted")
        _turn(conn, "s-boundary", 0, status="interrupted")
        _tool(conn, "s-boundary", 1, tool_type="shell", tool_name="exec_command", status="completed")
        conn.commit()

        assert "INTERRUPTED_TAIL" in _codes(conn, "s-trigger")
        assert "INTERRUPTED_TAIL" not in _codes(conn, "s-boundary")


def test_review_interrupted_tail_treats_missing_tool_start_as_tail(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-running", status="interrupted")
        _turn(conn, "s-running", 0, status="interrupted")
        _tool(conn, "s-running", 0, tool_type="shell", tool_name="exec_command", status="running", started_at=None, ended_at=None)
        conn.commit()

        assert "INTERRUPTED_TAIL" in _codes(conn, "s-running")


def test_review_tool_output_flood_does_not_cross_source_files(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        _session(conn, "s-lineage", status="failed")
        _turn(conn, "s-lineage", 0, status="completed", source_hash="source-a", source_line=10)
        _tool(
            conn,
            "s-lineage",
            0,
            tool_type="shell",
            tool_name="exec_command",
            status="completed",
            output_bytes=200000,
            turn_id=None,
            source_hash="source-a",
            source_line=20,
        )
        _turn(conn, "s-lineage", 1, status="failed", input_uncached=300, source_hash="source-b", source_line=30)
        conn.commit()

        assert "TOOL_OUTPUT_FLOOD" not in _codes(conn, "s-lineage")


def test_review_output_is_deterministic_and_no_raw_secret(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    secret = "sk-test012345678901234567890123456789"
    with connect(db_path) as conn:
        _session(conn, "s-deterministic", status="failed")
        for index in range(3):
            _turn(conn, "s-deterministic", index, status="failed", error_hash=sha256_text(secret))
        review = build_session_review(conn, "s-deterministic", {"review": {}})
        assert review is not None
        replace_review_findings_for_sessions(conn, ["s-deterministic"], review.findings)
        conn.commit()
        first = render_review_json(build_session_review_model(review))
        second_review = build_session_review(conn, "s-deterministic", {"review": {}})
        assert second_review is not None
        second = render_review_json(build_session_review_model(second_review))

    assert json.loads(first) == json.loads(second)
    assert first == second
    assert secret not in first


def test_review_config_validation():
    with pytest.raises(ValueError, match="review.repeated_error_hash_min"):
        load_review_config({"review": {"repeated_error_hash_min": 0}})


def _codes(conn, session_id):
    review = build_session_review(conn, session_id, {"review": {}})
    assert review is not None
    return {finding.code for finding in review.findings}


def _findings(conn, session_id):
    review = build_session_review(conn, session_id, {"review": {}})
    assert review is not None
    return {finding.code: finding for finding in review.findings}


def _session(conn, session_id, *, status="completed", project_path="/tmp/project", provider="codex"):
    upsert_session(
        conn,
        NormalizedSession(
            id=session_id,
            provider=provider,
            project_path=project_path,
            source_file_hash="sourcehash",
            source_line_start=1,
            source_line_end=100,
            started_at="2026-07-05T00:00:00Z",
            ended_at="2026-07-05T00:00:59Z",
            status=status,
            model="gpt-test",
            created_at="2026-07-05T00:00:00Z",
        ),
    )


def _turn(
    conn,
    session_id,
    index,
    *,
    status="completed",
    model="gpt-test",
    input_uncached=100,
    cache_read=0,
    token_flags=None,
    error_hash=None,
    provider="codex",
    source_hash="sourcehash",
    source_line=None,
):
    line = source_line if source_line is not None else 10 + index
    upsert_turn(
        conn,
        NormalizedTurn(
            id=f"{session_id}-turn-{index}",
            session_id=session_id,
            provider=provider,
            source_file_hash=source_hash,
            source_line_start=line,
            source_line_end=line,
            started_at=f"2026-07-05T00:00:{index:02d}Z",
            ended_at=f"2026-07-05T00:00:{index:02d}Z",
            status=status,
            model=model,
            input_uncached_tokens=input_uncached,
            cache_read_input_tokens=cache_read,
            output_tokens=10,
            token_flags=token_flags,
            error_message_hash=error_hash,
        ),
    )


def _tool(
    conn,
    session_id,
    index,
    *,
    tool_type="shell",
    tool_name="exec_command",
    status="completed",
    output_bytes=0,
    exit_code=0,
    provider="codex",
    turn_id="__default__",
    source_hash="sourcehash",
    source_line=None,
    started_at="__default__",
    ended_at="2026-07-05T00:00:10Z",
):
    line = source_line if source_line is not None else 20 + index
    event_turn_id = f"{session_id}-turn-0" if turn_id == "__default__" else turn_id
    event_started_at = f"2026-07-05T00:00:{10 + index:02d}Z" if started_at == "__default__" else started_at
    upsert_tool_event(
        conn,
        NormalizedToolEvent(
            id=f"{session_id}-tool-{index}",
            session_id=session_id,
            turn_id=event_turn_id,
            provider=provider,
            source_file_hash=source_hash,
            source_line_start=line,
            source_line_end=line,
            tool_type=tool_type,
            tool_name=tool_name,
            status=status,
            started_at=event_started_at,
            ended_at=ended_at,
            output_bytes=output_bytes,
            exit_code=exit_code,
        ),
    )
