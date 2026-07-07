from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import app_paths
from .models import PolicyFinding
from .util import sha256_text, stable_id, utc_now_iso


LEVEL_ORDER = {"needs_review": 1, "warning": 2, "violation": 3}


@dataclass(frozen=True)
class EffectivePolicy:
    version: int
    policy_hash: str
    services_mode: str
    services_deny: tuple[str, ...]
    services_allow: tuple[str, ...]
    flag_mentions: bool
    raw_payload_threshold_bytes: int
    secret_patterns: tuple[tuple[str, re.Pattern[str]], ...]
    sensitive_path_patterns: tuple[tuple[str, re.Pattern[str]], ...]


def load_policy(app_dir: Path | None = None, project_path: str | None = None) -> EffectivePolicy:
    paths = app_paths(app_dir)
    raw: dict[str, Any] = {}
    if paths["policy"].exists():
        with paths["policy"].open("rb") as handle:
            raw = tomllib.load(handle)
    policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    version = int(policy.get("version", 1))
    services = policy.get("services") if isinstance(policy.get("services"), dict) else {}
    raw_payload = policy.get("raw_payload") if isinstance(policy.get("raw_payload"), dict) else {}
    secrets = policy.get("secrets") if isinstance(policy.get("secrets"), dict) else {}
    sensitive_paths = policy.get("sensitive_paths") if isinstance(policy.get("sensitive_paths"), dict) else {}

    service_mode = str(services.get("mode", "denylist"))
    service_deny = _string_tuple(services.get("deny", ["api.openai.com"]))
    service_allow = _string_tuple(services.get("allow", []))

    overrides = policy.get("overrides") if isinstance(policy.get("overrides"), dict) else {}
    if project_path and project_path in overrides and isinstance(overrides[project_path], dict):
        override_services = overrides[project_path].get("services")
        if isinstance(override_services, dict):
            service_mode = str(override_services.get("mode", service_mode))
            service_deny = _string_tuple(override_services.get("deny", service_deny))
            service_allow = _string_tuple(override_services.get("allow", service_allow))

    secret_patterns = list(_builtin_secret_patterns())
    for item in secrets.get("custom", []) if isinstance(secrets.get("custom"), list) else []:
        compiled = _custom_pattern(item)
        if compiled:
            secret_patterns.append(compiled)

    path_patterns = list(_builtin_sensitive_path_patterns())
    for item in sensitive_paths.get("custom", []) if isinstance(sensitive_paths.get("custom"), list) else []:
        compiled = _custom_pattern(item)
        if compiled:
            path_patterns.append(compiled)

    canonical = json.dumps(raw or {"policy": {"version": version}}, sort_keys=True, ensure_ascii=False)
    return EffectivePolicy(
        version=version,
        policy_hash=sha256_text(canonical),
        services_mode=service_mode,
        services_deny=service_deny,
        services_allow=service_allow,
        flag_mentions=bool(services.get("flag_mentions", False)),
        raw_payload_threshold_bytes=int(raw_payload.get("threshold_bytes", 65536)),
        secret_patterns=tuple(secret_patterns),
        sensitive_path_patterns=tuple(path_patterns),
    )


def policy_rules_model(policy: EffectivePolicy) -> dict[str, Any]:
    return {
        "version": policy.version,
        "policyHash": policy.policy_hash,
        "services": {
            "mode": policy.services_mode,
            "deny": list(policy.services_deny),
            "allow": list(policy.services_allow),
            "flagMentions": policy.flag_mentions,
        },
        "secrets": [name for name, _ in policy.secret_patterns],
        "sensitivePaths": [name for name, _ in policy.sensitive_path_patterns],
        "rawPayloadThresholdBytes": policy.raw_payload_threshold_bytes,
    }


def evaluate_policy_findings(
    conn: sqlite3.Connection,
    policy: EffectivePolicy,
    *,
    since_iso: str | None = None,
) -> list[PolicyFinding]:
    tool_rows = conn.execute(
        """
        SELECT te.id, te.session_id, te.call_target, te.started_at, te.ended_at,
               s.provider, s.project_path
        FROM tool_events te
        JOIN sessions s ON s.id = te.session_id
        WHERE (? IS NULL OR COALESCE(te.started_at, te.ended_at) >= ?)
        """,
        (since_iso, since_iso),
    ).fetchall()
    now = utc_now_iso()
    findings: list[PolicyFinding] = []
    for row in tool_rows:
        effective_policy = load_policy(project_path=row["project_path"]) if row["project_path"] else policy
        targets = [target.strip().lower() for target in (row["call_target"] or "").split(",") if target.strip()]
        for target in targets:
            finding = _service_finding(row, effective_policy, target, now)
            if finding:
                findings.append(finding)
    return findings


def redact_with_policy(text: str, policy: EffectivePolicy) -> str:
    redacted = text
    for _, pattern in (*policy.secret_patterns, *policy.sensitive_path_patterns):
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def sensitive_policy_findings(
    policy: EffectivePolicy,
    *,
    value: Any,
    session_id: str | None,
    tool_event_id: str | None,
    surface: str,
    now: str | None = None,
) -> list[PolicyFinding]:
    text = _safe_policy_text(value)
    if not text:
        return []
    timestamp = now or utc_now_iso()
    level = _level_for_surface(surface)
    findings: list[PolicyFinding] = []
    for prefix, patterns in (
        ("secrets", policy.secret_patterns),
        ("sensitive_paths", policy.sensitive_path_patterns),
    ):
        for name, pattern in patterns:
            for match in pattern.finditer(text):
                matched = match.group(0)
                detail_hash = sha256_text(matched)
                rule_id = f"{prefix}.{name}"
                findings.append(
                    PolicyFinding(
                        id=stable_id("policy", rule_id, session_id, tool_event_id or "", surface, detail_hash),
                        session_id=session_id,
                        tool_event_id=tool_event_id,
                        rule_id=rule_id,
                        level=level,
                        surface=surface,
                        detail_hash=detail_hash,
                        first_seen_at=timestamp,
                        last_seen_at=timestamp,
                    )
                )
    return findings


def sensitive_policy_flags(policy: EffectivePolicy, value: Any) -> tuple[set[str], set[str]]:
    text = _safe_policy_text(value)
    if not text:
        return set(), set()
    privacy_flags: set[str] = set()
    policy_flags: set[str] = set()
    if any(pattern.search(text) for _, pattern in policy.secret_patterns):
        privacy_flags.add("POSSIBLE_SECRET")
        policy_flags.add("POLICY_SECRET")
    if any(pattern.search(text) for _, pattern in policy.sensitive_path_patterns):
        privacy_flags.add("SENSITIVE_PATH")
        policy_flags.add("POLICY_SENSITIVE_PATH")
    return privacy_flags, policy_flags


def unacked_policy_findings(
    conn: sqlite3.Connection,
    *,
    since_iso: str | None = None,
    include_acked: bool = False,
) -> list[sqlite3.Row]:
    ack_filter = "" if include_acked else "AND pa.finding_id IS NULL"
    return conn.execute(
        f"""
        SELECT pf.*, pa.acked_at, pa.reason
        FROM policy_findings pf
        LEFT JOIN policy_acks pa ON pa.finding_id = pf.id
        WHERE (? IS NULL OR pf.last_seen_at >= ?)
          {ack_filter}
        ORDER BY CASE pf.level
            WHEN 'violation' THEN 0
            WHEN 'warning' THEN 1
            ELSE 2
        END, pf.last_seen_at DESC
        """,
        (since_iso, since_iso),
    ).fetchall()


def should_fail_policy(rows: list[sqlite3.Row], fail_on: str) -> bool:
    threshold = LEVEL_ORDER[fail_on]
    return any(LEVEL_ORDER.get(row["level"], 0) >= threshold for row in rows)


def _level_for_surface(surface: str) -> str:
    if surface == "call":
        return "violation"
    if surface == "output":
        return "warning"
    return "needs_review"


def _safe_policy_text(value: Any) -> str:
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
            parts.append(_safe_policy_text(item))
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_safe_policy_text(item) for item in value)
    return str(value)


def _service_finding(row: sqlite3.Row, policy: EffectivePolicy, target: str, now: str) -> PolicyFinding | None:
    provider = str(row["provider"] or "").lower()
    if _is_provider_self_call(provider, target) and target not in policy.services_deny:
        return None
    if policy.services_mode == "allowlist":
        allowed = any(_target_matches(target, allowed_host) for allowed_host in policy.services_allow)
        if allowed:
            return None
        rule_id = f"services.allowlist:{target}"
    else:
        denied = any(_target_matches(target, denied_host) for denied_host in policy.services_deny)
        if not denied:
            return None
        rule_id = f"services.denylist:{target}"
    detail_hash = sha256_text(target)
    return PolicyFinding(
        id=stable_id("policy", rule_id, row["session_id"], detail_hash),
        session_id=row["session_id"],
        tool_event_id=row["id"],
        rule_id=rule_id,
        level="violation",
        surface="call",
        detail_hash=detail_hash,
        first_seen_at=now,
        last_seen_at=now,
    )


def _target_matches(target: str, configured: str) -> bool:
    configured = configured.lower().strip()
    return target == configured or target.endswith("." + configured)


def _is_provider_self_call(provider: str, target: str) -> bool:
    return (provider == "codex" and _target_matches(target, "api.openai.com")) or (
        provider == "claude" and _target_matches(target, "api.anthropic.com")
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).lower() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.lower(),)
    return ()


def _custom_pattern(item: Any) -> tuple[str, re.Pattern[str]] | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    pattern = item.get("pattern")
    if not isinstance(name, str) or not isinstance(pattern, str):
        return None
    return name, re.compile(pattern)


def _builtin_secret_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        ("openai_key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
        ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
        ("github_token", re.compile(r"(ghp_|github_pat_)[A-Za-z0-9_]{20,}")),
        ("bearer_header", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")),
        ("generic_env_assignment", re.compile(r"\b[A-Z0-9_]*(?:API|TOKEN|SECRET|KEY)[A-Z0-9_]*=([^\s]+)", re.IGNORECASE)),
    ]


def _builtin_sensitive_path_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        ("dot_env", re.compile(r"(?i)(^|/)\.env(\.|$|/)")),
        ("ssh_keys", re.compile(r"(?i)(^|/)\.ssh(/|$)")),
        ("cloud_credentials", re.compile(r"(?i)(^|/)\.(aws|config/gcloud)(/|$)")),
    ]
