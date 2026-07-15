from __future__ import annotations

import json
import re
import shlex
import sqlite3
from dataclasses import dataclass
from typing import Any

from .util import escape_markdown_text, markdown_code, redact_secrets, sha256_text, split_flags, stable_id


SECURITY_AUDIT_SCHEMA_VERSION = 1
SECURITY_RULESET_VERSION = 2
SECURITY_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class SecurityRule:
    code: str
    rule_id: str
    severity: str
    metric_value: str
    recommendation: str


@dataclass(frozen=True)
class SecuritySignal:
    code: str
    rule_id: str
    severity: str
    metric_name: str
    metric_value: str
    recommendation: str


RULES: dict[str, SecurityRule] = {
    "sensitive_command.shell_history": SecurityRule(
        "SENSITIVE_COMMAND",
        "sensitive_command.shell_history",
        "high",
        "shell_history_access",
        "Avoid reading shell history in agent runs; inspect it manually if needed.",
    ),
    "sensitive_command.keychain_dump": SecurityRule(
        "SENSITIVE_COMMAND",
        "sensitive_command.keychain_dump",
        "high",
        "keychain_secret_access",
        "Do not let the agent query keychains or password stores without explicit human review.",
    ),
    "sensitive_command.cloud_auth_token": SecurityRule(
        "SENSITIVE_COMMAND",
        "sensitive_command.cloud_auth_token",
        "high",
        "cloud_auth_token_access",
        "Use a scoped, non-secret status command or manual credential check instead.",
    ),
    "sensitive_command.ssh_key_material": SecurityRule(
        "SENSITIVE_COMMAND",
        "sensitive_command.ssh_key_material",
        "high",
        "ssh_key_material_access",
        "Do not expose private key material to an agent command.",
    ),
    "sensitive_command.env_secret_dump": SecurityRule(
        "SENSITIVE_COMMAND",
        "sensitive_command.env_secret_dump",
        "medium",
        "environment_secret_dump",
        "Filter environment inspection to non-secret variable names.",
    ),
    "dangerous_flag.privileged": SecurityRule(
        "DANGEROUS_FLAG",
        "dangerous_flag.privileged",
        "high",
        "privileged_execution",
        "Review the privilege boundary manually before rerunning.",
    ),
    "dangerous_flag.no_sandbox": SecurityRule(
        "DANGEROUS_FLAG",
        "dangerous_flag.no_sandbox",
        "high",
        "sandbox_disabled",
        "Keep sandboxing enabled unless the command is isolated and reviewed.",
    ),
    "dangerous_flag.allow_root": SecurityRule(
        "DANGEROUS_FLAG",
        "dangerous_flag.allow_root",
        "medium",
        "root_execution_allowed",
        "Prefer a non-root workflow for agent-driven commands.",
    ),
    "dangerous_flag.force": SecurityRule(
        "DANGEROUS_FLAG",
        "dangerous_flag.force",
        "medium",
        "force_flag",
        "Confirm the command is idempotent and scoped before using force flags.",
    ),
    "dangerous_flag.recursive_force": SecurityRule(
        "DANGEROUS_FLAG",
        "dangerous_flag.recursive_force",
        "high",
        "recursive_force",
        "Replace recursive force deletion with an explicit reviewed path and dry run.",
    ),
    "credential_access.secret_literal": SecurityRule(
        "CREDENTIAL_ACCESS",
        "credential_access.secret_literal",
        "high",
        "secret_literal",
        "Rotate exposed credentials if real and remove the raw local source log.",
    ),
    "credential_access.sensitive_path": SecurityRule(
        "CREDENTIAL_ACCESS",
        "credential_access.sensitive_path",
        "high",
        "sensitive_path",
        "Avoid reading credential-bearing files from agent commands.",
    ),
    "download_tool.remote_fetch": SecurityRule(
        "DOWNLOAD_TOOL",
        "download_tool.remote_fetch",
        "medium",
        "remote_fetch_tool",
        "Pin checksums and inspect downloaded content before execution.",
    ),
    "download_tool.package_install": SecurityRule(
        "DOWNLOAD_TOOL",
        "download_tool.package_install",
        "medium",
        "package_install",
        "Review package names, lockfiles, and install scripts before running.",
    ),
    "download_tool.pipe_to_shell": SecurityRule(
        "DOWNLOAD_TOOL",
        "download_tool.pipe_to_shell",
        "high",
        "download_pipe_to_shell",
        "Never pipe remote content directly to a shell; download, verify, then run manually.",
    ),
    "startup_persistence.crontab_write": SecurityRule(
        "STARTUP_PERSISTENCE",
        "startup_persistence.crontab_write",
        "high",
        "crontab_write",
        "Print scheduler config for manual review instead of installing it automatically.",
    ),
    "startup_persistence.launch_agent_write": SecurityRule(
        "STARTUP_PERSISTENCE",
        "startup_persistence.launch_agent_write",
        "high",
        "launch_agent_write",
        "Do not let the agent write LaunchAgent or LaunchDaemon entries without review.",
    ),
    "startup_persistence.systemd_enable": SecurityRule(
        "STARTUP_PERSISTENCE",
        "startup_persistence.systemd_enable",
        "high",
        "systemd_enable",
        "Review systemd units manually before enabling persistence.",
    ),
    "startup_persistence.shell_profile_write": SecurityRule(
        "STARTUP_PERSISTENCE",
        "startup_persistence.shell_profile_write",
        "medium",
        "shell_profile_write",
        "Avoid agent-driven shell startup file edits unless scoped and reviewed.",
    ),
}


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b[A-Z0-9_]*(?:API|TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*=([^\s]+)"),
)

SENSITIVE_PATH_PATTERNS = (
    re.compile(r"(?i)(^|[\s=:])(?:~|/[^ \t\n\r;|&]*)?/.?ssh(?:/|\b)"),
    re.compile(r"(?i)(^|[\s=:])(?:~|/[^ \t\n\r;|&]*)?/.?aws(?:/|\b)"),
    re.compile(r"(?i)(^|[\s=:])(?:~|/[^ \t\n\r;|&]*)?/.?config/(?:gcloud|gh)(?:/|\b)"),
    re.compile(r"(?i)(^|[\s=:])(?:~|/[^ \t\n\r;|&]*)?/kube/config\b"),
    re.compile(r"(?i)(^|[\s=:])(?:~|/[^ \t\n\r;|&]*)?/.env(?:\.|\b|/)"),
    re.compile(r"(?i)(^|[\s=:])(?:~|/[^ \t\n\r;|&]*)?(?:id_rsa|id_ed25519|auth\.json|credentials|\.npmrc|\.pypirc)\b"),
)

REMOTE_FETCH_TOOLS = {"curl", "wget"}
PACKAGE_INSTALL_TOOLS = {"npm", "pnpm", "yarn", "bun", "pip", "pip3", "brew"}


def derive_security_tool_metadata(value: Any) -> dict[str, str | None]:
    text = _safe_text(value)
    if not text:
        return {"security_flags": None, "security_detail": None, "command_hash": None}
    signals = detect_security_signals(text)
    if not signals:
        return {"security_flags": None, "security_detail": None, "command_hash": None}
    return {
        "security_flags": ",".join(sorted({signal.code for signal in signals})),
        "security_detail": ",".join(sorted({signal.rule_id for signal in signals})),
        "command_hash": sha256_text(redact_secrets(text)),
    }


def detect_security_signals(value: Any) -> list[SecuritySignal]:
    text = _safe_text(value)
    if not text:
        return []
    lowered = text.lower()
    tokens = _tokens(text)
    token_set = set(tokens)
    rule_ids: set[str] = set()

    if re.search(r"(?i)\b(?:cat|grep|rg|tail|less|more|sed)\b[^\n]*(?:zsh_history|bash_history|fish_history|\.history)\b", text):
        rule_ids.add("sensitive_command.shell_history")
    if re.search(r"(?i)\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain|unlock-keychain)\b", text):
        rule_ids.add("sensitive_command.keychain_dump")
    if re.search(r"(?i)\b(?:gcloud|aws|gh|npm)\s+auth\b[^\n]*(?:token|print|login|configure|get)", text):
        rule_ids.add("sensitive_command.cloud_auth_token")
    if re.search(r"(?i)\b(?:ssh-add\s+-L|ssh-keygen\s+-y|openssl\s+rsa)\b", text):
        rule_ids.add("sensitive_command.ssh_key_material")
    if re.search(r"(?i)\b(?:env|printenv|set)\b[^\n]*(?:api|token|secret|key|password)", text):
        rule_ids.add("sensitive_command.env_secret_dump")

    if "--privileged" in token_set:
        rule_ids.add("dangerous_flag.privileged")
    if "--no-sandbox" in token_set:
        rule_ids.add("dangerous_flag.no_sandbox")
    if "--allow-root" in token_set:
        rule_ids.add("dangerous_flag.allow_root")
    if "--force" in token_set:
        rule_ids.add("dangerous_flag.force")
    if _has_recursive_force(tokens):
        rule_ids.add("dangerous_flag.recursive_force")

    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        rule_ids.add("credential_access.secret_literal")
    if any(pattern.search(text) for pattern in SENSITIVE_PATH_PATTERNS):
        rule_ids.add("credential_access.sensitive_path")

    if re.search(r"(?is)\b(?:curl|wget)\b.+\|\s*(?:sh|bash|zsh)\b", text) or re.search(
        r"(?is)<\(\s*(?:curl|wget)\b", text
    ):
        rule_ids.add("download_tool.pipe_to_shell")
    if any(_base_command(token) in REMOTE_FETCH_TOOLS for token in tokens):
        rule_ids.add("download_tool.remote_fetch")
    if _has_package_install(tokens):
        rule_ids.add("download_tool.package_install")

    if _has_crontab_write(tokens):
        rule_ids.add("startup_persistence.crontab_write")
    if re.search(
        r"(?i)(?:launchctl\s+(?:load|bootstrap|enable)|(?:tee|cp|mv|install|>|>>).*(?:launchagents|launchdaemons)|(?:launchagents|launchdaemons).*(?:tee|cp|mv|write|install|>))",
        lowered,
    ):
        rule_ids.add("startup_persistence.launch_agent_write")
    if re.search(r"(?i)\bsystemctl\b[^\n]*(?:enable|link|preset)\b", text):
        rule_ids.add("startup_persistence.systemd_enable")
    if re.search(r"(?i)(?:>>|tee|cat\s+>|echo\s+.*>)[^\n]*(?:\.zshrc|\.bashrc|\.profile|\.bash_profile|config/fish/config\.fish)", text):
        rule_ids.add("startup_persistence.shell_profile_write")

    return [
        SecuritySignal(
            code=RULES[rule_id].code,
            rule_id=rule_id,
            severity=RULES[rule_id].severity,
            metric_name="security_rule",
            metric_value=RULES[rule_id].metric_value,
            recommendation=RULES[rule_id].recommendation,
        )
        for rule_id in sorted(rule_ids)
    ]


def build_security_audit_model(conn: sqlite3.Connection, *, since: str, label: str | None = None) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT te.id AS tool_event_id, te.session_id, te.turn_id, te.provider,
               te.source_file_hash, te.source_line_start, te.source_line_end,
               te.security_flags, te.security_detail, te.command_hash,
               COALESCE(te.started_at, te.ended_at, s.started_at, s.created_at) AS observed_at
        FROM tool_events te
        JOIN sessions s ON s.id = te.session_id
        WHERE COALESCE(te.started_at, te.ended_at, s.started_at, s.created_at) >= ?
          AND te.security_detail IS NOT NULL
        ORDER BY observed_at, te.session_id, te.id
        """,
        (since,),
    ).fetchall()
    findings = []
    for row in rows:
        for rule_id in split_flags(row["security_detail"]):
            rule = RULES.get(rule_id)
            if rule is None:
                continue
            findings.append(_finding_from_tool_row(row, rule))
    summary = _summary(findings)
    return {
        "schemaVersion": SECURITY_AUDIT_SCHEMA_VERSION,
        "rulesetVersion": SECURITY_RULESET_VERSION,
        "kind": "security_audit",
        "since": label or since,
        "summary": summary,
        "findings": findings,
    }


def render_security_audit_json(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True)


def render_security_audit_markdown(model: dict[str, Any]) -> str:
    summary = model["summary"]
    lines = [
        "# AgentOps Guard Security Audit",
        "",
        f"- Since: {escape_markdown_text(model['since'])}",
        f"- Ruleset version: {model['rulesetVersion']}",
        f"- Findings: {summary['findings']}",
        f"- High: {summary['bySeverity'].get('high', 0)}",
        f"- Medium: {summary['bySeverity'].get('medium', 0)}",
        f"- Low: {summary['bySeverity'].get('low', 0)}",
        "",
        "## Findings",
        "",
    ]
    findings = model.get("findings") or []
    if not findings:
        lines.append("No security audit findings detected.")
        return "\n".join(lines)
    for finding in findings:
        lines.append(
            f"- **{escape_markdown_text(finding['severity'])}** `{finding['code']}` `{finding['ruleId']}`"
        )
        lines.append(f"  Session: {_md_code(finding.get('sessionId') or 'unknown')}")
        lines.append(f"  Tool event: {_md_code(finding.get('toolEventId') or 'unknown')}")
        lines.append(f"  Source hash: {_md_code(finding.get('sourceFileHash') or 'unknown')}")
        lines.append(f"  Source lines: {escape_markdown_text(_line_range(finding))}")
        lines.append(f"  Metric: `{finding['metricName']}`={_md_code(finding['metricValue'])}")
        lines.append(f"  Command hash: {_md_code(finding.get('commandHash') or 'unknown')}")
        lines.append(f"  Recommendation: {escape_markdown_text(finding['recommendation'])}")
    return "\n".join(lines)


def should_fail_security_audit(model: dict[str, Any], fail_on: str) -> bool:
    if fail_on == "none":
        return False
    threshold = SECURITY_SEVERITY_ORDER[fail_on]
    return any(SECURITY_SEVERITY_ORDER.get(str(item.get("severity")), 0) >= threshold for item in model.get("findings") or [])


def _finding_from_tool_row(row: sqlite3.Row, rule: SecurityRule) -> dict[str, Any]:
    command_hash = row["command_hash"]
    return {
        "id": stable_id("security_audit", row["session_id"], row["tool_event_id"], rule.rule_id, command_hash or ""),
        "severity": rule.severity,
        "code": rule.code,
        "ruleId": rule.rule_id,
        "sessionId": row["session_id"],
        "turnId": row["turn_id"],
        "toolEventId": row["tool_event_id"],
        "provider": row["provider"],
        "sourceFileHash": row["source_file_hash"],
        "sourceLineStart": row["source_line_start"],
        "sourceLineEnd": row["source_line_end"],
        "metricName": "security_rule",
        "metricValue": rule.metric_value,
        "commandHash": command_hash,
        "recommendation": rule.recommendation,
    }


def _summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_code: dict[str, int] = {}
    for finding in findings:
        severity = str(finding["severity"])
        by_severity[severity] = by_severity.get(severity, 0) + 1
        code = str(finding["code"])
        by_code[code] = by_code.get(code, 0) + 1
    return {"findings": len(findings), "bySeverity": by_severity, "byCode": dict(sorted(by_code.items()))}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            parts.append(str(key))
            parts.append(_safe_text(item))
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_safe_text(item) for item in value)
    return str(value)


def _tokens(text: str) -> list[str]:
    try:
        parsed = shlex.split(text)
    except ValueError:
        parsed = text.split()
    return [token.lower() for token in parsed]


def _base_command(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _has_recursive_force(tokens: list[str]) -> bool:
    for token in tokens:
        compact = token.replace("-", "")
        if token.startswith("-") and "r" in compact and "f" in compact:
            return True
    return "-r" in tokens and "-f" in tokens


def _has_crontab_write(tokens: list[str]) -> bool:
    bases = [_base_command(token) for token in tokens]
    for index, token in enumerate(bases):
        if token != "crontab":
            continue
        following = tokens[index + 1 : index + 3]
        if not following:
            return False
        if following[0] == "-l":
            return False
        return any(item in {"-", "-e", "-r"} or not item.startswith("-") for item in following)
    return False


def _has_package_install(tokens: list[str]) -> bool:
    bases = [_base_command(token) for token in tokens]
    for index, token in enumerate(bases):
        if token in {"npm", "pnpm", "yarn", "bun"} and _next_token_is_install(bases, index):
            return True
        if token in {"pip", "pip3"} and "install" in bases[index + 1 : index + 3]:
            return True
        if token == "python" and bases[index + 1 : index + 4] == ["-m", "pip", "install"]:
            return True
        if token == "brew" and "install" in bases[index + 1 : index + 3]:
            return True
    return False


def _next_token_is_install(tokens: list[str], index: int) -> bool:
    return any(token in {"install", "add"} for token in tokens[index + 1 : index + 3])


def _line_range(finding: dict[str, Any]) -> str:
    start = finding.get("sourceLineStart")
    end = finding.get("sourceLineEnd")
    if start is None and end is None:
        return "unknown"
    if start == end or end is None:
        return str(start)
    return f"{start}-{end}"


def _md_code(value: object) -> str:
    return markdown_code(value)
