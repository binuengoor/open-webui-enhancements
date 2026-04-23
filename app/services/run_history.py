from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Iterable, List

from app.models.internal import RunHistoryEntry


class RecentRunHistory:
    def __init__(self, max_entries: int = 100):
        self.max_entries = max_entries
        self._entries: deque[RunHistoryEntry] = deque(maxlen=max_entries)
        self._lock = Lock()

    def append(self, entry: RunHistoryEntry) -> None:
        with self._lock:
            self._entries.appendleft(entry)

    def list(self, limit: int | None = None) -> List[RunHistoryEntry]:
        with self._lock:
            entries = list(self._entries)
        if limit is None or limit >= len(entries):
            return entries
        return entries[: max(0, limit)]

    def extend(self, entries: Iterable[RunHistoryEntry]) -> None:
        with self._lock:
            for entry in entries:
                self._entries.append(entry)
