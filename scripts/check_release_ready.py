#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "status": self.status, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AgentOps Guard release readiness")
    parser.add_argument("--target", default="release-prep", choices=["release-prep", "1.0.0"])
    parser.add_argument("--format", default="text", choices=["text", "json"])
    args = parser.parse_args(argv)

    checks = run_checks(target=args.target)
    status = overall_status(checks)
    model = {
        "status": status,
        "target": args.target,
        "checks": [check.as_dict() for check in checks],
    }
    if args.format == "json":
        print(json.dumps(model, indent=2, sort_keys=True))
    else:
        print(f"release readiness: {status}")
        for check in checks:
            print(f"- {check.status}: {check.id}: {check.detail}")

    if status == "fail":
        return 2
    if status == "blocked" and args.target == "1.0.0":
        return 3
    return 0


def run_checks(*, target: str) -> list[Check]:
    sys.path.insert(0, str(ROOT))
    import aicg
    from aicg.db import CURRENT_SCHEMA_VERSION
    from aicg.reporter import DAILY_REPORT_SCHEMA_VERSION
    from aicg.review import REVIEW_RULESET_VERSION

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = str(project["project"]["version"])
    checks = [
        _check(
            "version.package_sync",
            pyproject_version == aicg.__version__,
            f"pyproject={pyproject_version}, package={aicg.__version__}",
        ),
        _check(
            "version.release_marker",
            (aicg.__version__ == "0.9.0") if target == "release-prep" else (aicg.__version__ == "1.0.0"),
            f"current={aicg.__version__}, target={target}",
            block=True,
        ),
        _check(
            "schema.v1_contract",
            CURRENT_SCHEMA_VERSION >= 10,
            f"schema={CURRENT_SCHEMA_VERSION}",
        ),
        _check(
            "ruleset.current",
            REVIEW_RULESET_VERSION == 2,
            f"ruleset={REVIEW_RULESET_VERSION}",
        ),
        _check(
            "report_schema.current",
            DAILY_REPORT_SCHEMA_VERSION >= 4,
            f"daily_report_schema={DAILY_REPORT_SCHEMA_VERSION}",
        ),
        _cli_version_check(aicg.__version__, CURRENT_SCHEMA_VERSION, REVIEW_RULESET_VERSION),
        _schema_packaging_check(project),
        _required_docs_check(),
        _adr_check(),
        _accuracy_gate_check(),
        _beta_gate_check(),
    ]
    return checks


def overall_status(checks: list[Check]) -> str:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "block" for check in checks):
        return "blocked"
    return "pass"


def _check(check_id: str, passed: bool, detail: str, *, block: bool = False) -> Check:
    if passed:
        return Check(check_id, "pass", detail)
    return Check(check_id, "block" if block else "fail", detail)


def _cli_version_check(version: str, schema_version: int, ruleset_version: int) -> Check:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "aicg", "--version"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    expected = (version, f"schema {schema_version}", f"ruleset {ruleset_version}")
    passed = result.returncode == 0 and all(item in result.stdout for item in expected)
    detail = result.stdout.strip() or result.stderr.strip() or f"returncode={result.returncode}"
    return _check("version.cli_surface", passed, detail)


def _schema_packaging_check(project: dict[str, Any]) -> Check:
    expected = {
        "schemas/daily-report.schema.json",
        "schemas/dashboard-model.schema.json",
        "schemas/review-output.schema.json",
        "schemas/export-output.schema.json",
        "schemas/provider-capability.schema.json",
    }
    setuptools_config = project.get("tool", {}).get("setuptools", {})
    package_data = setuptools_config.get("package-data", {})
    data_files = setuptools_config.get("data-files", {})
    actual = set(package_data.get("aicg", []))
    uses_data_files = bool(data_files)
    passed = actual == expected and not uses_data_files and all((ROOT / "aicg" / path).is_file() for path in expected)
    detail = f"package_data={sorted(actual)}, data_files={sorted(data_files)}"
    return _check("packaging.schema_files", passed, detail)


def _required_docs_check() -> Check:
    required = [
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/accuracy-audit.md",
        "docs/accuracy-report-1.0.md",
        "docs/experimental-decisions.md",
        "docs/release-checklist-1.0.md",
        "docs/release-notes-1.0.md",
        "docs/stability.md",
        "docs/migration-guide.md",
        "docs/threat-model.md",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    return _check("docs.required_release_set", not missing, "missing=" + ",".join(missing) if missing else "all present")


def _adr_check() -> Check:
    required = [
        "docs/adr/0001-canonical-token-semantics.md",
        "docs/adr/0002-rebuild-migration-model.md",
        "docs/adr/0003-local-only-no-telemetry.md",
        "docs/adr/0004-zero-runtime-dependencies.md",
        "docs/adr/0005-html-svg-rendering-safety.md",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    return _check("docs.adr_initial_set", not missing, "missing=" + ",".join(missing) if missing else "all present")


def _accuracy_gate_check() -> Check:
    path = ROOT / "docs/accuracy-report-1.0.md"
    if not path.exists():
        return Check("gate.accuracy_audit", "fail", "docs/accuracy-report-1.0.md missing")
    text = path.read_text(encoding="utf-8").lower()
    pending = "status: pending" in text or "status：pending" in text or "状态：pending" in text
    passed = "status: passed" in text or "status：passed" in text or "状态：passed" in text
    if pending or not passed:
        return Check("gate.accuracy_audit", "block", "30-day accuracy audit is not marked passed")
    return Check("gate.accuracy_audit", "pass", "accuracy report marked passed")


def _beta_gate_check() -> Check:
    path = ROOT / "docs/release-checklist-1.0.md"
    if not path.exists():
        return Check("gate.beta_bugbar", "fail", "docs/release-checklist-1.0.md missing")
    text = path.read_text(encoding="utf-8").lower()
    if "beta gate: pending" in text or "bug bar: pending" in text:
        return Check("gate.beta_bugbar", "block", "external beta or P0/P1 bug bar is still pending")
    return Check("gate.beta_bugbar", "pass", "external beta and bug bar are marked complete")


if __name__ == "__main__":
    raise SystemExit(main())
