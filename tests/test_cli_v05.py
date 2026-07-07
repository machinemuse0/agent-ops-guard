import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_providers_and_incremental_scan(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "codex_exec_sample.jsonl", sessions / "sample.jsonl")
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]

    providers = subprocess.run(base + ["providers"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    init = subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    first = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    second = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert providers.returncode == 0
    assert {row["provider"] for row in json.loads(providers.stdout)} == {"codex", "claude"}
    assert init.returncode == 0
    assert "policy.toml" in init.stdout
    assert first.returncode == 0, first.stderr
    assert "files scanned: 1" in first.stdout
    assert second.returncode == 0, second.stderr
    assert "files scanned: 0" in second.stdout


def test_cli_policy_check_and_ack(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    _write_jsonl(
        sessions / "policy.jsonl",
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "policy-thread"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "policy-turn"}},
            {
                "type": "response_item",
                "timestamp": "2026-07-05T00:00:02Z",
                "payload": {
                    "type": "function_call",
                    "call_id": "call",
                    "name": "exec_command",
                    "arguments": {"cmd": "curl https://api.openai.com/v1/responses"},
                },
            },
        ],
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    check = subprocess.run(base + ["policy", "check", "--since", "9999d", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        finding_id = conn.execute("SELECT id FROM policy_findings LIMIT 1").fetchone()[0]
    secret_reason = "OPENAI_API_KEY=sk-test012345678901234567890123456789"
    ack = subprocess.run(base + ["policy", "ack", finding_id, "--reason", secret_reason], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    check_after_ack = subprocess.run(base + ["policy", "check", "--since", "9999d"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert scan.returncode == 0, scan.stderr
    assert check.returncode == 3
    assert json.loads(check.stdout)["findings"][0]["level"] == "violation"
    assert ack.returncode == 0, ack.stderr
    assert check_after_ack.returncode == 0
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        stored_reason = conn.execute("SELECT reason FROM policy_acks WHERE finding_id = ?", (finding_id,)).fetchone()[0]
    assert secret_reason not in stored_reason
    assert stored_reason.startswith("sha256:")


def test_policy_check_ignores_output_url_mentions(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    _write_jsonl(
        sessions / "mention.jsonl",
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "mention-thread"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "mention-turn"}},
            {
                "type": "response_item",
                "timestamp": "2026-07-05T00:00:02Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call",
                    "output": "documentation mentions https://api.openai.com/v1/responses",
                },
            },
        ],
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    check = subprocess.run(base + ["policy", "check", "--since", "9999d", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert scan.returncode == 0, scan.stderr
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["findings"] == []


def test_policy_override_allowlist_is_project_specific(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    project_path = "/tmp/aicg-oss-project"
    _write_jsonl(
        sessions / "override.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-07-05T00:00:00Z",
                "payload": {"id": "override-thread", "cwd": project_path},
            },
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "override-turn"}},
            {
                "type": "response_item",
                "timestamp": "2026-07-05T00:00:02Z",
                "payload": {
                    "type": "function_call",
                    "call_id": "call",
                    "name": "exec_command",
                    "arguments": {"cmd": "curl https://api.openai.com/v1/responses"},
                },
            },
        ],
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    (aicg_home / "policy.toml").write_text(
        f"""
[policy]
version = 1

[policy.services]
mode = "denylist"
deny = ["api.openai.com"]
allow = []
flag_mentions = false

[policy.overrides."{project_path}"]
services.mode = "allowlist"
services.allow = ["api.openai.com"]
""",
        encoding="utf-8",
    )

    scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    check = subprocess.run(base + ["policy", "check", "--since", "9999d", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert scan.returncode == 0, scan.stderr
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["findings"] == []


def test_custom_policy_secret_detects_and_capture_redacts(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    custom_secret = "itk_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    _write_jsonl(
        sessions / "custom-secret.jsonl",
        [
            {"type": "session_meta", "timestamp": "2026-07-05T00:00:00Z", "payload": {"id": "secret-thread"}},
            {"type": "turn_context", "timestamp": "2026-07-05T00:00:01Z", "payload": {"turn_id": "secret-turn"}},
            {
                "type": "response_item",
                "timestamp": "2026-07-05T00:00:02Z",
                "payload": {
                    "type": "function_call",
                    "call_id": "call",
                    "name": "exec_command",
                    "arguments": {"cmd": f"deploy --token {custom_secret}"},
                },
            },
        ],
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    (aicg_home / "policy.toml").write_text(
        """
[policy]
version = 1

[policy.services]
mode = "denylist"
deny = []
allow = []
flag_mentions = false

[policy.secrets]
enabled = true
builtin = []
[[policy.secrets.custom]]
name = "internal_token"
pattern = "itk_[A-Z0-9]{32}"
""",
        encoding="utf-8",
    )

    scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    check = subprocess.run(base + ["policy", "check", "--since", "9999d", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    capture = subprocess.run(
        base + ["capture", "codex", "--store-redacted", "--", sys.executable, "-c", f"print('{custom_secret}')"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert scan.returncode == 0, scan.stderr
    assert check.returncode == 3
    finding = json.loads(check.stdout)["findings"][0]
    assert finding["rule_id"] == "secrets.internal_token"
    assert finding["level"] == "violation"
    assert capture.returncode == 0, capture.stderr
    raw_text = next((aicg_home / "raw" / "codex").glob("*.jsonl")).read_text(encoding="utf-8")
    assert custom_secret not in raw_text
    assert "[REDACTED]" in raw_text


def test_scan_reprices_existing_sessions_when_no_files_change(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "codex_exec_sample.jsonl", sessions / "sample.jsonl")
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    first_scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    (aicg_home / "config.toml").write_text(
        """
[prices."codex:gpt-5"]
input_per_mtok_usd = 1
output_per_mtok_usd = 2
cache_read_input_per_mtok_usd = 0
cache_creation_input_per_mtok_usd = 0
credit_per_usd = 1
""",
        encoding="utf-8",
    )
    second_scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    summary = subprocess.run(
        base + ["summary", "--since", "9999d", "--format", "json", "--out", "daily.json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    report = json.loads((aicg_home / "reports" / "daily.json").read_text(encoding="utf-8"))

    assert first_scan.returncode == 0, first_scan.stderr
    assert second_scan.returncode == 0, second_scan.stderr
    assert "files scanned: 0" in second_scan.stdout
    assert summary.returncode == 0, summary.stderr
    assert report["overview"]["estimatedCostUsd"] is not None


def test_cli_capture_store_redacted_and_usage_import_period_snapshot(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]
    secret = "sk-test012345678901234567890123456789"
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        json.dumps(
            {
                "id": "usage-1",
                "model": "openrouter-model",
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "output_tokens": 30,
                "cost_usd": 0.01,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    capture = subprocess.run(
        base + ["capture", "codex", "--store-redacted", "--", sys.executable, "-c", f"print('{secret}')"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    imported = subprocess.run(
        base + ["import", "usage", "--provider", "openrouter", "--file", str(usage)],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    summary = subprocess.run(
        base + ["summary", "--period", "week", "--compare", "--format", "json", "--out", "weekly.json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    raw_files = list((aicg_home / "raw" / "codex").glob("*.jsonl"))
    assert capture.returncode == 0, capture.stderr
    assert secret in capture.stdout
    assert len(raw_files) == 1
    raw_text = raw_files[0].read_text(encoding="utf-8")
    assert secret not in raw_text
    assert "[REDACTED]" in raw_text
    assert imported.returncode == 0, imported.stderr
    assert summary.returncode == 0, summary.stderr
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM report_snapshots").fetchone()[0] == 1


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
