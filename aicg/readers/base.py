from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, Protocol

from ..models import ParsedRecords


@dataclass(frozen=True)
class Capabilities:
    token_usage: bool
    cache_semantics: Literal["disjoint", "subset", "none"]
    tool_events: bool
    turn_status: bool
    session_resume: bool
    background_flag: bool
    native_cost: bool


class ProviderReader(Protocol):
    provider: str

    def discover(self, since, paths: dict[str, Path] | None = None, known_sources: set[str] | None = None) -> Iterator[Path]:
        ...

    def read(self, path: Path) -> ParsedRecords:
        ...

    def capabilities(self) -> Capabilities:
        ...
