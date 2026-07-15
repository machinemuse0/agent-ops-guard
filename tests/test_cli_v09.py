import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_format_drift_scan_summary_and_doctor(tmp_path):
    aicg_home = tmp_path / "aicg"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    source = sessions / "future.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "timestamp": "2026-07-08T00:00:00Z", "payload": {"id": "future-thread"}}),
                json.dumps({"type": "aicg.future_event", "timestamp": "2026-07-08T00:00:01Z", "payload": {"future": True}, "futureField": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home), "PATH": ""}
    base = [sys.executable, "-m", "aicg"]

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    summary = subprocess.run(base + ["summary", "--since", "9999d", "--format", "json", "--out", "daily.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    doctor = subprocess.run(base + ["doctor", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert scan.returncode == 0, scan.stderr
    assert "format drift:" in scan.stdout
    assert "aicg.future_event=1" in scan.stdout
    assert summary.returncode == 0, summary.stderr
    report = json.loads((aicg_home / "reports" / "daily.json").read_text(encoding="utf-8"))
    assert report["formatDrift"]["warnings"]
    assert "token statistics may be incomplete" in report["formatDrift"]["warnings"][0]
    assert doctor.returncode == 0, doctor.stderr
    doctor_model = json.loads(doctor.stdout)
    assert doctor_model["formatDrift"]["observations"][0]["observationKey"] == "unknown_event_type:aicg.future_event"
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        count = conn.execute(
            "SELECT count FROM format_observations WHERE observation_key = 'unknown_event_type:aicg.future_event'"
        ).fetchone()[0]
    assert count == 1


def test_fixture_redact_removes_raw_content_paths_and_secrets(tmp_path):
    source = tmp_path / "raw.jsonl"
    output = tmp_path / "fixture.jsonl"
    keep_output = tmp_path / "fixture-keep.jsonl"
    secret = "sk-" + "A" * 40
    raw_message = f"use {secret} in /Users/alice/private/project"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "cwd": "/Users/alice/private/project",
                        "payload": {
                            "type": "function_call_output",
                            "output": raw_message,
                            "arguments": ["--token", "SECRET_TOKEN=value"],
                        },
                    }
                ),
                "{bad",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    base = [sys.executable, "-m", "aicg"]

    result = subprocess.run(base + ["fixture", "redact", str(source), "--out", str(output)], cwd=Path.cwd(), text=True, capture_output=True)
    keep = subprocess.run(base + ["fixture", "redact", str(source), "--out", str(keep_output), "--keep-structure"], cwd=Path.cwd(), text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert secret not in text
    assert "SECRET_TOKEN=value" not in text
    assert "/Users/alice" not in text
    assert "/REDACTED/project-1" in text
    assert "aicg_redacted_malformed" in text
    assert "{bad" not in text
    assert keep.returncode == 0, keep.stderr
    keep_rows = [json.loads(line) for line in keep_output.read_text(encoding="utf-8").splitlines()]
    assert len(keep_rows[0]["payload"]["output"]) == len(raw_message)


def test_support_bundle_is_local_reviewable_and_raw_free(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home), "PATH": ""}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)

    default_bundle = subprocess.run(base + ["support", "bundle", "--out", "bundle.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    include_config = subprocess.run(base + ["support", "bundle", "--out", "bundle-config.json", "--include-config"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert default_bundle.returncode == 0, default_bundle.stderr
    assert "review required" in default_bundle.stdout
    text = (aicg_home / "reports" / "bundle.json").read_text(encoding="utf-8")
    model = json.loads(text)
    assert model["includesConfig"] is False
    assert "config" not in model
    assert "hash_salt" not in text
    assert str(aicg_home) not in text
    assert any(check["id"] == "bundle.no_raw_leak" for check in model["doctorSelfCheck"]["checks"])
    assert include_config.returncode == 0, include_config.stderr
    config_model = json.loads((aicg_home / "reports" / "bundle-config.json").read_text(encoding="utf-8"))
    assert config_model["includesConfig"] is True
    assert config_model["config"]["status"] == "included"


def test_self_check_rejects_removed_reasoning_price_field(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home), "PATH": ""}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    (aicg_home / "config.toml").write_text(
        """
[prices."codex:model"]
reasoning_output_per_mtok_usd = 0
""",
        encoding="utf-8",
    )

    result = subprocess.run(base + ["doctor", "--self-check", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert result.returncode == 3
    checks = json.loads(result.stdout)["selfCheck"]["checks"]
    assert any(check["id"] == "config.removed_price_fields" and check["status"] == "fail" for check in checks)
