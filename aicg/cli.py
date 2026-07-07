from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

from .analyzer import analyze_sessions
from .config import app_paths, ensure_app_dirs, load_config
from .db import (
    ack_policy_finding,
    backup_database,
    clear_scan_error,
    connect,
    import_records,
    init_db,
    insert_run,
    replace_issues_for_sessions,
    reset_derived_tables,
    scan_state_for,
    upsert_git_activity,
    upsert_git_link,
    upsert_policy_finding,
    upsert_report_snapshot,
    upsert_scan_error,
    upsert_scan_state,
)
from .doctor import run_doctor
from .models import NormalizedSession, NormalizedTurn, ParsedRecords
from .policy import (
    evaluate_policy_findings,
    load_policy,
    policy_rules_model,
    redact_with_policy,
    should_fail_policy,
    unacked_policy_findings,
)
from .pricing import apply_pricing
from .readers import default_registry
from .reporter import build_daily_report, render_markdown_report
from .util import (
    file_mtime_after,
    hash_file,
    isoformat_utc,
    parse_since,
    redact_secrets,
    sha256_text,
    stable_id,
    utc_now_iso,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        if getattr(args, "debug", False):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        if getattr(args, "debug", False):
            raise
        print(f"error: sqlite: {exc}", file=sys.stderr)
        return 4
    except OSError as exc:
        if getattr(args, "debug", False):
            raise
        print(f"error: runtime: {exc}", file=sys.stderr)
        return 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m aicg")
    parser.add_argument("--debug", action="store_true", help="Show full tracebacks for runtime errors")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create local app dirs and DB")
    init_parser.set_defaults(func=cmd_init)

    providers_parser = subparsers.add_parser("providers", help="List provider readers and capabilities")
    providers_parser.set_defaults(func=cmd_providers)

    capture_parser = subparsers.add_parser("capture", help="Capture an agent command")
    capture_parser.add_argument("provider", choices=["codex"])
    capture_parser.add_argument("--store-redacted", action="store_true", help="Store redacted stdout under raw/")
    capture_parser.add_argument("--no-store", action="store_true", help="Do not store stdout; this is the default")
    capture_parser.add_argument("command_args", nargs=argparse.REMAINDER)
    capture_parser.set_defaults(func=cmd_capture)

    scan_parser = subparsers.add_parser("scan", help="Scan captured JSONL files")
    scan_parser.add_argument("--since", default="24h")
    scan_parser.add_argument("--provider", choices=["codex", "claude", "all"], default="all")
    scan_parser.set_defaults(func=cmd_scan)

    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild derived tables from local source logs")
    rebuild_parser.add_argument("--since", default="all")
    rebuild_parser.add_argument("--provider", choices=["codex", "claude", "all"], default="all")
    rebuild_parser.set_defaults(func=cmd_rebuild)

    import_parser = subparsers.add_parser("import", help="Import external local usage exports")
    import_subparsers = import_parser.add_subparsers(dest="import_kind", required=True)
    usage_parser = import_subparsers.add_parser("usage", help="Import OpenAI-compatible usage JSON/JSONL")
    usage_parser.add_argument("--provider", required=True, choices=["openrouter"])
    usage_parser.add_argument("--file", required=True)
    usage_parser.set_defaults(func=cmd_import_usage)

    summary_parser = subparsers.add_parser("summary", help="Write a Markdown summary")
    summary_parser.add_argument("--since", default="24h")
    summary_parser.add_argument("--period", choices=["day", "week", "month"])
    summary_parser.add_argument("--compare", action="store_true")
    summary_parser.add_argument("--utc", action="store_true")
    summary_parser.add_argument("--format", choices=["md", "json"], default="md")
    summary_parser.add_argument("--out", default="~/.aicg/reports/daily.md")
    summary_parser.set_defaults(func=cmd_summary)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect normalized local data")
    inspect_subparsers = inspect_parser.add_subparsers(dest="inspect_kind", required=True)
    inspect_session_parser = inspect_subparsers.add_parser("session", help="Inspect one session")
    inspect_session_parser.add_argument("session_id")
    inspect_session_parser.add_argument("--format", choices=["md", "json"], default="md")
    inspect_session_parser.set_defaults(func=cmd_inspect_session)

    export_parser = subparsers.add_parser("export", help="Export normalized metadata")
    export_parser.add_argument(
        "--kind",
        required=True,
        choices=["sessions", "turns", "tool-events", "issues", "scan-errors"],
    )
    export_parser.add_argument("--format", required=True, choices=["json", "csv"])
    export_parser.add_argument("--since", default="24h")
    export_parser.add_argument("--out", required=True)
    export_parser.set_defaults(func=cmd_export)

    policy_parser = subparsers.add_parser("policy", help="Check local policy findings")
    policy_subparsers = policy_parser.add_subparsers(dest="policy_command", required=True)
    policy_check = policy_subparsers.add_parser("check", help="Evaluate policy findings")
    policy_check.add_argument("--since", default="24h")
    policy_check.add_argument("--format", choices=["md", "json"], default="md")
    policy_check.add_argument("--fail-on", choices=["violation", "warning"], default="violation")
    policy_check.add_argument("--all", action="store_true", help="Include acknowledged findings")
    policy_check.set_defaults(func=cmd_policy_check)
    policy_rules = policy_subparsers.add_parser("rules", help="Print effective policy rules")
    policy_rules.add_argument("--format", choices=["md", "json"], default="md")
    policy_rules.set_defaults(func=cmd_policy_rules)
    policy_ack = policy_subparsers.add_parser("ack", help="Acknowledge one policy finding")
    policy_ack.add_argument("finding_id")
    policy_ack.add_argument("--reason")
    policy_ack.set_defaults(func=cmd_policy_ack)

    git_parser = subparsers.add_parser("git", help="Read local git activity")
    git_subparsers = git_parser.add_subparsers(dest="git_command", required=True)
    git_link = git_subparsers.add_parser("link", help="Link a local repo to an AICG project path")
    git_link.add_argument("repo_path")
    git_link.add_argument("--project-path")
    git_link.set_defaults(func=cmd_git_link)
    git_sync = git_subparsers.add_parser("sync", help="Sync read-only local git activity")
    git_sync.add_argument("--since", default="7d")
    git_sync.set_defaults(func=cmd_git_sync)

    doctor_parser = subparsers.add_parser("doctor", help="Run local diagnostics")
    doctor_parser.add_argument("--json", action="store_true", help="Write structured JSON")
    doctor_parser.add_argument("--out", help="Write diagnostics to a file")
    doctor_parser.add_argument("--online", action="store_true", help="Run opt-in Codex online/reachability diagnostics")
    doctor_parser.add_argument("--deep", action="store_true", help="Recursively scan large diagnostic directories")
    doctor_parser.add_argument("--max-files", type=int, default=50, help="Maximum files to inspect for bounded scans")
    doctor_parser.add_argument("--self-check", action="store_true", help="Run read-only AICG schema/config/report checks")
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


def cmd_providers(args: argparse.Namespace) -> int:
    rows = default_registry().rows()
    print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    command = list(args.command_args)
    store_redacted = bool(args.store_redacted)
    no_store = bool(args.no_store)
    while command and command[0] in {"--store-redacted", "--no-store"}:
        flag = command.pop(0)
        if flag == "--store-redacted":
            store_redacted = True
        elif flag == "--no-store":
            no_store = True
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("error: capture requires a command after --", file=sys.stderr)
        return 2

    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    started_at = utc_now_iso()
    exit_code = 127
    raw_path: Path | None = None
    raw_handle = None
    if store_redacted and not no_store:
        raw_dir = paths[f"raw_{args.provider}"]
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"capture-{started_at.replace(':', '').replace('Z', 'Z')}.jsonl"
        raw_handle = raw_path.open("w", encoding="utf-8")
        raw_handle.write(
            json.dumps(
                {
                    "aicg_capture": True,
                    "redacted": True,
                    "provider": args.provider,
                    "started_at": started_at,
                },
                sort_keys=True,
            )
            + "\n"
        )

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
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            if raw_handle is not None:
                text = chunk.decode("utf-8", errors="replace")
                raw_handle.write(
                    json.dumps(
                        {"stdout": redact_with_policy(redact_secrets(text), load_policy())},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        exit_code = process.wait()
        if raw_handle is not None:
            raw_handle.write(json.dumps({"ended_at": utc_now_iso(), "exit_code": exit_code}, sort_keys=True) + "\n")
            raw_handle.close()
            raw_handle = None

    ended_at = utc_now_iso()
    sanitized_command = _sanitize_command(command)
    run_id = stable_id("run", started_at, sanitized_command)
    with connect(paths["db"]) as conn:
        insert_run(
            conn,
            run_id=run_id,
            command=sanitized_command,
            provider=args.provider,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=exit_code,
            raw_path=str(raw_path) if raw_path else None,
        )
        conn.commit()
    if raw_handle is not None:
        raw_handle.close()
    return exit_code


def cmd_scan(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    cutoff = _parse_scan_since(args.since)
    config = load_config()
    policy = load_policy()
    registry = default_registry(policy=policy)
    files_scanned = 0
    files_skipped = 0
    files_failed = 0
    malformed_lines = 0
    totals = {"sessions": 0, "turns": 0, "tool_events": 0}
    affected_session_ids: list[str] = []

    with connect(paths["db"]) as conn:
        known_sources = _known_scan_sources(conn)
        for reader in registry.selected(args.provider):
            for path in reader.discover(cutoff, paths, known_sources):
                if _is_aicg_capture_file(path):
                    files_skipped += 1
                    continue
                if not _source_needs_scan(conn, path):
                    files_skipped += 1
                    continue
                records = _read_provider_file(conn, reader.provider, reader, path)
                if records is None:
                    files_failed += 1
                    continue
                files_scanned += 1
                malformed_lines += records.malformed_line_count
                counts = import_records(conn, records)
                _update_scan_state(conn, reader.provider, path, records)
                for key in totals:
                    totals[key] += counts[key]
                affected_session_ids.extend(session.id for session in records.sessions)

        target_session_ids = _session_ids_since(conn, cutoff)
        apply_pricing(conn, target_session_ids, config)
        issues = analyze_sessions(conn, target_session_ids, config)
        replace_issues_for_sessions(conn, target_session_ids, issues)
        _refresh_policy_findings(conn, isoformat_utc(cutoff))
        conn.commit()

    print(f"files scanned: {files_scanned}")
    print(f"files skipped: {files_skipped}")
    print(f"files failed: {files_failed}")
    print(f"sessions inserted/updated: {totals['sessions']}")
    print(f"turns inserted/updated: {totals['turns']}")
    print(f"tool events inserted/updated: {totals['tool_events']}")
    print(f"malformed lines: {malformed_lines}")
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    backup_path = backup_database(paths["db"])
    init_db(paths["db"], allow_schema_upgrade=True)
    warnings = _missing_source_warnings(paths["db"])
    with connect(paths["db"]) as conn:
        reset_derived_tables(conn)
        conn.commit()
    scan_args = argparse.Namespace(since=args.since, provider=args.provider)
    result = cmd_scan(scan_args)
    if backup_path:
        print(f"backup written: {backup_path}")
    for warning in warnings:
        print(f"warning: source file disappeared before rebuild: {warning}", file=sys.stderr)
    return result


def cmd_import_usage(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    source = Path(args.file).expanduser()
    if not source.exists():
        print(f"error: usage file not found: {source}", file=sys.stderr)
        return 1
    records = _usage_import_records(args.provider, source)
    with connect(paths["db"]) as conn:
        counts = import_records(conn, records)
        apply_pricing(conn, [session.id for session in records.sessions], load_config())
        conn.commit()
    print(f"usage sessions imported/updated: {counts['sessions']}")
    print(f"usage turns imported/updated: {counts['turns']}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = paths["reports"] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    period_meta = _period_meta(args.period, utc=args.utc) if args.period else None
    with connect(paths["db"]) as conn:
        previous_snapshot = _compare_snapshot(conn, period_meta) if args.compare and period_meta else None
        report_model = build_daily_report(
            conn,
            since=period_meta["since"] if period_meta else args.since,
            config=load_config(),
            period=period_meta["type"] if period_meta else None,
            period_start=period_meta["start"] if period_meta else None,
            period_end=period_meta["end"] if period_meta else None,
            compare=None,
        )
        if args.compare and period_meta:
            report_model["compare"] = _compare_delta(report_model, previous_snapshot)
        if period_meta:
            snapshot_json = json.dumps(report_model, ensure_ascii=False, sort_keys=True)
            upsert_report_snapshot(
                conn,
                period_type=period_meta["type"],
                period_start=period_meta["start"],
                report_json=snapshot_json,
                report_hash=sha256_text(snapshot_json),
            )
            conn.commit()
    if args.format == "json":
        report = json.dumps(report_model, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        report = render_markdown_report(report_model)
    out_path.write_text(report + ("" if report.endswith("\n") else "\n"), encoding="utf-8")
    print(f"report written: {out_path}")
    return 0


def cmd_inspect_session(args: argparse.Namespace) -> int:
    paths = app_paths()
    init_db(paths["db"])
    with connect(paths["db"]) as conn:
        model = _inspect_session_model(conn, args.session_id)
    if model is None:
        print(f"error: session not found: {args.session_id}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_inspect_session_markdown(model))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    paths = app_paths()
    init_db(paths["db"])
    cutoff_iso = parse_since(args.since).isoformat(timespec="seconds").replace("+00:00", "Z")
    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = paths["reports"] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(paths["db"]) as conn:
        rows = _export_rows(conn, args.kind, cutoff_iso)
    if args.format == "json":
        out_path.write_text(
            json.dumps(
                {"formatVersion": 2, "kind": args.kind, "rows": rows},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        _write_csv(out_path, rows, EXPORT_TABLES[args.kind][2])
    print(f"export written: {out_path}")
    return 0


def cmd_policy_rules(args: argparse.Namespace) -> int:
    ensure_app_dirs()
    model = policy_rules_model(load_policy())
    if args.format == "json":
        print(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("# AgentOps Guard Policy Rules")
        print(f"- Version: {model['version']}")
        print(f"- Policy hash: `{model['policyHash']}`")
        print(f"- Services mode: {model['services']['mode']}")
        print(f"- Services deny: {', '.join(model['services']['deny']) or 'none'}")
        print(f"- Services allow: {', '.join(model['services']['allow']) or 'none'}")
    return 0


def cmd_policy_check(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    cutoff_iso = isoformat_utc(parse_since(args.since))
    with connect(paths["db"]) as conn:
        _refresh_policy_findings(conn, cutoff_iso)
        conn.commit()
        rows = unacked_policy_findings(conn, since_iso=cutoff_iso, include_acked=args.all)
    model = {
        "formatVersion": 1,
        "policyHash": load_policy().policy_hash,
        "findings": [_row_dict(row) for row in rows],
    }
    if args.format == "json":
        print(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("# AgentOps Guard Policy Check")
        print(f"- Policy hash: `{model['policyHash']}`")
        if not rows:
            print("- Findings: none")
        else:
            for row in rows:
                print(f"- `{row['id']}` {row['level']} {row['rule_id']} session={row['session_id']}")
    return 3 if should_fail_policy(rows, args.fail_on) else 0


def cmd_policy_ack(args: argparse.Namespace) -> int:
    paths = app_paths()
    init_db(paths["db"])
    with connect(paths["db"]) as conn:
        exists = conn.execute("SELECT 1 FROM policy_findings WHERE id = ?", (args.finding_id,)).fetchone()
        if exists is None:
            print(f"error: policy finding not found: {args.finding_id}", file=sys.stderr)
            return 1
        ack_policy_finding(conn, args.finding_id, args.reason)
        conn.commit()
    print(f"policy finding acknowledged: {args.finding_id}")
    return 0


def cmd_git_link(args: argparse.Namespace) -> int:
    paths = app_paths()
    ensure_app_dirs()
    init_db(paths["db"])
    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.exists():
        print(f"error: repo path not found: {repo_path}", file=sys.stderr)
        return 1
    project_path = args.project_path or str(repo_path)
    with connect(paths["db"]) as conn:
        upsert_git_link(conn, repo_path=str(repo_path), project_path=project_path)
        conn.commit()
    print(f"git repo linked: {repo_path} -> {project_path}")
    return 0


def cmd_git_sync(args: argparse.Namespace) -> int:
    paths = app_paths()
    init_db(paths["db"])
    cutoff = parse_since(args.since)
    synced = 0
    with connect(paths["db"]) as conn:
        links = conn.execute("SELECT repo_path, project_path FROM git_links ORDER BY repo_path").fetchall()
        for link in links:
            activity = _read_git_activity(Path(link["repo_path"]), cutoff)
            upsert_git_activity(
                conn,
                project_path=link["project_path"],
                period_start=isoformat_utc(cutoff),
                commits=activity["commits"],
                merge_commits=activity["merge_commits"],
                insertions=activity["insertions"],
                deletions=activity["deletions"],
            )
            synced += 1
        conn.commit()
    print(f"git repos synced: {synced}")
    return 0


def _parse_scan_since(value: str):
    if value == "all":
        return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return parse_since(value)


def _known_scan_sources(conn) -> set[str]:
    return {
        row["source_file"]
        for row in conn.execute("SELECT source_file FROM scan_state").fetchall()
    }


def _session_ids_since(conn, cutoff) -> list[str]:
    cutoff_iso = isoformat_utc(cutoff)
    return [
        row["id"]
        for row in conn.execute(
            """
            SELECT id
            FROM sessions
            WHERE COALESCE(started_at, created_at) >= ?
            ORDER BY COALESCE(started_at, created_at)
            """,
            (cutoff_iso,),
        ).fetchall()
    ]


def _refresh_policy_findings(conn, since_iso: str | None) -> None:
    conn.execute(
        "DELETE FROM policy_findings WHERE rule_id LIKE 'services.%' AND (? IS NULL OR last_seen_at >= ?)",
        (since_iso, since_iso),
    )
    for finding in evaluate_policy_findings(conn, load_policy(), since_iso=since_iso):
        upsert_policy_finding(conn, finding)


def _source_needs_scan(conn, path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    state = scan_state_for(conn, str(path))
    if state is None:
        return True
    if int(state["file_size"] or -1) != stat.st_size or float(state["mtime"] or -1) != stat.st_mtime:
        return True
    recorded_hash = state["file_hash"]
    return bool(recorded_hash and hash_file(path) != recorded_hash)


def _update_scan_state(conn, provider: str, path: Path, records) -> None:
    stat = path.stat()
    upsert_scan_state(
        conn,
        provider=provider,
        source_file=str(path),
        file_hash=records.source_file_hash or hash_file(path),
        file_size=stat.st_size,
        mtime=stat.st_mtime,
        last_line=max((session.source_line_end or 0 for session in records.sessions), default=0),
    )


def _is_aicg_capture_file(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
    except OSError:
        return False
    try:
        marker = json.loads(first)
    except json.JSONDecodeError:
        return False
    return isinstance(marker, dict) and marker.get("aicg_capture") is True


def _missing_source_warnings(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    try:
        with connect(db_path) as conn:
            return [
                row["source_file"]
                for row in conn.execute("SELECT source_file FROM scan_state ORDER BY source_file").fetchall()
                if not Path(row["source_file"]).exists()
            ]
    except (sqlite3.Error, ValueError):
        return []


def _usage_import_records(provider: str, source: Path) -> ParsedRecords:
    source_hash = hash_file(source)
    created_at = utc_now_iso()
    sessions: list[NormalizedSession] = []
    turns: list[NormalizedTurn] = []
    for index, item in enumerate(_usage_rows(source), start=1):
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else item
        model = str(item.get("model") or usage.get("model") or "unknown")
        native_cost = _float_or_none(item.get("cost_usd") or item.get("total_cost") or item.get("cost"))
        session_id = stable_id("import", provider, source_hash, index)
        turn_id = stable_id("import_turn", provider, source_hash, index)
        started_at = item.get("created_at") or item.get("timestamp") or created_at
        session = NormalizedSession(
            id=session_id,
            provider=provider,
            native_session_id=str(item.get("id") or f"{source_hash}:{index}"),
            lineage_id=f"import:{source_hash}",
            source_file=str(source),
            source_file_hash=source_hash,
            source_line_start=index,
            source_line_end=index,
            started_at=started_at,
            ended_at=started_at,
            status="completed",
            model=model,
            estimated_cost_usd=native_cost,
            credit_estimate=native_cost,
            cost_source="native" if native_cost is not None else None,
            raw_event_count=1,
            created_at=created_at,
        )
        input_tokens = _int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        cached_tokens = _int(usage.get("cached_input_tokens") or usage.get("cache_read_input_tokens"))
        output_tokens = _int(usage.get("output_tokens") or usage.get("completion_tokens"))
        turn = NormalizedTurn(
            id=turn_id,
            session_id=session_id,
            provider=provider,
            native_turn_key=f"import:{provider}:{source_hash}:{index}",
            source_file_hash=source_hash,
            source_line_start=index,
            source_line_end=index,
            started_at=started_at,
            ended_at=started_at,
            status="completed",
            model=model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            input_uncached_tokens=max(input_tokens - cached_tokens, 0),
            cache_read_input_tokens=cached_tokens,
            cache_creation_input_tokens=_int(usage.get("cache_creation_input_tokens")),
            output_tokens=output_tokens,
            reasoning_output_tokens=_int(usage.get("reasoning_output_tokens")),
            estimated_cost_usd=native_cost,
            credit_estimate=native_cost,
            cost_source="native" if native_cost is not None else None,
        )
        sessions.append(session)
        turns.append(turn)
    return ParsedRecords(sessions=sessions, turns=turns, source_file=str(source), source_file_hash=source_hash)


def _usage_rows(source: Path) -> list[dict]:
    text = source.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        loaded = json.loads(stripped)
        return [item for item in loaded if isinstance(item, dict)]
    rows = []
    for line in stripped.splitlines():
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _period_meta(period: str | None, *, utc: bool) -> dict[str, str] | None:
    if period is None:
        return None
    tz = dt.timezone.utc if utc else dt.datetime.now().astimezone().tzinfo
    now = dt.datetime.now(tz)
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=1)
    elif period == "week":
        start = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=7)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    return {
        "type": period,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "since": isoformat_utc(start.astimezone(dt.timezone.utc)),
    }


def _compare_snapshot(conn, period_meta: dict[str, str] | None) -> dict[str, object] | None:
    if not period_meta:
        return None
    current_start = dt.datetime.fromisoformat(period_meta["start"])
    if period_meta["type"] == "day":
        previous_start = current_start - dt.timedelta(days=1)
    elif period_meta["type"] == "week":
        previous_start = current_start - dt.timedelta(days=7)
    else:
        previous_start = (current_start.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    previous_key = previous_start.isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT report_json FROM report_snapshots WHERE period_type = ? AND period_start = ?",
        (period_meta["type"], previous_key),
    ).fetchone()
    if row is None:
        return None
    previous = json.loads(row["report_json"])
    overview = previous.get("overview", {})
    return {
        "previousPeriodStart": previous_key,
        "overview": overview,
    }


def _compare_delta(current_report: dict[str, object], previous_snapshot: dict[str, object] | None) -> dict[str, object] | None:
    if previous_snapshot is None:
        return None
    current = current_report.get("overview", {}) if isinstance(current_report.get("overview"), dict) else {}
    previous = previous_snapshot.get("overview", {}) if isinstance(previous_snapshot.get("overview"), dict) else {}
    current_cost = current.get("estimatedCostUsd")
    previous_cost = previous.get("estimatedCostUsd")
    return {
        "previousPeriodStart": previous_snapshot["previousPeriodStart"],
        "sessionsDelta": int(current.get("sessions") or 0) - int(previous.get("sessions") or 0),
        "totalTokensDelta": int(current.get("totalTokens") or 0) - int(previous.get("totalTokens") or 0),
        "estimatedCostUsdDelta": None
        if current_cost is None or previous_cost is None
        else float(current_cost) - float(previous_cost),
    }


def _read_git_activity(repo_path: Path, cutoff) -> dict[str, int]:
    since = cutoff.isoformat()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", f"--since={since}", "--numstat", "--pretty=format:commit %H %P"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return {"commits": 0, "merge_commits": 0, "insertions": 0, "deletions": 0}
    commits = 0
    merge_commits = 0
    insertions = 0
    deletions = 0
    for line in result.stdout.splitlines():
        if line.startswith("commit "):
            commits += 1
            parts = line.split()
            if len(parts) > 3:
                merge_commits += 1
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            insertions += int(parts[0])
            deletions += int(parts[1])
    return {"commits": commits, "merge_commits": merge_commits, "insertions": insertions, "deletions": deletions}


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    output = run_doctor(
        json_output=args.json,
        online=args.online,
        deep=args.deep,
        max_files=args.max_files,
        self_check=args.self_check,
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


def _inspect_session_model(conn, session_id: str) -> dict | None:
    session = conn.execute(
        """
        SELECT id, provider, native_session_id, lineage_id, parent_session_id,
               project_path, source_file_hash, source_line_start,
               source_line_end, started_at, ended_at, status, model, task_type,
               duration_ms, retry_count, background_flag, estimated_cost_usd,
               credit_estimate, cost_source, wasted_cost_usd,
               privacy_flags, policy_flags, raw_event_count,
               malformed_line_count, created_at
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        return None
    token_row = conn.execute(
        """
        SELECT
            COUNT(*) AS turns,
            COALESCE(SUM(input_uncached_tokens), 0) AS input_uncached_tokens,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(reasoning_output_tokens), 0) AS reasoning_output_tokens,
            COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
            COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read_input_tokens
        FROM turns
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    tool_row = conn.execute(
        """
        SELECT COUNT(*) AS tool_events, COALESCE(SUM(output_bytes), 0) AS output_bytes
        FROM tool_events
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    issues = [_row_dict(row) for row in conn.execute(
        """
        SELECT id, severity, code, title, detail, recommendation, evidence, created_at
        FROM issues
        WHERE session_id = ?
        ORDER BY severity, code
        """,
        (session_id,),
    ).fetchall()]
    evidence = [_row_dict(row) for row in conn.execute(
        """
        SELECT id, issue_id, session_id, turn_id, tool_event_id, source_file_hash,
               source_line_start, source_line_end, metric_name, metric_value,
               message_hash, created_at
        FROM issue_evidence
        WHERE session_id = ?
        ORDER BY issue_id, id
        """,
        (session_id,),
    ).fetchall()]
    return {
        "schemaVersion": 1,
        "session": _row_dict(session),
        "aggregates": {
            "turns": int(token_row["turns"] or 0),
            "toolEvents": int(tool_row["tool_events"] or 0),
            "toolOutputBytes": int(tool_row["output_bytes"] or 0),
            "inputUncachedTokens": int(token_row["input_uncached_tokens"] or 0),
            "inputTokens": int(token_row["input_tokens"] or 0),
            "cachedInputTokens": int(token_row["cached_input_tokens"] or 0),
            "outputTokens": int(token_row["output_tokens"] or 0),
            "reasoningOutputTokens": int(token_row["reasoning_output_tokens"] or 0),
            "cacheCreationInputTokens": int(token_row["cache_creation_input_tokens"] or 0),
            "cacheReadInputTokens": int(token_row["cache_read_input_tokens"] or 0),
        },
        "issues": issues,
        "evidence": evidence,
    }


def _render_inspect_session_markdown(model: dict) -> str:
    session = model["session"]
    aggregates = model["aggregates"]
    lines = [
        "# AgentOps Guard Session Inspect",
        "",
        f"- Session: `{session['id']}`",
        f"- Provider: {session['provider']}",
        f"- Project: `{session.get('project_path') or 'unknown'}`",
        f"- Status: {session.get('status') or 'unknown'}",
        f"- Model: {session.get('model') or 'unknown'}",
        f"- Source hash: `{session.get('source_file_hash') or 'unknown'}`",
        f"- Source lines: {session.get('source_line_start') or 'unknown'}-{session.get('source_line_end') or 'unknown'}",
        "",
        "## Aggregates",
        f"- Turns: {aggregates['turns']}",
        f"- Tool events: {aggregates['toolEvents']}",
        f"- Total tokens: {_inspect_total_tokens(session['provider'], aggregates)}",
        f"- Tool output bytes: {aggregates['toolOutputBytes']}",
        "",
        "## Issues",
    ]
    if model["issues"]:
        for issue in model["issues"]:
            lines.append(f"- `{issue['code']}` [{issue['severity']}]: {issue['title']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Evidence pointers"])
    if model["evidence"]:
        for evidence in model["evidence"]:
            lines.append(
                f"- `{evidence['issue_id']}` {evidence['metric_name']}={evidence['metric_value']} source={evidence.get('source_file_hash') or 'unknown'} lines={evidence.get('source_line_start') or 'unknown'}-{evidence.get('source_line_end') or 'unknown'}"
            )
    else:
        lines.append("- none")
    return "\n".join(lines)


def _inspect_total_tokens(provider: str, aggregates: dict) -> int:
    return (
        int(aggregates.get("inputUncachedTokens") or 0)
        + int(aggregates["cacheCreationInputTokens"])
        + int(aggregates["cacheReadInputTokens"])
        + int(aggregates["outputTokens"])
    )


EXPORT_TABLES = {
    "sessions": (
        "sessions",
        "COALESCE(started_at, created_at)",
        [
            "id", "provider", "project_path", "source_file_hash", "source_line_start",
            "source_line_end", "native_session_id", "lineage_id", "parent_session_id",
            "started_at", "ended_at", "status", "model", "task_type",
            "duration_ms", "retry_count", "background_flag", "estimated_cost_usd",
            "credit_estimate", "cost_source", "wasted_cost_usd",
            "privacy_flags", "policy_flags", "raw_event_count",
            "malformed_line_count", "created_at",
        ],
    ),
    "turns": (
        "turns",
        "COALESCE(started_at, ended_at)",
        [
            "id", "session_id", "provider", "source_file_hash", "source_line_start",
            "source_line_end", "native_turn_key", "started_at", "ended_at", "status", "model", "task_type",
            "duration_ms", "retry_count", "background_flag", "input_uncached_tokens", "input_tokens",
            "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens", "estimated_cost_usd",
            "credit_estimate", "cost_source", "privacy_flags", "policy_flags", "token_flags", "error_type",
            "error_message_hash",
        ],
    ),
    "tool-events": (
        "tool_events",
        "COALESCE(started_at, ended_at)",
        [
            "id", "session_id", "turn_id", "provider", "source_file_hash",
            "source_line_start", "source_line_end", "tool_type", "tool_name", "status",
            "started_at", "ended_at", "duration_ms", "output_bytes", "exit_code", "call_target",
        ],
    ),
    "issues": (
        "issues",
        "created_at",
        [
            "id", "session_id", "severity", "code", "title", "detail",
            "recommendation", "evidence", "created_at",
        ],
    ),
    "scan-errors": (
        "scan_errors",
        "created_at",
        ["id", "provider", "source_file", "error_type", "error_message_hash", "created_at"],
    ),
}


def _export_rows(conn, kind: str, cutoff_iso: str) -> list[dict]:
    table, time_expr, columns = EXPORT_TABLES[kind]
    column_sql = ", ".join(columns)
    rows = conn.execute(
        f"""
        SELECT {column_sql}
        FROM {table}
        WHERE {time_expr} >= ?
        ORDER BY {time_expr} DESC
        """,
        (cutoff_iso,),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


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
        records = reader.read(path)
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
    clear_scan_error(conn, provider=provider, source_file=str(path))
    return records


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
    for index, arg in enumerate(command):
        safe = redact_secrets(arg)
        if index == 0:
            safe = Path(safe).name
        elif safe.startswith("-"):
            safe = safe.split("=", 1)[0]
        else:
            safe = "[redacted-arg]"
        if "\n" in safe or len(safe) > 120:
            safe = "[redacted-arg]"
        sanitized.append(safe)
    return shlex.join(sanitized)
