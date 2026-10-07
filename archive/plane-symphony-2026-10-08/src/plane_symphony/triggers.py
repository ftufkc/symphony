"""Trigger events fed into the orchestrator from the poller and webhook."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerEvent:
    work_item_id: str
    project_id: str
    reason: str  # "state" | "mention"
    comment_id: str | None = None
    actor_id: str | None = None
