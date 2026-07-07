from __future__ import annotations

from pathlib import Path

from .base import ProviderReader
from .claude_jsonl import ClaudeJsonlReader
from .codex_jsonl import CodexJsonlReader


class ReaderRegistry:
    def __init__(self) -> None:
        self._readers: dict[str, ProviderReader] = {}

    def register(self, reader: ProviderReader) -> None:
        self._readers[reader.provider] = reader

    def get(self, provider: str) -> ProviderReader:
        try:
            return self._readers[provider]
        except KeyError as exc:
            raise ValueError(f"unknown provider: {provider}") from exc

    def selected(self, provider: str) -> list[ProviderReader]:
        if provider == "all":
            return [self._readers[key] for key in sorted(self._readers)]
        return [self.get(provider)]

    def rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for provider, reader in sorted(self._readers.items()):
            caps = reader.capabilities()
            rows.append(
                {
                    "provider": provider,
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


def default_registry(policy=None) -> ReaderRegistry:
    registry = ReaderRegistry()
    registry.register(CodexJsonlReader(policy=policy))
    registry.register(ClaudeJsonlReader(policy=policy))
    return registry


def jsonl_files_since(root: Path, cutoff, known_sources: set[str] | None = None) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    known_sources = known_sources or set()
    for path in root.rglob("*.jsonl"):
        try:
            if path.is_file() and (str(path) in known_sources or path.stat().st_mtime >= cutoff.timestamp()):
                files.append(path)
        except OSError:
            continue
    return sorted(files)
