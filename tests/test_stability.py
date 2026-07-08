import json
import os
import subprocess
import sys
from pathlib import Path

from aicg.cli import EXPORT_TABLES


def test_cli_help_contains_stable_command_surface():
    result = subprocess.run([sys.executable, "-m", "aicg", "--help"], cwd=Path.cwd(), text=True, capture_output=True)
    assert result.returncode == 0
    for command in (
        "init",
        "providers",
        "scan",
        "rebuild",
        "summary",
        "dashboard",
        "alerts",
        "schedule",
        "review",
        "export",
        "doctor",
    ):
        assert command in result.stdout


def test_export_column_sets_are_stable():
    assert EXPORT_TABLES["sessions"][2] == [
        "id",
        "provider",
        "project_path",
        "source_file_hash",
        "source_line_start",
        "source_line_end",
        "native_session_id",
        "lineage_id",
        "parent_session_id",
        "started_at",
        "ended_at",
        "status",
        "model",
        "task_type",
        "duration_ms",
        "retry_count",
        "background_flag",
        "estimated_cost_usd",
        "credit_estimate",
        "cost_source",
        "wasted_cost_usd",
        "privacy_flags",
        "policy_flags",
        "raw_event_count",
        "malformed_line_count",
        "created_at",
    ]
    assert EXPORT_TABLES["scan-errors"][2] == [
        "id",
        "provider",
        "source_file",
        "error_type",
        "error_message_hash",
        "created_at",
    ]


def test_schema_required_keys_are_stable():
    schemas = {}
    for path in (Path.cwd() / "schemas").glob("*.schema.json"):
        schemas[path.name] = json.loads(path.read_text(encoding="utf-8"))
    assert set(schemas) == {
        "daily-report.schema.json",
        "dashboard-model.schema.json",
        "review-output.schema.json",
        "export-output.schema.json",
        "provider-capability.schema.json",
    }
    assert "overview" in schemas["daily-report.schema.json"]["required"]
    assert "trends" in schemas["dashboard-model.schema.json"]["required"]
    assert "origin" in schemas["provider-capability.schema.json"]["required"]


def test_example_provider_documented_verify_command(tmp_path):
    env = {
        **os.environ,
        "AICG_HOME": str(tmp_path / "aicg"),
        "PYTHONPATH": str(Path.cwd() / "examples" / "provider-plugin") + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicg",
            "providers",
            "verify",
            "aicg_example_provider",
            "--fixtures",
            "examples/provider-plugin/fixtures",
            "--format",
            "json",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"] is True
