from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .analyzer import analyze_sessions
from .config import app_paths, ensure_app_dirs, load_config
from .db import (
    connect,
    import_records,
    init_db,
    insert_run,
    replace_issues_for_sessions,
    upsert_scan_error,
)
from .doctor import run_doctor
from .pricing import apply_pricing
from .readers import ClaudeJsonlReader, CodexJsonlReader
from .reporter import generate_markdown_report
from .util import (
    file_mtime_after,
    now_utc,
    parse_since,
    redact_secrets,
    stable_id,
    utc_now_iso,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m aicg")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create local app dirs and DB")
    init_parser.set_defaults(func=cmd_init)

    capture_parser = subparsers.add_parser("capture", help="Capture an agent command")
    capture_parser.add_argument("provider", choices=["codex"])
    capture_parser.add_argument("command_args", nargs=argparse.REMAINDER)
    capture_parser.set_defaults(func=cmd_capture)

    scan_parser = subparsers.add_parser("scan", help="Scan captured JSONL files")
    scan_parser.add_argument("--since", default="24h")
    scan_parser.set_defaults(func=cmd_scan)

    summary_parser = subparsers.add_parser("summary", help="Write a Markdown summary")
    summary_parser.add_argument("--since", default="24h")
    summary_parser.add_argument("--out", default="~/.aicg/reports/daily.md")
    summary_parser.set_defaults(func=cmd_summary)

    doctor_parser = subparsers.add_parser("doctor", help="Run local diagnostics")
    doctor_parser.add_argument("--json", action="store_true", help="Write structured JSON")
    doctor_parser.add_argument("--out", help="Write diagnostics to a file")
    doctor_parser.add_argument("--online", action="store_true", help="Run opt-in Codex online/reachability diagnostics")
    doctor_parser.add_argument("--deep", action="store_true", help="Recursively scan large diagnostic directories")
    doctor_parser.add_argument("--max-files", type=int, default=50, help="Maximum files to inspect for bounded scans")
    doctor_parser.set_defaults(func=cmd_doctor)
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    paths = app_paths()
    db_existed = paths["db"].exists()
    statuses = ensure_app_dirs()
    init_db(paths["db"])
    statuses[str(paths["db"])] = "existing" if db_existed else "created"
    print("AI Coding Cost Guard initialized")
    for path, status in sorted(statuses.items()):
        print(f"- {status}: {path}")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    command = list(args.command_args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("error: capture requires a command after --", file=sys.stderr)
        return 2

    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    stamp = now_utc().strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = paths["raw_codex"] / f"{stamp}.jsonl"
    started_at = utc_now_iso()
    exit_code = 127

    with raw_path.open("wb") as raw_file:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=None,
            )
        except OSError as exc:
            print(f"warning: failed to start command: {exc}", file=sys.stderr)
        else:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                raw_file.write(chunk)
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            exit_code = process.wait()

    ended_at = utc_now_iso()
    sanitized_command = _sanitize_command(command)
    run_id = stable_id("run", started_at, sanitized_command, raw_path)
    with connect(paths["db"]) as conn:
        insert_run(
            conn,
            run_id=run_id,
            command=sanitized_command,
            provider=args.provider,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=exit_code,
            raw_path=str(raw_path),
        )
        conn.commit()
    return exit_code


def cmd_scan(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    cutoff = parse_since(args.since)
    config = load_config()
    codex_reader = CodexJsonlReader()
    claude_reader = ClaudeJsonlReader()
    files_scanned = 0
    files_failed = 0
    malformed_lines = 0
    totals = {"sessions": 0, "turns": 0, "tool_events": 0}
    affected_session_ids: list[str] = []

    codex_files = _codex_jsonl_files_since(paths, cutoff)
    claude_files = _jsonl_files_since(_claude_projects_dir(), cutoff)

    with connect(paths["db"]) as conn:
        for path in codex_files:
            records = _read_provider_file(conn, "codex", codex_reader, path)
            if records is None:
                files_failed += 1
                continue
            files_scanned += 1
            malformed_lines += records.malformed_line_count
            counts = import_records(conn, records)
            for key in totals:
                totals[key] += counts[key]
            affected_session_ids.extend(session.id for session in records.sessions)

        for path in claude_files:
            records = _read_provider_file(conn, "claude", claude_reader, path)
            if records is None:
                files_failed += 1
                continue
            files_scanned += 1
            malformed_lines += records.malformed_line_count
            counts = import_records(conn, records)
            for key in totals:
                totals[key] += counts[key]
            affected_session_ids.extend(session.id for session in records.sessions)

        unique_session_ids = sorted(set(affected_session_ids))
        apply_pricing(conn, unique_session_ids, config)
        issues = analyze_sessions(conn, unique_session_ids, config)
        replace_issues_for_sessions(conn, unique_session_ids, issues)
        conn.commit()

    print(f"files scanned: {files_scanned}")
    print(f"files failed: {files_failed}")
    print(f"sessions inserted/updated: {totals['sessions']}")
    print(f"turns inserted/updated: {totals['turns']}")
    print(f"tool events inserted/updated: {totals['tool_events']}")
    print(f"malformed lines: {malformed_lines}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = paths["reports"] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(paths["db"]) as conn:
        report = generate_markdown_report(conn, since=args.since, config=load_config())
    out_path.write_text(report, encoding="utf-8")
    print(f"report written: {out_path}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    output = run_doctor(
        json_output=args.json,
        online=args.online,
        deep=args.deep,
        max_files=args.max_files,
    )
    if args.out:
        paths = app_paths()
        out_path = Path(args.out).expanduser()
        if not out_path.is_absolute():
            out_path = paths["reports"] / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"doctor report written: {out_path}")
    else:
        print(output)
    return 0


def _jsonl_files_since(root: Path, cutoff) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*.jsonl"):
        try:
            if path.is_file() and file_mtime_after(path, cutoff):
                files.append(path)
        except OSError:
            continue
    return sorted(files)


def _read_provider_file(conn, provider: str, reader, path: Path):
    try:
        return reader.read(path)
    except Exception as exc:
        upsert_scan_error(
            conn,
            provider=provider,
            source_file=str(path),
            error_type=type(exc).__name__,
            error_message=str(exc) or repr(exc),
        )
        print(
            f"warning: failed to scan {provider} file {path}: {type(exc).__name__}",
            file=sys.stderr,
        )
        return None


def _codex_jsonl_files_since(paths: dict[str, Path], cutoff) -> list[Path]:
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    roots = [
        paths["raw_codex"],
        codex_home / "sessions",
        codex_home / "archived_sessions",
    ]
    unique: dict[str, Path] = {}
    for root in roots:
        for path in _jsonl_files_since(root, cutoff):
            unique[str(path)] = path
    return sorted(unique.values())


def _claude_projects_dir() -> Path:
    if "CLAUDE_CONFIG_DIR" in os.environ:
        return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() / "projects"
    return Path("~/.claude/projects").expanduser()


def _sanitize_command(command: list[str]) -> str:
    sanitized: list[str] = []
    looks_like_codex_exec = len(command) >= 2 and Path(command[0]).name == "codex" and command[1] == "exec"
    for index, arg in enumerate(command):
        safe = redact_secrets(arg)
        if looks_like_codex_exec and index >= 2 and not safe.startswith("-"):
            safe = "[redacted-arg]"
        elif "\n" in safe or len(safe) > 120:
            safe = "[redacted-arg]"
        sanitized.append(safe)
    return shlex.join(sanitized)
