import os
import shutil
import subprocess
import sys
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


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
    doctor_json_result = subprocess.run(
        base + ["doctor", "--json"],
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
    assert doctor_json_result.returncode == 0, doctor_json_result.stderr
    assert '"schemaVersion": 1' in doctor_json_result.stdout
    assert '"offline": true' in doctor_json_result.stdout
    assert doctor_out_result.returncode == 0, doctor_out_result.stderr
    assert (aicg_home / "reports" / "doctor.md").exists()
