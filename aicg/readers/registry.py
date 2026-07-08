from __future__ import annotations

import hashlib
import inspect
import importlib.util
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from .. import __version__
from .base import ProviderReader
from .claude_jsonl import ClaudeJsonlReader
from .codex_jsonl import CodexJsonlReader


@dataclass
class ReaderEntry:
    reader: ProviderReader
    origin: str
    verified: bool
    version: str | None = None
    entry_point: str | None = None


@dataclass
class ProviderLoadError:
    entry_point: str
    module: str
    error_type: str
    message: str


class ReaderRegistry:
    def __init__(self) -> None:
        self._readers: dict[str, ReaderEntry] = {}
        self._load_errors: list[ProviderLoadError] = []

    def register(
        self,
        reader: ProviderReader,
        *,
        origin: str = "builtin",
        verified: bool = True,
        version: str | None = None,
        entry_point: str | None = None,
    ) -> None:
        self._readers[reader.provider] = ReaderEntry(
            reader=reader,
            origin=origin,
            verified=verified,
            version=version,
            entry_point=entry_point,
        )

    def get(self, provider: str) -> ProviderReader:
        try:
            return self._readers[provider].reader
        except KeyError as exc:
            raise ValueError(f"unknown provider: {provider}") from exc

    def selected(self, provider: str) -> list[ProviderReader]:
        if provider == "all":
            return [self._readers[key].reader for key in sorted(self._readers)]
        return [self.get(provider)]

    def rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for provider, entry in sorted(self._readers.items()):
            caps = entry.reader.capabilities()
            rows.append(
                {
                    "provider": provider,
                    "origin": entry.origin,
                    "verified": entry.verified,
                    "version": entry.version,
                    "token_usage": caps.token_usage,
                    "cache_semantics": caps.cache_semantics,
                    "tool_events": caps.tool_events,
                    "turn_status": caps.turn_status,
                    "session_resume": caps.session_resume,
                    "background_flag": caps.background_flag,
                    "native_cost": caps.native_cost,
                }
            )
        return rows

    def add_load_error(self, *, entry_point: str, module: str, error: Exception) -> None:
        self._load_errors.append(
            ProviderLoadError(
                entry_point=entry_point,
                module=module,
                error_type=type(error).__name__,
                message=str(error),
            )
        )

    def load_errors(self) -> list[dict[str, str]]:
        return [
            {
                "entryPoint": item.entry_point,
                "module": item.module,
                "errorType": item.error_type,
                "message": item.message,
            }
            for item in self._load_errors
        ]


def default_registry(
    policy=None,
    *,
    allow_unverified: bool = False,
    verified_plugins: Any = None,
) -> ReaderRegistry:
    registry = ReaderRegistry()
    registry.register(CodexJsonlReader(policy=policy), origin="builtin", verified=True, version=__version__)
    registry.register(ClaudeJsonlReader(policy=policy), origin="builtin", verified=True, version=__version__)
    _load_entry_point_readers(
        registry,
        policy=policy,
        allow_unverified=allow_unverified,
        verified_plugins=verified_plugins or set(),
    )
    return registry


def _load_entry_point_readers(
    registry: ReaderRegistry,
    *,
    policy: Any,
    allow_unverified: bool,
    verified_plugins: Any,
) -> None:
    try:
        entry_points = metadata.entry_points(group="aicg.providers")
    except TypeError:
        entry_points = metadata.entry_points().get("aicg.providers", [])
    for entry_point in entry_points:
        module_name = entry_point.value.split(":", 1)[0]
        verified = entry_point_verified(entry_point, verified_plugins)
        if not verified and not allow_unverified:
            continue
        try:
            obj = entry_point.load()
            reader = instantiate_reader(obj, policy=policy)
        except Exception as exc:
            registry.add_load_error(entry_point=entry_point.value, module=module_name, error=exc)
            continue
        version = None
        dist = getattr(entry_point, "dist", None)
        if dist is not None:
            try:
                version = dist.version
            except Exception:
                version = None
        registry.register(
            reader,
            origin="third-party",
            verified=verified,
            version=version,
            entry_point=entry_point.value,
        )


def entry_point_verified(entry_point: Any, verified_plugins: Any) -> bool:
    module_name = _entry_point_module(entry_point.value)
    legacy, records = _verification_items(verified_plugins)
    if entry_point.value in legacy or module_name in legacy:
        return True
    dist_name, dist_version = _entry_point_distribution(entry_point)
    current_module_hash: str | None = None
    for record in records:
        record_entry_point = str(record.get("entryPoint") or "")
        record_module = str(record.get("module") or "")
        if record_entry_point and record_entry_point != entry_point.value:
            continue
        if not record_entry_point and record_module and record_module != module_name:
            continue
        record_dist = str(record.get("distribution") or "")
        if record_dist and dist_name and record_dist.lower() != dist_name.lower():
            continue
        record_version = str(record.get("version") or "")
        if record_version and dist_version and record_version != dist_version:
            continue
        expected_hash = str(record.get("moduleFileHash") or "")
        if expected_hash:
            if current_module_hash is None:
                current_module_hash = module_file_hash(module_name)
            if current_module_hash != expected_hash:
                continue
        expected_tree_hash = str(record.get("moduleTreeHash") or "")
        if expected_tree_hash and module_tree_hash(module_name) != expected_tree_hash:
            continue
        return True
    return False


def entry_points_for_module(module_name: str) -> list[dict[str, str | None]]:
    try:
        entry_points = metadata.entry_points(group="aicg.providers")
    except TypeError:
        entry_points = metadata.entry_points().get("aicg.providers", [])
    rows = []
    for entry_point in entry_points:
        if _entry_point_module(entry_point.value) != module_name:
            continue
        dist_name, dist_version = _entry_point_distribution(entry_point)
        rows.append(
            {
                "entryPoint": entry_point.value,
                "distribution": dist_name,
                "version": dist_version,
            }
        )
    return rows


def module_file_hash(module_name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        return None
    origin = getattr(spec, "origin", None)
    if not origin or origin in {"built-in", "namespace"}:
        return None
    path = Path(origin)
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def module_tree_hash(module_name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        return None
    locations = getattr(spec, "submodule_search_locations", None)
    if not locations:
        return module_file_hash(module_name)
    files: list[Path] = []
    for location in locations:
        root = Path(location)
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*.py") if path.is_file())
    if not files:
        return module_file_hash(module_name)
    digest = hashlib.sha256()
    for path in sorted(files):
        try:
            relative = path.relative_to(Path(locations[0]))
        except ValueError:
            relative = path.name
        digest.update(str(relative).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        digest.update(b"\0")
    return digest.hexdigest()


def _verification_items(verified_plugins: Any) -> tuple[set[str], list[dict[str, Any]]]:
    if verified_plugins is None:
        return set(), []
    if isinstance(verified_plugins, dict):
        legacy_items = verified_plugins.get("verified", [])
        record_items = verified_plugins.get("records", [])
    else:
        legacy_items = verified_plugins
        record_items = []
    legacy: set[str] = set()
    records: list[dict[str, Any]] = []
    for item in legacy_items or []:
        if isinstance(item, dict):
            records.append(item)
        elif item:
            legacy.add(str(item))
    for item in record_items or []:
        if isinstance(item, dict):
            records.append(item)
        elif item:
            legacy.add(str(item))
    return legacy, records


def _entry_point_module(value: str) -> str:
    return value.split(":", 1)[0]


def _entry_point_distribution(entry_point: Any) -> tuple[str | None, str | None]:
    dist = getattr(entry_point, "dist", None)
    if dist is None:
        return None, None
    name = None
    try:
        name = dist.metadata.get("Name")
    except Exception:
        name = None
    try:
        version = dist.version
    except Exception:
        version = None
    return name, version


def instantiate_reader(obj: Any, *, policy: Any = None) -> ProviderReader:
    if _looks_like_reader(obj):
        return obj
    if inspect.isclass(obj):
        return _call_reader_factory(obj, policy=policy)
    if callable(obj):
        return _call_reader_factory(obj, policy=policy)
    raise TypeError("provider entry point must be a ProviderReader or factory")


def _call_reader_factory(factory: Any, *, policy: Any = None) -> ProviderReader:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        reader = factory()
    else:
        if "policy" in signature.parameters:
            reader = factory(policy=policy)
        else:
            reader = factory()
    if not _looks_like_reader(reader):
        raise TypeError("provider factory did not return a ProviderReader")
    return reader


def _looks_like_reader(value: Any) -> bool:
    return (
        hasattr(value, "provider")
        and callable(getattr(value, "discover", None))
        and callable(getattr(value, "read", None))
        and callable(getattr(value, "capabilities", None))
    )


def jsonl_files_since(root: Path, cutoff, known_sources: set[str] | None = None) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    known_sources = known_sources or set()
    for path in root.rglob("*.jsonl"):
        try:
            if path.is_file() and str(path) not in known_sources and path.stat().st_mtime >= cutoff.timestamp():
                files.append(path)
        except OSError:
            continue
    return sorted(files)
