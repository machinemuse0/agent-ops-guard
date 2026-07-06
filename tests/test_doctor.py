import os

from aicg.doctor import collect_doctor_report, format_doctor_markdown


def test_doctor_markdown_includes_repair_suggestions():
    report = {
        "generatedAt": "2026-07-05T00:00:00Z",
        "python": {"version": "3.12.8", "status": "ok"},
        "aicg": {
            "paths": [
                {"name": "app", "path": "/tmp/aicg", "status": "missing"},
            ]
        },
        "codex": {
            "binary": "codex",
            "version": "codex-cli 0.142.5",
            "home": "/tmp/codex",
            "config": {
                "path": "/tmp/codex/config.toml",
                "status": "exists",
                "model": "gpt-5.5",
                "profiles": ["fast"],
                "mcpServerCount": 2,
                "remoteMcpServerCount": 1,
            },
            "modelsCache": {"path": "/tmp/codex/models_cache.json", "status": "file"},
            "state": {"activeSessionFiles": 1, "archivedSessionFiles": 0},
            "doctor": {
                "status": "warning",
                "exitCode": 1,
                "checks": [
                    {
                        "id": "network.provider_reachability",
                        "status": "fail",
                        "summary": "provider endpoint unreachable",
                    }
                ],
            },
        },
        "claude": {
            "binary": None,
            "version": None,
            "root": "/tmp/claude",
            "projectFiles": 1,
            "cacheBytes": 0,
        },
        "recentErrors": {"codex": [], "claude": []},
        "largeFiles": {
            "thresholdBytes": 100,
            "items": [],
            "truncated": False,
            "totalMatches": 0,
        },
        "recommendations": [
            "Run `python -m aicg init` to create the local app directories and SQLite DB.",
            "Check proxy, VPN, firewall, DNS, and custom CA settings for Codex provider reachability.",
        ],
    }

    markdown = format_doctor_markdown(report)

    assert "AgentOps Guard Doctor" in markdown
    assert "network.provider_reachability" in markdown
    assert "python -m aicg init" in markdown


def test_doctor_defaults_to_offline_without_codex_doctor_call(tmp_path, monkeypatch):
    marker = tmp_path / "doctor-called"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        f"""#!/bin/sh
if [ "$1" = "doctor" ]; then
  echo called > {marker}
  echo '{{"overallStatus":"fail","checks":{{}}}}'
  exit 1
fi
echo 'codex-cli 9.9.9'
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))

    report = collect_doctor_report(tmp_path / "aicg", online=False)

    assert report["mode"]["offline"] is True
    assert report["codex"]["doctor"]["status"] == "skipped"
    assert not marker.exists()


def test_doctor_online_invokes_codex_doctor_and_parses_checks(tmp_path, monkeypatch):
    marker = tmp_path / "doctor-called"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        f"""#!/bin/sh
if [ "$1" = "doctor" ]; then
  echo called > {marker}
  echo '{{"overallStatus":"fail","checks":{{"network.provider_reachability":{{"status":"fail","category":"network","summary":"provider endpoint unreachable"}}}}}}'
  exit 1
fi
echo 'codex-cli 9.9.9'
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))

    report = collect_doctor_report(tmp_path / "aicg", online=True)

    assert report["mode"]["offline"] is False
    assert marker.exists()
    assert report["codex"]["doctor"]["overallStatus"] == "fail"
    assert report["codex"]["doctor"]["checks"][0]["id"] == "network.provider_reachability"


def test_doctor_deep_max_files_reports_scan_cap(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    for index in range(5):
        (codex_home / f"log-{index}.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    report = collect_doctor_report(tmp_path / "aicg", deep=True, max_files=3)

    assert report["mode"]["deep"] is True
    assert report["limits"]["maxFiles"] == 3
    assert report["largeFiles"]["scanCapReached"] is True
