from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from aicg.models import NormalizedSession, NormalizedTurn, ParsedRecords
from aicg.readers.base import Capabilities


class ExampleProviderReader:
    provider = "example"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            token_usage=True,
            cache_semantics="disjoint",
            tool_events=False,
            turn_status=True,
            session_resume=False,
            background_flag=False,
            native_cost=False,
        )

    def discover(self, since, paths: dict[str, Path] | None = None, known_sources: set[str] | None = None):
        if not paths:
            return
        raw_dir = paths.get("example")
        if raw_dir is None:
            return
        for path in sorted(raw_dir.glob("*.jsonl")):
            if known_sources and str(path) in known_sources:
                continue
            yield path

    def read(self, path: Path) -> ParsedRecords:
        sessions: list[NormalizedSession] = []
        turns: list[NormalizedTurn] = []
        unknown_event_types: Counter[str] = Counter()
        malformed = 0
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if row.get("type") == "aicg.future_event" or "session_id" not in row:
                unknown_event_types[str(row.get("type") or "<missing>")] += 1
                continue
            session_id = f"example:{row['session_id']}"
            turn_id = f"{session_id}:turn:{line_no}"
            sessions.append(
                NormalizedSession(
                    id=session_id,
                    provider=self.provider,
                    native_session_id=row["session_id"],
                    project_path=row.get("project_path"),
                    started_at=row.get("started_at"),
                    ended_at=row.get("ended_at"),
                    status=row.get("status"),
                    model=row.get("model"),
                    raw_event_count=1,
                    malformed_line_count=0,
                )
            )
            turns.append(
                NormalizedTurn(
                    id=turn_id,
                    session_id=session_id,
                    provider=self.provider,
                    started_at=row.get("started_at"),
                    ended_at=row.get("ended_at"),
                    status=row.get("status"),
                    model=row.get("model"),
                    input_uncached_tokens=int(row.get("input_tokens") or 0),
                    output_tokens=int(row.get("output_tokens") or 0),
                )
            )
        return ParsedRecords(
            sessions=sessions,
            turns=turns,
            malformed_line_count=malformed,
            source_file=str(path),
            unknown_event_types=unknown_event_types,
        )


def create_provider_reader() -> ExampleProviderReader:
    return ExampleProviderReader()
