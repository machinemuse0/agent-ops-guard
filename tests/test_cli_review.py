import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from aicg.db import init_db, upsert_session, upsert_turn
from aicg.models import NormalizedSession, NormalizedTurn
from aicg.util import sha256_text


def test_cli_review_session_export_issue_and_determinism(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    source_file = tmp_path / "source.jsonl"
    source_file.write_text("{}\n", encoding="utf-8")
    secret = "sk-test012345678901234567890123456789"
    init_db(db_path)
    _insert_repeated_failure(
        db_path,
        "s-cli",
        started_at="2026-07-05T00:00:00Z",
        error_hash=sha256_text(secret),
        source_file=str(source_file),
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]

    first = subprocess.run(
        base + ["review", "--session", "s-cli", "--format", "json", "--export-issue", "review.md"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    second = subprocess.run(
        base + ["review", "--session", "s-cli", "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    source = subprocess.run(
        base + ["inspect", "source", "sourcehash", "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    issue_path = aicg_home / "reports" / "review.md"

    assert first.returncode == 3, first.stderr
    assert second.returncode == 3, second.stderr
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert "python -m aicg inspect source sourcehash" in issue_path.read_text(encoding="utf-8")
    assert source.returncode == 0, source.stderr
    assert json.loads(source.stdout)["sources"][0]["sourceFile"] == str(source_file)
    assert json.loads(source.stdout)["sources"][0]["exists"] is True
    assert "review issue template written:" in first.stderr
    assert secret not in first.stdout
    assert issue_path.exists()
    issue_text = issue_path.read_text(encoding="utf-8")
    assert "This file contains no prompt/code/output raw text." in issue_text
    assert secret not in issue_text
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM review_evidence").fetchone()[0] >= 1


def test_cli_review_last_batch_project_and_fail_on_none(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    init_db(db_path)
    _insert_repeated_failure(
        db_path,
        "s-old",
        project_path="/tmp/aicg-review-project",
        started_at="2026-07-05T00:00:00Z",
        wasted_cost=1.0,
    )
    _insert_repeated_failure(
        db_path,
        "s-new",
        project_path="/tmp/aicg-review-project",
        started_at="2026-07-05T00:01:00Z",
        wasted_cost=10.0,
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]

    last = subprocess.run(base + ["review", "--last", "--format", "json", "--fail-on", "none"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    batch = subprocess.run(
        base + ["review", "--since", "9999d", "--top", "1", "--format", "json", "--fail-on", "none"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    project = subprocess.run(
        base + ["review", "--project", "/tmp/aicg-review-project", "--since", "9999d", "--format", "json", "--fail-on", "none"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert last.returncode == 0, last.stderr
    assert json.loads(last.stdout)["session"]["id"] == "s-new"
    assert batch.returncode == 0, batch.stderr
    assert json.loads(batch.stdout)["sessions"][0]["id"] == "s-new"
    assert project.returncode == 0, project.stderr
    project_model = json.loads(project.stdout)
    assert project_model["patterns"][0]["code"] == "REPEATED_IDENTICAL_FAILURE"
    assert project_model["patterns"][0]["sessions"] == 2
    assert project_model["limit"] == 200
    assert project_model["matchedSessions"] == 2
    assert project_model["truncated"] is False


def test_cli_review_project_limit_scope(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    init_db(db_path)
    for index in range(3):
        _insert_repeated_failure(
            db_path,
            f"s-{index}",
            project_path="/tmp/aicg-review-project",
            started_at=f"2026-07-05T00:0{index}:00Z",
        )
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    limited = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicg",
            "review",
            "--project",
            "/tmp/aicg-review-project",
            "--since",
            "9999d",
            "--limit",
            "2",
            "--format",
            "json",
            "--fail-on",
            "none",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    full = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicg",
            "review",
            "--project",
            "/tmp/aicg-review-project",
            "--since",
            "9999d",
            "--limit",
            "0",
            "--format",
            "json",
            "--fail-on",
            "none",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert limited.returncode == 0, limited.stderr
    limited_model = json.loads(limited.stdout)
    assert limited_model["limit"] == 2
    assert limited_model["matchedSessions"] == 3
    assert limited_model["sessionsReviewed"] == 2
    assert limited_model["truncated"] is True
    assert limited_model["patterns"][0]["sessions"] == 2
    assert full.returncode == 0, full.stderr
    full_model = json.loads(full.stdout)
    assert full_model["limit"] == 0
    assert full_model["matchedSessions"] == 3
    assert full_model["sessionsReviewed"] == 3
    assert full_model["truncated"] is False
    assert full_model["patterns"][0]["sessions"] == 3


def test_cli_review_top_prefers_failed_sessions_without_pricing(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    init_db(db_path)
    _insert_repeated_failure(
        db_path,
        "s-failed",
        started_at="2026-07-05T00:00:00Z",
        wasted_cost=None,
    )
    with sqlite3.connect(db_path) as raw:
        raw.row_factory = sqlite3.Row
        upsert_session(
            raw,
            NormalizedSession(
                id="s-recent-completed",
                provider="codex",
                project_path="/tmp/project",
                source_file_hash="sourcehash",
                source_file="/tmp/source.jsonl",
                source_line_start=1,
                source_line_end=10,
                started_at="2026-07-05T00:01:00Z",
                ended_at="2026-07-05T00:01:00Z",
                status="completed",
                model="gpt-test",
                created_at="2026-07-05T00:01:00Z",
            ),
        )
        upsert_turn(
            raw,
            NormalizedTurn(
                id="s-recent-completed-turn-0",
                session_id="s-recent-completed",
                provider="codex",
                started_at="2026-07-05T00:01:00Z",
                ended_at="2026-07-05T00:01:00Z",
                status="completed",
                input_uncached_tokens=999999,
                output_tokens=1,
            ),
        )
        raw.commit()
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    result = subprocess.run(
        [sys.executable, "-m", "aicg", "review", "--since", "9999d", "--top", "1", "--format", "json", "--fail-on", "none"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["sessions"][0]["id"] == "s-failed"


def test_cli_review_invalid_config_exits_2(tmp_path):
    aicg_home = tmp_path / "aicg"
    db_path = aicg_home / "aicg.sqlite"
    init_db(db_path)
    (aicg_home / "config.toml").write_text("[review]\nrepeated_error_hash_min = 0\n", encoding="utf-8")
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    result = subprocess.run(
        [sys.executable, "-m", "aicg", "review", "--session", "missing"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "review.repeated_error_hash_min" in result.stderr


def _insert_repeated_failure(
    db_path,
    session_id,
    *,
    project_path="/tmp/project",
    started_at="2026-07-05T00:00:00Z",
    error_hash="err-a",
    wasted_cost=1.0,
    source_file="/tmp/source.jsonl",
):
    with sqlite3.connect(db_path) as raw:
        raw.row_factory = sqlite3.Row
        upsert_session(
            raw,
            NormalizedSession(
                id=session_id,
                provider="codex",
                project_path=project_path,
                source_file=source_file,
                source_file_hash="sourcehash",
                source_line_start=1,
                source_line_end=100,
                started_at=started_at,
                ended_at=started_at,
                status="failed",
                model="gpt-test",
                wasted_cost_usd=wasted_cost,
                created_at=started_at,
            ),
        )
        for index in range(5):
            upsert_turn(
                raw,
                NormalizedTurn(
                    id=f"{session_id}-turn-{index}",
                    session_id=session_id,
                    provider="codex",
                    source_file_hash="sourcehash",
                    source_line_start=10 + index,
                    source_line_end=10 + index,
                    started_at=f"2026-07-05T00:00:{index:02d}Z",
                    ended_at=f"2026-07-05T00:00:{index:02d}Z",
                    status="failed",
                    input_uncached_tokens=100,
                    output_tokens=10,
                    error_message_hash=error_hash,
                ),
            )
        raw.commit()
