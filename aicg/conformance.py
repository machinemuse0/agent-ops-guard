from __future__ import annotations

import importlib
import json
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .models import ParsedRecords
from .readers.base import Capabilities, ProviderReader
from .readers.registry import instantiate_reader
from .util import SECRET_PATTERNS


CONFORMANCE_VERSION = 2


def load_reader_from_module(module_or_package: str) -> ProviderReader:
    module = importlib.import_module(module_or_package)
    for name in ("create_provider_reader", "provider_reader", "PROVIDER_READER"):
        if hasattr(module, name):
            return instantiate_reader(getattr(module, name))
    for value in vars(module).values():
        if isinstance(value, type) and hasattr(value, "capabilities") and hasattr(value, "read"):
            return instantiate_reader(value)
    raise ValueError(f"module does not expose a provider reader: {module_or_package}")


def verify_reader(reader: ProviderReader, fixtures: Path | None = None) -> dict[str, Any]:
    checks = []
    checks.append(_check("provider.name", bool(getattr(reader, "provider", None)), "provider name is present"))
    try:
        caps = reader.capabilities()
    except Exception as exc:
        caps = None
        checks.append(
            _check(
                "provider.capabilities",
                False,
                "capabilities failed",
                errorType=type(exc).__name__,
            )
        )
    else:
        checks.append(
            _check(
                "provider.capabilities",
                isinstance(caps, Capabilities),
                "capabilities returns Capabilities",
            )
        )
    requires_interrupted_fixture = isinstance(caps, Capabilities) and caps.turn_status
    interrupted_fixture_seen = False
    interrupted_fixture_passed = False
    fixture_paths = _fixture_paths(reader, fixtures)
    checks.append(_check("fixtures.present", bool(fixture_paths), "at least one fixture is available"))
    for path in fixture_paths:
        fixture_checks, fixture_interrupted = _verify_fixture(reader, path)
        checks.extend(fixture_checks)
        if _is_interrupted_fixture(path):
            interrupted_fixture_seen = True
            interrupted_fixture_passed = interrupted_fixture_passed or fixture_interrupted
    if requires_interrupted_fixture:
        checks.append(
            _check(
                "fixtures.interrupted_semantics",
                interrupted_fixture_seen and interrupted_fixture_passed,
                "interrupted/truncated fixture produces interrupted status",
            )
        )
    passed = all(check["status"] == "pass" for check in checks)
    return {
        "schemaVersion": CONFORMANCE_VERSION,
        "provider": getattr(reader, "provider", None),
        "passed": passed,
        "checks": checks,
    }


def render_conformance_markdown(model: dict[str, Any]) -> str:
    lines = [
        "# AgentOps Guard Provider Conformance",
        "",
        f"- Provider: `{model.get('provider') or 'unknown'}`",
        f"- Passed: {str(bool(model.get('passed'))).lower()}",
        "",
        "## Checks",
        "",
    ]
    for check in model.get("checks") or []:
        lines.append(f"- `{check['id']}` [{check['status']}]: {check['summary']}")
    return "\n".join(lines)


def render_conformance_json(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True)


def _verify_fixture(reader: ProviderReader, path: Path) -> tuple[list[dict[str, Any]], bool]:
    checks = []
    try:
        first = reader.read(path)
        second = reader.read(path)
    except Exception as exc:
        return [_check(f"fixture.{path.name}.read", False, "fixture read failed", errorType=type(exc).__name__)], False
    first_is_records = isinstance(first, ParsedRecords)
    checks.append(
        _check(
            f"fixture.{path.name}.type",
            first_is_records,
            "read returns ParsedRecords",
        )
    )
    if not first_is_records:
        return checks, False
    checks.append(
        _check(
            f"fixture.{path.name}.idempotent",
            _stable_records(first) == _stable_records(second),
            "read is deterministic",
        )
    )
    checks.append(
        _check(
            f"fixture.{path.name}.non_empty",
            bool(first.sessions or first.turns or first.tool_events),
            "fixture produces at least one normalized record",
        )
    )
    checks.append(
        _check(
            f"fixture.{path.name}.provider_match",
            _providers_match(reader, first),
            "normalized records use the provider name",
        )
    )
    checks.append(
        _check(
            f"fixture.{path.name}.token_buckets",
            all(_tokens_ok(turn) for turn in first.turns),
            "token buckets are non-negative and reasoning is subset of output",
        )
    )
    payload = json.dumps(_stable_records(first), ensure_ascii=False, sort_keys=True)
    checks.append(
        _check(
            f"fixture.{path.name}.no_secret_patterns",
            not _has_known_secret(payload),
            "normalized records do not contain known secret patterns",
        )
    )
    checks.extend(_verify_bad_line_resilience(reader, path, first))
    checks.extend(_verify_unknown_event_observability(reader, path, first))
    return checks, _has_interrupted_status(first)


def _verify_bad_line_resilience(reader: ProviderReader, path: Path, first: ParsedRecords) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aicg-provider-fixture-") as tmpdir:
        appended = Path(tmpdir) / path.name
        appended.write_bytes(path.read_bytes() + b'\n{"bad"\n')
        try:
            records = reader.read(appended)
        except Exception as exc:
            return [
                _check(
                    f"fixture.{path.name}.bad_line_resilience",
                    False,
                    "reader tolerates appended malformed JSONL line",
                    errorType=type(exc).__name__,
                )
            ]
    checks.append(
        _check(
            f"fixture.{path.name}.bad_line_resilience",
            isinstance(records, ParsedRecords),
            "reader tolerates appended malformed JSONL line",
        )
    )
    if isinstance(records, ParsedRecords):
        checks.append(
            _check(
                f"fixture.{path.name}.append_identity_stability",
                _record_ids(records) == _record_ids(first),
                "existing normalized ids remain stable after append",
            )
        )
        checks.append(
            _check(
                f"fixture.{path.name}.malformed_count",
                int(records.malformed_line_count or 0) >= int(first.malformed_line_count or 0) + 1,
                "malformed line count increases for appended bad line",
            )
        )
    return checks


def _verify_unknown_event_observability(reader: ProviderReader, path: Path, first: ParsedRecords) -> list[dict[str, Any]]:
    check_id = f"fixture.{path.name}.unknown_event_observability"
    event_type = "aicg.future_event"
    baseline = int((first.unknown_event_types or {}).get(event_type, 0))
    with tempfile.TemporaryDirectory(prefix="aicg-provider-fixture-") as tmpdir:
        appended = Path(tmpdir) / path.name
        appended.write_bytes(
            path.read_bytes()
            + b'\n{"type":"aicg.future_event","timestamp":"2026-07-08T00:00:00Z","payload":{"future":true},"futureField":true}\n'
        )
        try:
            records = reader.read(appended)
        except Exception as exc:
            return [
                _check(
                    check_id,
                    False,
                    "reader counts unknown event types without failing",
                    errorType=type(exc).__name__,
                )
            ]
    count = int((records.unknown_event_types or {}).get(event_type, 0)) if isinstance(records, ParsedRecords) else 0
    return [
        _check(
            check_id,
            count >= baseline + 1,
            "reader counts unknown event types without failing",
            observed=count,
            baseline=baseline,
        )
    ]


def _fixture_paths(reader: ProviderReader, fixtures: Path | None) -> list[Path]:
    if fixtures is not None:
        return sorted(path for path in fixtures.glob("*.jsonl") if path.is_file())
    declared = getattr(reader, "conformance_fixtures", None)
    if declared:
        return sorted(Path(path) for path in declared)
    return []


def _tokens_ok(turn: Any) -> bool:
    return (
        int(getattr(turn, "input_uncached_tokens", 0) or 0) >= 0
        and int(getattr(turn, "cache_read_input_tokens", 0) or 0) >= 0
        and int(getattr(turn, "cache_creation_input_tokens", 0) or 0) >= 0
        and int(getattr(turn, "output_tokens", 0) or 0) >= 0
        and int(getattr(turn, "reasoning_output_tokens", 0) or 0) <= int(getattr(turn, "output_tokens", 0) or 0)
    )


def _is_interrupted_fixture(path: Path) -> bool:
    name = path.name.lower()
    return "interrupted" in name or "truncated" in name


def _has_interrupted_status(records: ParsedRecords) -> bool:
    for item in (*records.sessions, *records.turns):
        if getattr(item, "status", None) == "interrupted":
            return True
    return False


def _has_known_secret(payload: str) -> bool:
    return any(pattern.search(payload) for pattern in SECRET_PATTERNS)


def _record_ids(records: ParsedRecords) -> dict[str, list[str]]:
    return {
        "sessions": [str(getattr(item, "id", "")) for item in records.sessions],
        "turns": [str(getattr(item, "id", "")) for item in records.turns],
        "tool_events": [str(getattr(item, "id", "")) for item in records.tool_events],
        "policy_findings": [str(getattr(item, "id", "")) for item in records.policy_findings],
    }


def _providers_match(reader: ProviderReader, records: ParsedRecords) -> bool:
    provider = str(getattr(reader, "provider", ""))
    for collection in (records.sessions, records.turns, records.tool_events, records.policy_findings):
        for item in collection:
            item_provider = getattr(item, "provider", provider)
            if item_provider != provider:
                return False
    return True


def _stable_records(records: ParsedRecords) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key in ("sessions", "turns", "tool_events"):
        items = []
        for item in getattr(records, key):
            item_dict = asdict(item) if is_dataclass(item) else dict(item)
            item_dict.pop("created_at", None)
            items.append(item_dict)
        data[key] = items
    data["malformed_line_count"] = records.malformed_line_count
    data["source_file_hash"] = records.source_file_hash
    data["unknown_event_types"] = dict(records.unknown_event_types or {})
    data["unrecognized_field_ratio"] = records.unrecognized_field_ratio
    data["unrecognized_field_count"] = records.unrecognized_field_count
    data["total_field_count"] = records.total_field_count
    return data


def _check(check_id: str, passed: bool, summary: str, **extra: Any) -> dict[str, Any]:
    return {"id": check_id, "status": "pass" if passed else "fail", "summary": summary, **extra}
