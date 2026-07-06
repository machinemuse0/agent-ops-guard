from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any


UTC = dt.timezone.utc


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def isoformat_utc(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return isoformat_utc(now_utc())


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_timestamp(value: str | None) -> str | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return isoformat_utc(parsed)


def parse_since(value: str | None, now: dt.datetime | None = None) -> dt.datetime:
    current = now or now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if not value:
        return current - dt.timedelta(hours=24)
    text = value.strip()
    match = re.fullmatch(r"(\d+)([smhdw])", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "s":
            return current - dt.timedelta(seconds=amount)
        if unit == "m":
            return current - dt.timedelta(minutes=amount)
        if unit == "h":
            return current - dt.timedelta(hours=amount)
        if unit == "d":
            return current - dt.timedelta(days=amount)
        if unit == "w":
            return current - dt.timedelta(weeks=amount)
    parsed = parse_iso_datetime(text)
    if parsed is not None:
        return parsed
    raise ValueError(f"Unsupported --since value: {value!r}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{sha256_text(joined)[:24]}"


def redact_secrets(text: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "sk-[REDACTED]", text)
    redacted = re.sub(r"ghp_[A-Za-z0-9_]{20,}", "ghp_[REDACTED]", redacted)
    redacted = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"\b([A-Z0-9_]*(?:API|TOKEN|SECRET|KEY)[A-Z0-9_]*)=([^\s]+)",
        r"\1=[REDACTED]",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b[A-Z0-9_]*(?:API|TOKEN|SECRET|KEY)[A-Z0-9_]*=([^\s]+)", re.IGNORECASE),
)


SENSITIVE_PATH_PATTERNS = (
    re.compile(r"(?i)(^|/)\.env(\.|$|/)"),
    re.compile(r"(?i)(^|/)\.ssh(/|$)"),
    re.compile(r"(?i)(^|/)(id_rsa|id_ed25519|auth\.json|credentials)(\.|$)"),
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/home/[^/\s]+/"),
)


RESTRICTED_SERVICE_PATTERNS = (
    re.compile(r"api\.openai\.com|chatgpt\.com/backend-api", re.IGNORECASE),
    re.compile(r"api\.anthropic\.com|claude\.ai", re.IGNORECASE),
    re.compile(r"openrouter\.ai|generativelanguage\.googleapis\.com", re.IGNORECASE),
    re.compile(r"api\.deepseek\.com|api\.moonshot\.cn", re.IGNORECASE),
)


def flags_to_text(flags: set[str]) -> str | None:
    if not flags:
        return None
    return ",".join(sorted(flags))


def split_flags(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def detect_privacy_flags(value: Any, *, raw_payload_threshold: int = 2048) -> set[str]:
    text = _safe_text(value)
    if not text:
        return set()
    flags: set[str] = set()
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        flags.add("POSSIBLE_SECRET")
    if any(pattern.search(text) for pattern in SENSITIVE_PATH_PATTERNS):
        flags.add("SENSITIVE_PATH")
    if len(text.encode("utf-8", errors="replace")) > raw_payload_threshold:
        flags.add("RAW_PAYLOAD_RISK")
    return flags


def detect_policy_flags(value: Any) -> set[str]:
    text = _safe_text(value)
    if not text:
        return set()
    if any(pattern.search(text) for pattern in RESTRICTED_SERVICE_PATTERNS):
        return {"RESTRICTED_SERVICE_CALL"}
    return set()


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


def duration_ms(started_at: str | None, ended_at: str | None) -> int | None:
    started = parse_iso_datetime(started_at)
    ended = parse_iso_datetime(ended_at)
    if started is None or ended is None:
        return None
    delta = ended - started
    return max(0, int(delta.total_seconds() * 1000))


def file_mtime_after(path: Path, cutoff: dt.datetime) -> bool:
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return mtime >= cutoff
