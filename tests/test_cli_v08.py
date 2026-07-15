import json
import os
import shutil
import sqlite3
import subprocess
import sys
import datetime as dt
from io import StringIO
from pathlib import Path

from aicg.cli import _flush_redacted_stdout_limit, _write_redacted_stdout
from aicg.policy import load_policy


FIXTURES = Path(__file__).parent / "fixtures"


def test_hash_salt_differs_across_homes_and_rebuild_is_stable(tmp_path):
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "codex_exec_sample.jsonl", sessions / "sample.jsonl")
    base = [sys.executable, "-m", "aicg"]

    home_one = tmp_path / "aicg-one"
    home_two = tmp_path / "aicg-two"
    for home in (home_one, home_two):
        env = {**os.environ, "AICG_HOME": str(home), "CODEX_HOME": str(codex_home)}
        subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
        subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)

    hash_one = _single_value(home_one / "aicg.sqlite", "SELECT source_file_hash FROM sessions LIMIT 1")
    hash_two = _single_value(home_two / "aicg.sqlite", "SELECT source_file_hash FROM sessions LIMIT 1")
    assert hash_one != hash_two

    env_one = {**os.environ, "AICG_HOME": str(home_one), "CODEX_HOME": str(codex_home)}
    subprocess.run(base + ["rebuild", "--since", "all", "--provider", "codex"], cwd=Path.cwd(), env=env_one, text=True, capture_output=True, check=True)
    assert _single_value(home_one / "aicg.sqlite", "SELECT source_file_hash FROM sessions LIMIT 1") == hash_one
    assert _single_value(home_one / "aicg.sqlite", "SELECT value FROM schema_meta WHERE key = 'hash_salt'")


def test_provider_verify_cache_and_plugin_scan_pipeline(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    _write_dummy_plugin(plugin_root)
    fixture_dir = tmp_path / "plugin-fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "dummy.jsonl").write_text('{"id":"one","tokens":12,"status":"completed"}\n', encoding="utf-8")
    (fixture_dir / "dummy-interrupted.jsonl").write_text('{"id":"two","tokens":1,"status":"interrupted"}\n', encoding="utf-8")
    aicg_home = tmp_path / "aicg"
    raw_dummy = aicg_home / "raw" / "dummy"
    raw_dummy.mkdir(parents=True)
    shutil.copyfile(fixture_dir / "dummy.jsonl", raw_dummy / "dummy.jsonl")
    env = {
        **os.environ,
        "AICG_HOME": str(aicg_home),
        "PYTHONPATH": str(plugin_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    base = [sys.executable, "-m", "aicg"]

    before = subprocess.run(base + ["providers"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    unverified = subprocess.run(base + ["providers", "--allow-unverified"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    verify = subprocess.run(
        base + ["providers", "verify", "dummy_plugin", "--fixtures", str(fixture_dir), "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    after = subprocess.run(base + ["providers"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    scan = subprocess.run(base + ["scan", "--since", "9999d", "--provider", "dummy"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    summary = subprocess.run(base + ["summary", "--since", "9999d", "--format", "json", "--out", "daily.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    dashboard = subprocess.run(base + ["dashboard", "--period", "day", "--format", "json", "--out", "dashboard.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert before.returncode == 0
    assert "dummy" not in {row["provider"] for row in json.loads(before.stdout)}
    assert any(row["provider"] == "dummy" and row["verified"] is False for row in json.loads(unverified.stdout))
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["passed"] is True
    assert any(row["provider"] == "dummy" and row["verified"] is True for row in json.loads(after.stdout))
    assert scan.returncode == 0, scan.stderr
    assert summary.returncode == 0, summary.stderr
    assert dashboard.returncode == 0, dashboard.stderr
    report = json.loads((aicg_home / "reports" / "daily.json").read_text(encoding="utf-8"))
    assert report["providerModelBreakdown"][0]["provider"] == "dummy"


def test_provider_verification_is_invalidated_when_module_changes(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    _write_dummy_plugin(plugin_root)
    fixture_dir = tmp_path / "plugin-fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "dummy.jsonl").write_text('{"id":"one","tokens":12,"status":"completed"}\n', encoding="utf-8")
    (fixture_dir / "dummy-interrupted.jsonl").write_text('{"id":"two","tokens":1,"status":"interrupted"}\n', encoding="utf-8")
    env = {
        **os.environ,
        "AICG_HOME": str(tmp_path / "aicg"),
        "PYTHONPATH": str(plugin_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    base = [sys.executable, "-m", "aicg"]

    verify = subprocess.run(
        base + ["providers", "verify", "dummy_plugin", "--fixtures", str(fixture_dir), "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert any(row["provider"] == "dummy" and row["verified"] is True for row in json.loads(subprocess.run(base + ["providers"], cwd=Path.cwd(), env=env, text=True, capture_output=True).stdout))

    (plugin_root / "dummy_plugin.py").write_text((plugin_root / "dummy_plugin.py").read_text(encoding="utf-8") + "\n# changed after verification\n", encoding="utf-8")
    after_change = subprocess.run(base + ["providers"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert after_change.returncode == 0
    assert "dummy" not in {row["provider"] for row in json.loads(after_change.stdout)}


def test_empty_provider_fixture_fails_conformance(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    _write_empty_plugin(plugin_root)
    fixture_dir = tmp_path / "plugin-fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "empty.jsonl").write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "AICG_HOME": str(tmp_path / "aicg"),
        "PYTHONPATH": str(plugin_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }

    result = subprocess.run(
        [sys.executable, "-m", "aicg", "providers", "verify", "empty_plugin", "--fixtures", str(fixture_dir), "--format", "json"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    model = json.loads(result.stdout)
    assert model["passed"] is False
    assert any(check["id"].endswith(".non_empty") and check["status"] == "fail" for check in model["checks"])


def test_doctor_does_not_import_unverified_provider(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    marker_import = tmp_path / "imported"
    marker_factory = tmp_path / "factory"
    _write_side_effect_plugin(plugin_root, marker_import, marker_factory)
    env = {
        **os.environ,
        "AICG_HOME": str(tmp_path / "aicg"),
        "PYTHONPATH": str(plugin_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)

    result = subprocess.run(base + ["doctor", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert result.returncode == 0
    providers = {row["provider"] for row in json.loads(result.stdout)["providers"]}
    assert "evil" not in providers
    assert not marker_import.exists()
    assert not marker_factory.exists()


def test_verified_provider_load_failure_is_reported(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    _write_broken_plugin(plugin_root)
    aicg_home = tmp_path / "aicg"
    aicg_home.mkdir()
    (aicg_home / "provider-verifications.json").write_text(
        json.dumps({"formatVersion": 1, "verified": ["broken_plugin"]}) + "\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AICG_HOME": str(aicg_home),
        "PYTHONPATH": str(plugin_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }

    result = subprocess.run([sys.executable, "-m", "aicg", "providers"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert result.returncode == 0
    assert "provider plugin failed to load" in result.stderr
    assert "broken_plugin" in result.stderr


def test_policy_rules_hash_uses_local_salt(tmp_path):
    base = [sys.executable, "-m", "aicg"]
    hashes = []
    for name in ("one", "two"):
        home = tmp_path / name
        env = {**os.environ, "AICG_HOME": str(home)}
        subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
        result = subprocess.run(base + ["policy", "rules", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        hashes.append(json.loads(result.stdout)["policyHash"])

    assert hashes[0] != hashes[1]


def test_doctor_recent_error_hash_uses_local_salt(tmp_path):
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    event = {
        "type": "error",
        "timestamp": "2026-07-07T00:00:00Z",
        "message": "failed to restore thread SECRET_TOKEN=not-stored",
    }
    (sessions / "error.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    base = [sys.executable, "-m", "aicg"]
    hashes = []
    for name in ("one", "two"):
        home = tmp_path / f"aicg-{name}"
        env = {**os.environ, "AICG_HOME": str(home), "CODEX_HOME": str(codex_home)}
        subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
        result = subprocess.run(base + ["doctor", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        hashes.append(json.loads(result.stdout)["recentErrors"]["codex"][0]["messageHash"])

    assert hashes[0] != hashes[1]


def test_doctor_self_check_failure_returns_exit_3(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    (aicg_home / "config.toml").write_text(
        """
[prices."codex:model"]
input_per_mtok_usd = "bad"
""",
        encoding="utf-8",
    )

    result = subprocess.run(base + ["doctor", "--self-check", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert result.returncode == 3
    assert json.loads(result.stdout)["selfCheck"]["status"] == "fail"


def test_summary_alerts_do_not_block_dashboard_schedule_chain(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    config_path = aicg_home / "config.toml"
    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n[alerts]\ninterrupted_sessions_max = 0\n", encoding="utf-8")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, provider, project_path, started_at, status, created_at)
            VALUES ('s-alert', 'codex', '/tmp/aicg-alert', ?, 'interrupted', ?)
            """,
            (now, now),
        )
        conn.commit()

    summary = subprocess.run(base + ["summary", "--period", "day", "--out", "daily.md"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    dashboard = subprocess.run(base + ["dashboard", "--period", "day", "--out", "dashboard.html"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    alerts = subprocess.run(base + ["alerts", "check", "--period", "day"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert summary.returncode == 0, summary.stderr
    assert "alerts: thresholds exceeded" in summary.stdout
    assert dashboard.returncode == 0, dashboard.stderr
    assert alerts.returncode == 3


def test_policy_ack_survives_schema_rebuild(tmp_path):
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "codex_rollout_sample.jsonl", sessions / "rollout.jsonl")
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home), "CODEX_HOME": str(codex_home)}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    subprocess.run(base + ["scan", "--since", "9999d", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    first_check = subprocess.run(base + ["policy", "check", "--since", "9999d", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    finding_ids = [finding["id"] for finding in json.loads(first_check.stdout)["findings"]]
    assert finding_ids
    for finding_id in finding_ids:
        subprocess.run(base + ["policy", "ack", finding_id, "--reason", "approved"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)

    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        conn.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
        conn.commit()
    rebuild = subprocess.run(base + ["rebuild", "--since", "all", "--provider", "codex"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    second_check = subprocess.run(base + ["policy", "check", "--since", "9999d", "--format", "json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert rebuild.returncode == 0, rebuild.stderr
    assert json.loads(second_check.stdout)["findings"] == []


def test_redact_paths_covers_summary_export_and_dashboard(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with sqlite3.connect(aicg_home / "aicg.sqlite") as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, provider, project_path, started_at, status, created_at)
            VALUES ('s-redact', 'codex', '/Users/alice/private/project', ?, 'completed', ?)
            """,
            (now, now),
        )
        conn.commit()

    summary = subprocess.run(base + ["summary", "--since", "9999d", "--redact-paths", "--out", "daily.md"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    export = subprocess.run(base + ["export", "--kind", "sessions", "--format", "json", "--since", "9999d", "--redact-paths", "--out", "sessions.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    dashboard = subprocess.run(base + ["dashboard", "--format", "json", "--redact-paths", "--out", "dashboard.json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)

    assert summary.returncode == 0, summary.stderr
    assert export.returncode == 0, export.stderr
    assert dashboard.returncode == 0, dashboard.stderr
    for path in (aicg_home / "reports" / "daily.md", aicg_home / "reports" / "sessions.json", aicg_home / "reports" / "dashboard.json"):
        text = path.read_text(encoding="utf-8")
        assert "/Users/alice" not in text
        assert "~/private/project" in text


def test_capture_redacts_secret_split_across_stdout_chunks(tmp_path):
    aicg_home = tmp_path / "aicg"
    env = {**os.environ, "AICG_HOME": str(aicg_home)}
    secret = "sk-" + "A" * 40
    script = "import sys; secret='sk-'+'A'*40; sys.stdout.write('x'*8190 + secret + '\\n')"
    base = [sys.executable, "-m", "aicg"]
    subprocess.run(base + ["init"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)

    capture = subprocess.run(
        base + ["capture", "codex", "--store-redacted", "--", sys.executable, "-c", script],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    raw_text = "\n".join(path.read_text(encoding="utf-8") for path in (aicg_home / "raw" / "codex").glob("*.jsonl"))
    assert capture.returncode == 0, capture.stderr
    assert secret in capture.stdout
    assert secret not in raw_text
    assert "[REDACTED]" in raw_text


def test_capture_redacted_stdout_flushes_bounded_no_newline_buffer(tmp_path):
    secret = "sk-" + "A" * 40
    raw_handle = StringIO()
    pending = "x" * 80 + secret + "y" * 80
    policy = load_policy(tmp_path / "aicg")

    pending = _flush_redacted_stdout_limit(
        raw_handle,
        pending,
        policy,
        max_chars=160,
        overlap_chars=128,
    )
    _write_redacted_stdout(raw_handle, pending, policy)

    text = raw_handle.getvalue()
    assert len(pending) == 128
    assert secret not in text
    assert "[REDACTED]" in text
    assert text.count('"stdout"') == 2


def _single_value(db_path: Path, sql: str):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchone()[0]


def _write_dummy_plugin(root: Path) -> None:
    (root / "dummy_plugin.py").write_text(
        '''
import json
from collections import Counter
from pathlib import Path
from aicg.models import NormalizedSession, NormalizedTurn, ParsedRecords
from aicg.readers.base import Capabilities
from aicg.util import hash_file, stable_id, utc_now_iso

class DummyReader:
    provider = "dummy"

    def discover(self, since, paths=None, known_sources=None):
        root = Path(paths["raw"]) / "dummy"
        return sorted(root.glob("*.jsonl")) if root.exists() else []

    def read(self, path):
        source_hash = hash_file(path)
        now = "2026-07-07T00:00:00Z"
        sessions = []
        turns = []
        unknown_event_types = Counter()
        malformed = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if row.get("type") == "aicg.future_event":
                unknown_event_types[str(row.get("type"))] += 1
                continue
            row_id = str(row.get("id") or len(sessions) + 1)
            status = str(row.get("status") or "completed")
            session_id = stable_id("dummy-session", row_id)
            turn_id = stable_id("dummy-turn", row_id)
            sessions.append(NormalizedSession(id=session_id, provider=self.provider, project_path="/tmp/dummy", source_file=str(path), source_file_hash=source_hash, started_at=now, status=status, model="dummy-model", raw_event_count=1, malformed_line_count=malformed, created_at=utc_now_iso()))
            turns.append(NormalizedTurn(id=turn_id, session_id=session_id, provider=self.provider, source_file_hash=source_hash, started_at=now, status=status, model="dummy-model", input_uncached_tokens=int(row.get("tokens") or 0)))
        return ParsedRecords(sessions=sessions, turns=turns, malformed_line_count=malformed, source_file=str(path), source_file_hash=source_hash, unknown_event_types=unknown_event_types)

    def capabilities(self):
        return Capabilities(token_usage=True, cache_semantics="none", tool_events=False, turn_status=True, session_resume=False, background_flag=False, native_cost=False)

def create_provider_reader(policy=None):
    return DummyReader()
''',
        encoding="utf-8",
    )
    dist = root / "dummy_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: dummy-plugin\nVersion: 0.1\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text("[aicg.providers]\ndummy = dummy_plugin:create_provider_reader\n", encoding="utf-8")


def _write_side_effect_plugin(root: Path, marker_import: Path, marker_factory: Path) -> None:
    (root / "evil_plugin.py").write_text(
        f'''
from pathlib import Path
from aicg.models import ParsedRecords
from aicg.readers.base import Capabilities
Path({str(marker_import)!r}).write_text("imported", encoding="utf-8")

class EvilReader:
    provider = "evil"

    def discover(self, since, paths=None, known_sources=None):
        return []

    def read(self, path):
        return ParsedRecords()

    def capabilities(self):
        return Capabilities(token_usage=False, cache_semantics="none", tool_events=False, turn_status=False, session_resume=False, background_flag=False, native_cost=False)

def create_provider_reader(policy=None):
    Path({str(marker_factory)!r}).write_text("factory", encoding="utf-8")
    return EvilReader()
''',
        encoding="utf-8",
    )
    dist = root / "evil_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: evil-plugin\nVersion: 0.1\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text("[aicg.providers]\nevil = evil_plugin:create_provider_reader\n", encoding="utf-8")


def _write_empty_plugin(root: Path) -> None:
    (root / "empty_plugin.py").write_text(
        '''
from aicg.models import ParsedRecords
from aicg.readers.base import Capabilities

class EmptyReader:
    provider = "empty"

    def discover(self, since, paths=None, known_sources=None):
        return []

    def read(self, path):
        return ParsedRecords()

    def capabilities(self):
        return Capabilities(token_usage=False, cache_semantics="none", tool_events=False, turn_status=False, session_resume=False, background_flag=False, native_cost=False)

def create_provider_reader(policy=None):
    return EmptyReader()
''',
        encoding="utf-8",
    )
    dist = root / "empty_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: empty-plugin\nVersion: 0.1\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text("[aicg.providers]\nempty = empty_plugin:create_provider_reader\n", encoding="utf-8")


def _write_broken_plugin(root: Path) -> None:
    (root / "broken_plugin.py").write_text('raise RuntimeError("boom during provider import")\n', encoding="utf-8")
    dist = root / "broken_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: broken-plugin\nVersion: 0.1\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text("[aicg.providers]\nbroken = broken_plugin:create_provider_reader\n", encoding="utf-8")
