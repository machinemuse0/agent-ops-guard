from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from .config import app_paths, load_config
from .db import init_db
from .readers import default_registry
from .self_check import run_self_check
from .util import sha256_text, utc_now_iso


RECENT_ERROR_MAX_BYTES_PER_FILE = 1024 * 1024


def run_doctor(
    app_dir: Path | None = None,
    *,
    json_output: bool = False,
    online: bool = False,
    deep: bool = False,
    max_files: int = 50,
    self_check: bool = False,
) -> str:
    report = collect_doctor_report(
        app_dir,
        online=online,
        deep=deep,
        max_files=max_files,
        self_check=self_check,
    )
    if json_output:
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    return format_doctor_markdown(report)


def collect_doctor_report(
    app_dir: Path | None = None,
    *,
    online: bool = False,
    deep: bool = False,
    max_files: int = 50,
    self_check: bool = False,
) -> dict[str, Any]:
    bounded_max_files = max(1, int(max_files))
    paths = app_paths(app_dir)
    db_error = None
    if paths["db"].exists():
        try:
            init_db(paths["db"])
        except Exception as exc:
            db_error = {"type": type(exc).__name__, "message": str(exc)}
    config_error = None
    try:
        config = load_config(app_dir)
    except Exception as exc:
        config_error = {"type": type(exc).__name__, "messageHash": sha256_text(str(exc))}
        config = {"thresholds": {"large_history_file_bytes": 104857600}, "prices": {}}
    threshold = int(config["thresholds"].get("large_history_file_bytes", 104857600))
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    claude_root = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
    codex_errors, codex_error_scan = _recent_jsonl_errors(
        [codex_home / "sessions", codex_home / "archived_sessions"],
        provider="codex",
        max_files=bounded_max_files,
    )
    claude_errors, claude_error_scan = _recent_jsonl_errors(
        [claude_root / "projects"],
        provider="claude",
        max_files=bounded_max_files,
    )
    provider_rows, provider_load_errors = _provider_info(paths)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "generatedAt": utc_now_iso(),
        "mode": {
            "offline": not online,
            "online": online,
            "deep": deep,
        },
        "limits": {
            "maxFiles": bounded_max_files,
            "recentErrorMaxBytesPerFile": RECENT_ERROR_MAX_BYTES_PER_FILE,
        },
        "python": _python_info(),
        "aicg": _aicg_paths(paths),
        "providers": provider_rows,
        "providerLoadErrors": provider_load_errors,
        "configError": config_error,
        "dbError": db_error,
        "codex": _codex_info(codex_home, online=online, max_files=bounded_max_files),
        "claude": _claude_info(claude_root, max_files=bounded_max_files),
        "largeFiles": _large_files(
            [paths["app"], claude_root, codex_home],
            threshold,
            deep=deep,
            max_files=bounded_max_files,
        ),
        "recentErrors": {
            "codex": codex_errors,
            "claude": claude_errors,
        },
        "recentErrorScan": {
            "codex": codex_error_scan,
            "claude": claude_error_scan,
        },
    }
    if self_check:
        report["selfCheck"] = run_self_check(app_dir)
    report["recommendations"] = _recommendations(report)
    return report


def format_doctor_markdown(report: dict[str, Any]) -> str:
    mode = report.get("mode", {"offline": True, "deep": False})
    limits = report.get("limits", {"maxFiles": 50})
    lines = [
        "# AgentOps Guard Doctor",
        "",
        f"Generated: {report['generatedAt']}",
        f"Mode: offline={str(mode['offline']).lower()}, deep={str(mode['deep']).lower()}, maxFiles={limits['maxFiles']}",
        "",
        "## Runtime",
        f"- Python: {report['python']['version']} ({report['python']['status']})",
        "",
        "## Local state",
    ]
    for item in report["aicg"]["paths"]:
        lines.append(f"- {item['name']}: {item['path']} ({item['status']})")
    lines.extend(["", "## Providers"])
    for row in report.get("providers", []):
        lines.append(
            f"- {row['provider']}: origin={row.get('origin')}, verified={str(row.get('verified')).lower()}, version={row.get('version') or 'unknown'}"
        )
    for error in report.get("providerLoadErrors", []):
        lines.append(
            f"- load error: {error.get('entryPoint') or error.get('module')} ({error.get('errorType')}: {error.get('message')})"
        )

    codex = report["codex"]
    lines.extend(
        [
            "",
            "## Codex",
            f"- Binary: {codex['binary'] or 'missing'}",
            f"- Version: {codex['version'] or 'unknown'}",
            f"- CODEX_HOME: {codex['home']}",
            f"- Config: {codex['config']['path']} ({codex['config']['status']})",
            f"- Default model: {codex['config'].get('model') or 'unknown'}",
            f"- Profiles: {', '.join(codex['config'].get('profiles', [])) or 'none'}",
            f"- MCP servers: {codex['config'].get('mcpServerCount', 0)} configured, {codex['config'].get('remoteMcpServerCount', 0)} remote",
            f"- Models cache: {codex['modelsCache']['path']} ({codex['modelsCache']['status']})",
            f"- Model options: {codex['modelsCache'].get('modelCount', 0)} cached",
            f"- Session files: {codex['state']['activeSessionFiles']} active, {codex['state']['archivedSessionFiles']} archived",
            f"- `codex doctor --json`: {codex['doctor']['status']} (exit_code={codex['doctor']['exitCode']})",
        ]
    )
    if codex["modelsCache"].get("modelsSample"):
        lines.append(f"- Model sample: {', '.join(codex['modelsCache']['modelsSample'][:10])}")
    if codex["doctor"]["checks"]:
        lines.append("")
        lines.append("### Codex warnings and failures")
        for check in codex["doctor"]["checks"]:
            lines.append(
                f"- `{check['id']}` [{check['status']}]: {check['summary']}"
            )

    claude = report["claude"]
    lines.extend(
        [
            "",
            "## Claude Code",
            f"- Binary: {claude['binary'] or 'missing'}",
            f"- Version: {claude['version'] or 'unknown'}",
            f"- Config dir: {claude['root']}",
            f"- Projects: {claude['projectFiles']} transcript file(s)",
            f"- Cache size: {claude['cacheBytes']} bytes",
        ]
    )

    lines.extend(["", "## Recent error signals"])
    for provider in ("codex", "claude"):
        errors = report["recentErrors"][provider]
        scan = report.get("recentErrorScan", {}).get(provider, {})
        cap_suffix = ", scanCapReached=true" if scan.get("scanCapReached") else ""
        lines.append(
            f"- {provider}: {len(errors)} recent error-like event(s), filesChecked={scan.get('filesChecked', 0)}{cap_suffix}"
        )
        for error in errors[:5]:
            lines.append(
                f"  - {error['kind']} at {error['timestamp'] or 'unknown'} in {error['sourceFile']}"
            )

    lines.extend(["", f"## Large files > {report['largeFiles']['thresholdBytes']} bytes"])
    if report["largeFiles"].get("scanCapReached"):
        lines.append(f"- scanCapReached=true after {report['largeFiles'].get('filesScanned', 0)} file(s)")
    large_items = report["largeFiles"]["items"]
    if large_items:
        for item in large_items[:20]:
            lines.append(f"- {item['path']} ({item['bytes']} bytes)")
        if report["largeFiles"]["truncated"]:
            lines.append("- ... more files omitted")
    else:
        lines.append("- none found")

    if "selfCheck" in report:
        self_check = report["selfCheck"]
        lines.extend(["", "## Self-check"])
        lines.append(f"- Status: {self_check['status']}")
        for check in self_check["checks"]:
            lines.append(f"- `{check['id']}` [{check['status']}]: {check['summary']}")

    lines.extend(["", "## Repair suggestions"])
    if report["recommendations"]:
        for recommendation in report["recommendations"]:
            lines.append(f"- {recommendation}")
    else:
        lines.append("- No immediate repair suggestion.")
    lines.append("")
    return "\n".join(lines)


def _python_info() -> dict[str, str]:
    version = sys.version_info
    ok = version >= (3, 11)
    return {
        "version": f"{version.major}.{version.minor}.{version.micro}",
        "status": "ok" if ok else "requires 3.11+",
    }


def _aicg_paths(paths: dict[str, Path]) -> dict[str, Any]:
    items = []
    for key in ("app", "raw_codex", "raw_claude", "reports", "config", "db"):
        path = paths[key]
        items.append(
            {
                "name": key,
                "path": str(path),
                "status": "exists" if path.exists() else "missing",
            }
        )
    return {"paths": items}


def _provider_info(paths: dict[str, Path]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    verified = _load_provider_verifications(paths)
    try:
        registry = default_registry(verified_plugins=verified)
    except Exception as exc:
        return [], [
            {
                "entryPoint": "aicg.providers",
                "module": "unknown",
                "errorType": type(exc).__name__,
                "message": str(exc),
            }
        ]
    return registry.rows(), registry.load_errors()


def _load_provider_verifications(paths: dict[str, Path]) -> dict[str, object]:
    path = paths["app"] / "provider-verifications.json"
    if not path.exists():
        return {"formatVersion": 2, "records": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"formatVersion": 2, "records": []}
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"formatVersion": 1, "verified": [str(item) for item in data if item]}
    return {"formatVersion": 2, "records": []}


def _codex_info(codex_home: Path, *, online: bool, max_files: int) -> dict[str, Any]:
    binary = shutil.which("codex")
    version = _command_output([binary, "--version"], timeout=10) if binary else None
    config_path = codex_home / "config.toml"
    models_cache = codex_home / "models_cache.json"
    active_sessions, active_cap = _count_jsonl(codex_home / "sessions", max_files=max_files)
    archived_sessions, archived_cap = _count_jsonl(codex_home / "archived_sessions", max_files=max_files)
    return {
        "home": str(codex_home),
        "binary": binary,
        "version": _clean_version(version),
        "config": _codex_config_summary(config_path),
        "modelsCache": _json_file_summary(models_cache),
        "versionCache": _json_file_summary(codex_home / "version.json"),
        "state": {
            "stateDb": _file_summary(codex_home / "state_5.sqlite"),
            "logDb": _file_summary(codex_home / "logs_2.sqlite"),
            "activeSessionFiles": active_sessions,
            "activeSessionFilesScanCapReached": active_cap,
            "archivedSessionFiles": archived_sessions,
            "archivedSessionFilesScanCapReached": archived_cap,
        },
        "doctor": _codex_doctor(binary, online=online),
    }


def _claude_info(claude_root: Path, *, max_files: int) -> dict[str, Any]:
    binary = shutil.which("claude")
    version = _command_output([binary, "--version"], timeout=10) if binary else None
    project_files, project_cap = _count_jsonl(claude_root / "projects", max_files=max_files)
    return {
        "root": str(claude_root),
        "binary": binary,
        "version": _clean_version(version),
        "settings": _json_file_summary(claude_root / "settings.json"),
        "projectFiles": project_files,
        "projectFilesScanCapReached": project_cap,
        "cacheBytes": _dir_size(claude_root / "cache", max_files=max_files),
    }


def _codex_config_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path),
        "status": "missing",
        "model": None,
        "profiles": [],
        "mcpServerCount": 0,
        "remoteMcpServerCount": 0,
        "remoteMcpServers": [],
    }
    if not path.exists():
        return summary
    summary["status"] = "exists"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        summary["status"] = f"parse-error: {exc}"
        return summary
    summary["model"] = data.get("model")
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    summary["profiles"] = sorted(profiles.keys())
    servers = data.get("mcp_servers") if isinstance(data.get("mcp_servers"), dict) else {}
    summary["mcpServerCount"] = len(servers)
    remote_servers = []
    for name, value in servers.items():
        if isinstance(value, dict) and value.get("url"):
            remote_servers.append(name)
    summary["remoteMcpServers"] = sorted(remote_servers)
    summary["remoteMcpServerCount"] = len(remote_servers)
    return summary


def _codex_doctor(binary: str | None, *, online: bool) -> dict[str, Any]:
    if not online:
        return {
            "status": "skipped",
            "exitCode": None,
            "overallStatus": "offline",
            "checks": [],
        }
    if not binary:
        return {
            "status": "missing",
            "exitCode": None,
            "overallStatus": "missing",
            "checks": [],
        }
    try:
        result = subprocess.run(
            [binary, "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "exitCode": None, "overallStatus": "timeout", "checks": []}
    except OSError as exc:
        return {
            "status": "error",
            "exitCode": None,
            "overallStatus": "error",
            "error": str(exc),
            "checks": [],
        }
    parsed: dict[str, Any] = {}
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            parsed = {}
    checks = []
    for check_id, check in (parsed.get("checks") or {}).items():
        if not isinstance(check, dict):
            continue
        status = check.get("status")
        if status in {"warning", "fail", "error"}:
            checks.append(
                {
                    "id": check_id,
                    "category": check.get("category"),
                    "status": status,
                    "summary": check.get("summary"),
                    "remediation": check.get("remediation"),
                    "issues": _compact_issues(check.get("issues")),
                }
            )
    return {
        "status": "ok" if result.returncode == 0 else "warning",
        "exitCode": result.returncode,
        "overallStatus": parsed.get("overallStatus") or "unknown",
        "checks": checks,
    }


def _compact_issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compacted = []
    for item in value:
        if isinstance(item, dict):
            compacted.append(
                {
                    "severity": item.get("severity"),
                    "cause": item.get("cause"),
                    "measured": item.get("measured"),
                    "expected": item.get("expected"),
                    "remedy": item.get("remedy"),
                }
            )
    return compacted


def _json_file_summary(path: Path) -> dict[str, Any]:
    summary = _file_summary(path)
    if summary["status"] != "file":
        return summary
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        summary["jsonStatus"] = "unreadable"
        return summary
    if isinstance(data, dict):
        summary["jsonStatus"] = "ok"
        summary["topLevelKeys"] = sorted(str(key) for key in data.keys())[:20]
        model_names = _model_names(data.get("models"))
        if model_names:
            summary["modelCount"] = len(model_names)
            summary["modelsSample"] = model_names[:20]
    elif isinstance(data, list):
        summary["jsonStatus"] = "ok"
        summary["items"] = len(data)
    else:
        summary["jsonStatus"] = type(data).__name__
    return summary


def _model_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, dict):
                name = (
                    item.get("id")
                    or item.get("name")
                    or item.get("model")
                    or item.get("slug")
                    or item.get("display_name")
                )
                if isinstance(name, str):
                    names.append(name)
            elif isinstance(item, str):
                names.append(item)
        return sorted(set(names))
    return []


def _file_summary(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"path": str(path), "status": "missing", "bytes": 0}
        if path.is_file():
            return {"path": str(path), "status": "file", "bytes": path.stat().st_size}
        if path.is_dir():
            return {"path": str(path), "status": "dir", "bytes": _dir_size(path, max_files=50)}
    except OSError as exc:
        return {"path": str(path), "status": f"error: {exc}", "bytes": 0}
    return {"path": str(path), "status": "other", "bytes": 0}


def _large_files(
    roots: list[Path],
    threshold: int,
    *,
    deep: bool,
    max_files: int,
) -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    files_scanned = 0
    scan_cap_reached = False
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        try:
            candidates = root.rglob("*") if deep else _shallow_files(root)
            for path in candidates:
                if files_scanned >= max_files:
                    scan_cap_reached = True
                    break
                path_key = str(path)
                if path_key in seen:
                    continue
                seen.add(path_key)
                try:
                    if path.is_file():
                        files_scanned += 1
                        size = path.stat().st_size
                        if size > threshold:
                            found.append({"path": str(path), "bytes": size})
                except OSError:
                    continue
        except OSError:
            continue
        if scan_cap_reached:
            break
    found = sorted(found, key=lambda item: int(item["bytes"]), reverse=True)
    return {
        "thresholdBytes": threshold,
        "items": found[:20],
        "truncated": len(found) > 20,
        "totalMatches": len(found),
        "filesScanned": files_scanned,
        "scanCapReached": scan_cap_reached,
    }


def _shallow_files(root: Path):
    for path in root.iterdir():
        yield path
        if path.is_dir():
            try:
                yield from path.iterdir()
            except OSError:
                continue


def _recent_jsonl_errors(
    roots: list[Path],
    *,
    provider: str,
    max_files: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    files = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*.jsonl"):
                if path.is_file():
                    files.append(path)
        except OSError:
            continue
    files = sorted(files, key=_safe_mtime, reverse=True)
    scan_cap_reached = len(files) > max_files
    files = files[:max_files]
    errors: list[dict[str, Any]] = []
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                bytes_read = 0
                for line in handle:
                    bytes_read += len(line.encode("utf-8", errors="replace"))
                    if bytes_read > RECENT_ERROR_MAX_BYTES_PER_FILE:
                        break
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        error = _error_signal(event, provider)
                        if error:
                            error["sourceFile"] = str(path)
                            errors.append(error)
                            if len(errors) >= 20:
                                return errors, {
                                    "filesChecked": len(files),
                                    "scanCapReached": scan_cap_reached,
                                    "maxBytesPerFile": RECENT_ERROR_MAX_BYTES_PER_FILE,
                                }
        except OSError:
            continue
    return errors, {
        "filesChecked": len(files),
        "scanCapReached": scan_cap_reached,
        "maxBytesPerFile": RECENT_ERROR_MAX_BYTES_PER_FILE,
    }


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _error_signal(event: dict[str, Any], provider: str) -> dict[str, Any] | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    timestamp = payload.get("timestamp") or event.get("timestamp")
    text = " ".join(
        str(value)
        for key, value in payload.items()
        if key in {"type", "status", "reason", "message", "error", "summary"}
    )
    lowered = text.lower()
    if not any(term in lowered for term in ("error", "failed", "fail", "resume", "restore")):
        return None
    kind = "thread_restore_or_resume_error" if "resume" in lowered or "restore" in lowered else "error"
    return {
        "provider": provider,
        "kind": kind,
        "timestamp": timestamp if isinstance(timestamp, str) else None,
        "messageHash": sha256_text(text),
    }


def _recommendations(report: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    missing_aicg = [
        item["name"] for item in report["aicg"]["paths"] if item["status"] == "missing"
    ]
    if missing_aicg:
        recommendations.append(
            "Run `python -m aicg init` to create the local app directories and SQLite DB."
        )
    if report.get("configError"):
        recommendations.append("Fix `~/.aicg/config.toml` parsing before relying on thresholds or pricing.")
    codex = report["codex"]
    if not codex["binary"]:
        recommendations.append("Install or expose `codex` on PATH before running Codex diagnostics.")
    for check in codex["doctor"]["checks"]:
        check_id = check["id"]
        if check_id == "network.provider_reachability":
            recommendations.append("Check proxy, VPN, firewall, DNS, and custom CA settings for Codex provider reachability.")
        elif check_id == "network.websocket_reachability":
            recommendations.append("Check whether the proxy or network policy supports ChatGPT WebSocket traffic.")
        elif check_id == "state.rollout_db_parity":
            recommendations.append("Back up Codex state, then rebuild or refresh thread indexes if restore/resume errors persist.")
        elif check_id == "terminal.env":
            recommendations.append("Use a real terminal type such as `TERM=xterm-256color` when running interactive Codex.")
        elif check_id == "updates.status":
            recommendations.append("Refresh Codex update metadata when network access is available.")
        elif check.get("remediation"):
            recommendations.append(str(check["remediation"]))
    if report["largeFiles"]["totalMatches"] > 0:
        recommendations.append("Review large history/cache files before sharing logs or committing artifacts.")
    if report["largeFiles"].get("scanCapReached"):
        recommendations.append("Run `python -m aicg doctor --deep --max-files <N>` when you need a fuller bounded cache/history scan.")
    for scan in report.get("recentErrorScan", {}).values():
        if scan.get("scanCapReached"):
            recommendations.append("Increase `doctor --max-files` if recent error scanning reached its configured cap.")
    if report["recentErrors"]["codex"]:
        recommendations.append("Inspect recent Codex error-like rollout events before resuming affected threads.")
    if report["recentErrors"]["claude"]:
        recommendations.append("Inspect recent Claude transcript error-like events before continuing those sessions.")
    if report.get("selfCheck", {}).get("status") == "fail":
        recommendations.append("Inspect `doctor --self-check --json` failures before publishing or migrating the local DB.")
    return _dedupe(recommendations)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _command_output(command: list[str | None], *, timeout: int) -> str | None:
    if not command[0]:
        return None
    try:
        result = subprocess.run(
            [str(part) for part in command if part is not None],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (result.stdout or result.stderr).strip() or None


def _clean_version(value: str | None) -> str | None:
    if not value:
        return None
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[-1] if lines else None


def _count_jsonl(root: Path, *, max_files: int) -> tuple[int, bool]:
    if not root.exists():
        return 0, False
    count = 0
    try:
        for path in root.rglob("*.jsonl"):
            if path.is_file():
                count += 1
                if count >= max_files:
                    return count, True
    except OSError:
        return count, False
    return count, False


def _dir_size(root: Path, *, max_files: int | None = None) -> int:
    if not root.exists():
        return 0
    total = 0
    files_scanned = 0
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    if max_files is not None and files_scanned >= max_files:
                        break
                    files_scanned += 1
                    total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total
