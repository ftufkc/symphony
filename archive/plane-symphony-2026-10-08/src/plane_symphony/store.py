"""Persistence seam. v1 is in-memory only (Plane is the source of truth).

A future SQLite implementation of the same Protocol adds durable mention
de-dup and run history without changing callers.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol


@dataclass
class RunRecord:
    work_item_id: str
    project_id: str
    reason: str
    status: str  # "ok" | "failed"
    detail: str = ""
    thread_id: str | None = None  # Codex thread ID for resume


class Store(Protocol):
    def is_mention_handled(self, comment_id: str) -> bool: ...
    def mark_mention_handled(self, comment_id: str) -> None: ...
    def record_run(self, run: RunRecord) -> None: ...
    def get_latest_thread(self, work_item_id: str) -> str | None: ...
    def record_claim_failure(self, work_item_id: str) -> None: ...
    def should_retry_claim(self, work_item_id: str, backoff_seconds: float = 300) -> bool: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mentions: set[str] = set()
        self.runs: list[RunRecord] = []
        self._claim_failures: dict[str, float] = {}  # work_item_id → timestamp

    def is_mention_handled(self, comment_id: str) -> bool:
        with self._lock:
            return comment_id in self._mentions

    def mark_mention_handled(self, comment_id: str) -> None:
        with self._lock:
            self._mentions.add(comment_id)

    def record_run(self, run: RunRecord) -> None:
        with self._lock:
            self.runs.append(run)

    def get_latest_thread(self, work_item_id: str) -> str | None:
        """Return the most recent successful thread_id for this work item, or None."""
        with self._lock:
            candidates = [r for r in self.runs if r.work_item_id == work_item_id and r.thread_id]
            return candidates[-1].thread_id if candidates else None

    def record_claim_failure(self, work_item_id: str) -> None:
        """Record that claiming this item failed (to avoid spam)."""
        import time
        with self._lock:
            self._claim_failures[work_item_id] = time.time()

    def should_retry_claim(self, work_item_id: str, backoff_seconds: float = 300) -> bool:
        """Check if enough time has passed since last claim failure."""
        import time
        with self._lock:
            last_failure = self._claim_failures.get(work_item_id)
            if last_failure is None:
                return True  # Never failed before
            return (time.time() - last_failure) > backoff_seconds
