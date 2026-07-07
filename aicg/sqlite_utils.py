from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TypeVar


SQLITE_MAX_BIND_VARIABLES = 900

T = TypeVar("T")


def chunked(values: Sequence[T] | Iterable[T], size: int = SQLITE_MAX_BIND_VARIABLES) -> Iterator[list[T]]:
    batch: list[T] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
