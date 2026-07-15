from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .util import SECRET_PATTERNS, SENSITIVE_PATH_PATTERNS, redact_secrets


CONTENT_KEYS = {
    "message",
    "messages",
    "content",
    "output",
    "stdout",
    "stderr",
    "command",
    "arguments",
    "args",
    "input",
    "prompt",
    "completion",
    "text",
}


PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|private/tmp|tmp)/[^\s\"'<>|,;)]+")


class RedactionError(ValueError):
    pass


def redact_fixture(input_path: Path, output_path: Path, *, keep_structure: bool = False) -> dict[str, int]:
    aliases: dict[str, str] = {}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    lines = 0
    malformed = 0
    with input_path.open("r", encoding="utf-8", errors="replace") as source, tmp_path.open("w", encoding="utf-8") as dest:
        for line_number, line in enumerate(source, start=1):
            text = line.strip()
            if not text:
                continue
            lines += 1
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                malformed += 1
                safe_event = {"aicg_redacted_malformed": True, "line": line_number}
            else:
                safe_event = _redact_value(event, aliases, keep_structure=keep_structure, force_content=False)
            dest.write(json.dumps(safe_event, ensure_ascii=False, sort_keys=True) + "\n")
    leaks = _leak_locations(tmp_path)
    if leaks:
        tmp_path.unlink(missing_ok=True)
        preview = "; ".join(leaks[:5])
        raise RedactionError(f"redacted fixture still contains sensitive content: {preview}")
    tmp_path.replace(output_path)
    return {"lines": lines, "malformed": malformed, "pathAliases": len(aliases)}


def _redact_value(value: Any, aliases: dict[str, str], *, keep_structure: bool, force_content: bool) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            safe[key_text] = _redact_value(
                item,
                aliases,
                keep_structure=keep_structure,
                force_content=force_content or key_text.lower() in CONTENT_KEYS,
            )
        return safe
    if isinstance(value, list):
        return [
            _redact_value(item, aliases, keep_structure=keep_structure, force_content=force_content)
            for item in value
        ]
    if isinstance(value, str):
        if force_content:
            return _content_placeholder(value, keep_structure=keep_structure)
        return _alias_paths(redact_secrets(value), aliases)
    return value


def _content_placeholder(value: str, *, keep_structure: bool) -> str:
    byte_len = len(value.encode("utf-8", errors="replace"))
    if keep_structure:
        return "X" * len(value)
    return f"[REDACTED len={byte_len}]"


def _alias_paths(value: str, aliases: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        path = match.group(0)
        if path not in aliases:
            aliases[path] = f"/REDACTED/project-{len(aliases) + 1}"
        return aliases[path]

    return PATH_PATTERN.sub(replace, value)


def _leak_locations(path: Path) -> list[str]:
    leaks: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            leaks.append(f"line {line_number}: secret pattern")
        if any(pattern.search(line) for pattern in SENSITIVE_PATH_PATTERNS):
            leaks.append(f"line {line_number}: sensitive path pattern")
        if PATH_PATTERN.search(line):
            leaks.append(f"line {line_number}: absolute path")
    return leaks
