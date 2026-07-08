import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import aicg


FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_version_matches_package_metadata():
    result = subprocess.run(
        [sys.executable, "-m", "aicg", "--version"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )
    pyproject = tomllib.loads((Path.cwd() / "pyproject.toml").read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr
    assert aicg.__version__ == "0.8.0"
    assert pyproject["project"]["version"] == aicg.__version__
    assert aicg.__version__ in result.stdout


def test_cli_scan_summary_and_doctor_outputs(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    codex_sessions = codex_home / "sessions"
    claude_project = claude_home / "projects" / "-tmp-aicg-demo"
    codex_sessions.mkdir(parents=True)
    claude_project.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "codex_rollout_sample.jsonl", codex_sessions / "rollout.jsonl")
    shutil.copyfile(FIXTURES / "claude_transcript_sample.jsonl", claude_project / "claude.jsonl")

    env = {
        **os.environ,
        "AICG_HOME": str(aicg_home),
        "CODEX_HOME": str(codex_home),
        "CLAUDE_CONFIG_DIR": str(claude_home),
        "PATH": "",
    }
    base = [sys.executable, "-m", "aicg"]

    init_result = subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    scan_result = subprocess.run(
        base + ["scan", "--since", "9999d"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    summary_result = subprocess.run(
        base + ["summary", "--since", "9999d", "--out", "daily.md"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    summary_json_result = subprocess.run(
        base + ["summary", "--since", "9999d", "--format", "json", "--out", "daily.json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        session_id = conn.execute("SELECT id FROM sessions ORDER BY id LIMIT 1").fetchone()[0]
    inspect_result = subprocess.run(
        base + ["inspect", "session", session_id, "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    inspect_missing_result = subprocess.run(
        base + ["inspect", "session", "missing-id", "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    export_csv_result = subprocess.run(
        base + ["export", "--kind", "sessions", "--format", "csv", "--since", "9999d", "--out", "sessions.csv"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    export_json_result = subprocess.run(
        base + ["export", "--kind", "issues", "--format", "json", "--since", "9999d", "--out", "issues.json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    doctor_json_result = subprocess.run(
        base + ["doctor", "--json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    doctor_self_check_result = subprocess.run(
        base + ["doctor", "--self-check", "--json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    doctor_out_result = subprocess.run(
        base + ["doctor", "--out", "doctor.md"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert init_result.returncode == 0, init_result.stderr
    assert scan_result.returncode == 0, scan_result.stderr
    assert "files scanned: 2" in scan_result.stdout
    assert "files failed: 0" in scan_result.stdout
    assert "sessions inserted/updated: 2" in scan_result.stdout
    assert summary_result.returncode == 0, summary_result.stderr
    assert "report written:" in summary_result.stdout
    assert (aicg_home / "reports" / "daily.md").exists()
    assert "AgentOps Guard Daily Summary" in (aicg_home / "reports" / "daily.md").read_text()
    assert summary_json_result.returncode == 0, summary_json_result.stderr
    daily_json = json.loads((aicg_home / "reports" / "daily.json").read_text())
    assert daily_json["overview"]["sessions"] == 2
    assert "taskRollups" in daily_json
    assert inspect_result.returncode == 0, inspect_result.stderr
    inspect_json = json.loads(inspect_result.stdout)
    assert inspect_json["session"]["id"] == session_id
    assert "source_file" not in inspect_json["session"]
    assert "sk-test012345678901234567890123456789" not in inspect_result.stdout
    assert inspect_missing_result.returncode == 1
    assert "session not found" in inspect_missing_result.stderr
    assert export_csv_result.returncode == 0, export_csv_result.stderr
    assert (aicg_home / "reports" / "sessions.csv").exists()
    assert export_json_result.returncode == 0, export_json_result.stderr
    assert "sk-test012345678901234567890123456789" not in (aicg_home / "reports" / "issues.json").read_text()
    assert doctor_json_result.returncode == 0, doctor_json_result.stderr
    assert '"schemaVersion": 1' in doctor_json_result.stdout
    assert '"offline": true' in doctor_json_result.stdout
    assert doctor_self_check_result.returncode == 0, doctor_self_check_result.stderr
    self_check_json = json.loads(doctor_self_check_result.stdout)
    assert self_check_json["selfCheck"]["status"] in {"pass", "warning"}
    assert doctor_out_result.returncode == 0, doctor_out_result.stderr
    assert (aicg_home / "reports" / "doctor.md").exists()


def test_cli_capture_records_metadata_without_raw_stdout_copy(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicg",
            "capture",
            "codex",
            "--",
            sys.executable,
            "-c",
            "print('hello from capture')",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "hello from capture" in result.stdout
    assert list((aicg_home / "raw" / "codex").glob("*.jsonl")) == []
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        raw_path = conn.execute("SELECT raw_path FROM runs").fetchone()[0]
    assert raw_path is None


def test_cli_export_empty_csv_writes_header(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicg",
            "export",
            "--kind",
            "sessions",
            "--format",
            "csv",
            "--since",
            "24h",
            "--out",
            "empty-sessions.csv",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    exported = (aicg_home / "reports" / "empty-sessions.csv").read_text(encoding="utf-8")
    assert exported.startswith("id,provider,project_path")
