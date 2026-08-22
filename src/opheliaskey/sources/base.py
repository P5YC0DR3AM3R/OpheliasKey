"""Common contract for every ingestion source.

Sources do exactly one job: fetch bytes from somewhere and hand them to
`Database.store_raw`. They never write to `orders` or `line_items` — parsing is
a separate stage so that a parser bug is always recoverable by re-parsing
rather than re-fetching.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from ..db.database import Database


@dataclass
class SyncResult:
    source: str
    fetched: int = 0
    new: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    cursor: str | None = None

    def summary(self) -> str:
        parts = [f"{self.source}: {self.fetched} fetched, {self.new} new"]
        if self.skipped:
            parts.append(f"{self.skipped} unchanged")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts)


class Source(Protocol):
    name: str

    def sync(self, db: Database, *, full: bool = False) -> SyncResult: ...


class RateLimiter:
    """Minimal blocking rate limiter. Amazon's published limits are low and
    inconsistently documented, so we stay well under them by default."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.monotonic()
