"""Poller: the state-trigger backbone. Scans every project for work items in the
trigger state and submits them. Self-healing — a missed item is re-found next scan.
"""

from __future__ import annotations

import logging
import threading
import time

from .config import Settings
from .plane_client import PlaneClient, StateNotFound
from .triggers import TriggerEvent

log = logging.getLogger("plane_symphony.poller")


class Poller:
    def __init__(self, settings: Settings, client: PlaneClient, submit) -> None:
        self.settings = settings
        self.client = client
        self.submit = submit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick = 0  # deterministic jitter source (avoid Math.random-style nondeterminism)

    def scan_once(self) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        for project in self.client.list_projects():
            self.client.invalidate_states(project.id)  # states may have just been created
            try:
                trigger_id = self.client.resolve_state_id(project.id, self.settings.trigger_state)
            except StateNotFound:
                continue  # project has no trigger state -> not served
            except Exception:  # noqa: BLE001
                log.exception("state resolve failed for project %s", project.id)
                continue
            try:
                for wi in self.client.iter_work_items(project.id):
                    if wi.state_id == trigger_id:
                        events.append(TriggerEvent(work_item_id=wi.id, project_id=project.id, reason="state"))
            except Exception:  # noqa: BLE001
                log.exception("work-item scan failed for project %s", project.id)
        return events

    def _loop(self) -> None:
        interval = self.settings.poll_interval_ms / 1000.0
        while not self._stop.is_set():
            try:
                for ev in self.scan_once():
                    self.submit(ev)
            except Exception:  # noqa: BLE001
                log.exception("poll scan failed")
            # small deterministic jitter so multiple instances don't sync up
            self._tick += 1
            jitter = (self._tick % 5) * 0.2
            self._stop.wait(interval + jitter)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("poller started (every %sms)", self.settings.poll_interval_ms)

    def stop(self) -> None:
        self._stop.set()
