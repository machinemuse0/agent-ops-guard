import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_release_readiness_reports_blocked_until_external_gates_complete():
    result = subprocess.run(
        [sys.executable, "scripts/check_release_ready.py", "--format", "json"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    assert model["status"] == "blocked"
    checks = {check["id"]: check for check in model["checks"]}
    assert checks["schema.v1_contract"]["status"] == "pass"
    assert checks["gate.accuracy_audit"]["status"] == "block"
    assert checks["gate.beta_bugbar"]["status"] == "block"


def test_release_readiness_blocks_1_0_target_when_audit_is_pending():
    result = subprocess.run(
        [sys.executable, "scripts/check_release_ready.py", "--target", "1.0.0", "--format", "json"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 3
    model = json.loads(result.stdout)
    assert model["status"] == "blocked"
    checks = {check["id"]: check for check in model["checks"]}
    assert checks["version.release_marker"]["status"] == "block"
    assert checks["gate.accuracy_audit"]["status"] == "block"


def test_v1_migration_rehearsal_preserves_user_state_for_v05_like_db(tmp_path):
    home = tmp_path / "aicg-v05"
    codex_home = tmp_path / "codex-empty"
    (codex_home / "sessions").mkdir(parents=True)
    env = {**os.environ, "AICG_HOME": str(home), "CODEX_HOME": str(codex_home), "PATH": ""}
    base = [sys.executable, "-m", "aicg"]

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    _seed_user_state(home / "aicg.sqlite")
    _set_schema_version(home / "aicg.sqlite", "5")

    rebuild = subprocess.run(base + ["rebuild", "--since", "all"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    doctor = subprocess.run(base + ["doctor", "--self-check", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert rebuild.returncode == 0, rebuild.stderr
    assert doctor.returncode == 0, doctor.stderr
    assert json.loads(doctor.stdout)["selfCheck"]["status"] == "pass"
    _assert_user_state(home / "aicg.sqlite")


def test_v1_migration_rehearsal_preserves_user_state_for_v09_db(tmp_path):
    home = tmp_path / "aicg-v09"
    codex_home = tmp_path / "codex-empty"
    (codex_home / "sessions").mkdir(parents=True)
    env = {**os.environ, "AICG_HOME": str(home), "CODEX_HOME": str(codex_home), "PATH": ""}
    base = [sys.executable, "-m", "aicg"]

    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    _seed_user_state(home / "aicg.sqlite")

    rebuild = subprocess.run(base + ["rebuild", "--since", "all"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    support = subprocess.run(base + ["support", "bundle", "--out", "bundle.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert rebuild.returncode == 0, rebuild.stderr
    assert support.returncode == 0, support.stderr
    bundle_text = (home / "reports" / "bundle.json").read_text(encoding="utf-8")
    assert "hash_salt" not in bundle_text
    assert str(home) not in bundle_text
    _assert_user_state(home / "aicg.sqlite")


def _seed_user_state(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO policy_acks (finding_id, reason, acked_at)
            VALUES ('finding-one', 'sha256:reason', '2026-07-08T00:00:00Z');
            INSERT INTO git_links (repo_path, project_path, linked_at)
            VALUES ('/REDACTED/repo', '/REDACTED/project', '2026-07-08T00:00:00Z');
            INSERT INTO report_snapshots (
                period_type, period_start, report_json, report_hash,
                dashboard_model_json, created_at
            ) VALUES (
                'day', '2026-07-08T00:00:00Z', '{}', 'hash', '{}',
                '2026-07-08T00:00:00Z'
            );
            INSERT INTO alert_events (
                id, period_type, period_start, alert_key, threshold_value,
                actual_value, report_hash, config_hash, created_at
            ) VALUES (
                'alert-one', 'day', '2026-07-08T00:00:00Z',
                'interrupted_sessions_max', 0, 1, 'hash', 'config',
                '2026-07-08T00:00:00Z'
            );
            """
        )


def _set_schema_version(db_path: Path, version: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (version,))


def _assert_user_state(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM policy_acks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM git_links").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM report_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 1
        assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "10"
