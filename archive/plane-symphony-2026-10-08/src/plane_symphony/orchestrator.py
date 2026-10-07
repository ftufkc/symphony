"""Orchestrator: the brain. Consumes TriggerEvents from a queue with a fixed pool
of worker threads (pool size == max_concurrent), de-dupes via an in-memory claim
set, reconciles against Plane's current state, dispatches the Codex run, and
guarantees the work item never gets stranded.

State-trigger lifecycle: claim -> set working_state (AI Doing) -> Codex run (the
agent sets the final state + summary comment per WORKFLOW) -> safety-net: if the
agent left it in working_state, move to review. Failure -> error comment + review.

Mention-trigger: no state change; run for a reply; mark the comment handled.
"""

from __future__ import annotations

import logging
import queue
import threading

from .config import Settings
from .models import markdown_to_comment_html
from .plane_client import PlaneClient
from .runner import run_task as default_run_task
from .store import RunRecord, Store
from .triggers import TriggerEvent

log = logging.getLogger("plane_symphony.orchestrator")


def _state_is(name: str | None, target: str) -> bool:
    return (name or "").strip().lower() == target.strip().lower()


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        client: PlaneClient,
        store: Store,
        run_task=default_run_task,
    ) -> None:
        self.settings = settings
        self.client = client
        self.store = store
        self._run_task = run_task
        self._queue: "queue.Queue[TriggerEvent]" = queue.Queue()
        self._claimed: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []

    # ---- public API ---------------------------------------------------------
    def submit(self, event: TriggerEvent) -> None:
        self._queue.put(event)

    def start(self) -> None:
        for i in range(max(1, self.settings.max_concurrent)):
            t = threading.Thread(target=self._worker, name=f"orch-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        log.info("orchestrator started (%d workers)", len(self._workers))

    def stop(self) -> None:
        self._stop.set()

    # ---- internals ----------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.handle(event)
            except Exception:  # noqa: BLE001
                log.exception("error handling event %s", event)
            finally:
                self._queue.task_done()

    def handle(self, event: TriggerEvent) -> None:
        # fast de-dup (already being worked)
        with self._lock:
            if event.work_item_id in self._claimed:
                return
        # mention de-dup (re-derivable from Plane; in-memory for this session)
        if event.reason == "mention" and event.comment_id and self.store.is_mention_handled(event.comment_id):
            return

        # reconcile: re-read current state from Plane (Plane is the source of truth)
        try:
            wi = self.client.get_work_item(event.project_id, event.work_item_id)
        except Exception:  # noqa: BLE001
            log.exception("reconcile read failed for %s", event.work_item_id)
            return
        if event.reason == "state" and not _state_is(wi.state_name, self.settings.trigger_state):
            return  # no longer eligible (someone/we moved it)

        # claim
        with self._lock:
            if event.work_item_id in self._claimed:
                return
            self._claimed.add(event.work_item_id)
        try:
            self._dispatch(event, wi)
        finally:
            with self._lock:
                self._claimed.discard(event.work_item_id)

    def _dispatch(self, event: TriggerEvent, wi) -> None:
        pid, wid = event.project_id, event.work_item_id
        comments = []
        try:
            comments = self.client.list_comments(pid, wid)
        except Exception:  # noqa: BLE001
            log.exception("list_comments failed for %s", wid)

        # state trigger: show progress by moving to working_state
        if event.reason == "state":
            claimed = self._safe_set_state(pid, wid, self.settings.working_state)
            if not claimed:
                log.error("failed to claim %s (set working_state), aborting run", wid)

                # Deduplicate: only comment if we haven't failed recently
                if self.store.should_retry_claim(wid, backoff_seconds=300):
                    try:
                        html = markdown_to_comment_html(
                            "⚠️ Failed to claim this task: could not set state to AI Doing.\n\n"
                            "I will keep trying every poll cycle, but won't spam comments for 5 minutes."
                        )
                        self.client.add_comment(pid, wid, html)
                    except Exception:  # noqa: BLE001
                        log.exception("failed to post claim-failure comment for %s", wid)
                    self.store.record_claim_failure(wid)
                else:
                    log.info("skipping duplicate claim failure comment for %s", wid)

                self.store.record_run(RunRecord(wid, pid, event.reason, "claim_failed", "set_state failed", thread_id=None))
                return

        thread_id = None
        timed_out = False
        try:
            turn_result, thread_id, timed_out = self._run_task(self.settings, wi, comments, event.reason, self.store)
            if timed_out:
                # Timeout: runner discarded result, treat as failure
                log.warning("task timed out for %s, treating as failure", wid)
                exc = TimeoutError(f"Task exceeded {self.settings.turn_timeout_ms}ms timeout")
                self._fail(pid, wid, exc, event.reason)
                self.store.record_run(RunRecord(wid, pid, event.reason, "timeout", str(exc), thread_id=None))
                return
        except Exception as exc:  # noqa: BLE001
            log.exception("codex run failed for %s", wid)
            self._fail(pid, wid, exc, event.reason)
            self.store.record_run(RunRecord(wid, pid, event.reason, "failed", str(exc), thread_id=None))
            return

        # mention de-dup marker
        if event.reason == "mention" and event.comment_id:
            self.store.mark_mention_handled(event.comment_id)

        # safety net: state trigger must not be left stranded in working_state
        if event.reason == "state":
            try:
                cur = self.client.get_work_item(pid, wid)
                if _state_is(cur.state_name, self.settings.working_state):
                    log.warning("%s left in working_state; moving to review", wid)
                    self.client.add_comment(
                        pid, wid,
                        markdown_to_comment_html(
                            "(plane-symphony) The run finished without setting a final state; "
                            "moving to review for a human to check."
                        ),
                    )
                    self._safe_set_state(pid, wid, self.settings.outcome_review)
            except Exception:  # noqa: BLE001
                log.exception("safety-net check failed for %s", wid)

        self.store.record_run(RunRecord(wid, pid, event.reason, "ok", thread_id=thread_id))

    def _fail(self, pid: str, wid: str, exc: Exception, reason: str) -> None:
        """Post failure comment; only change state if triggered by state (not mention)."""
        try:
            self.client.add_comment(
                pid, wid,
                markdown_to_comment_html(f"⚠️ (plane-symphony) AI run failed and needs a human:\n\n{exc}"),
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to post failure comment for %s", wid)
        # mention failures leave the state unchanged (user may have just been asking a question)
        if reason == "state":
            self._safe_set_state(pid, wid, self.settings.outcome_review)

    def _safe_set_state(self, pid: str, wid: str, state_name: str) -> bool:
        """Set state, return True on success, False on failure (logs exception)."""
        try:
            sid = self.client.resolve_state_id(pid, state_name)
            self.client.set_state(pid, wid, sid)
            return True
        except Exception:  # noqa: BLE001
            log.exception("set_state %r failed for %s", state_name, wid)
            return False


def recover_orphans(client: PlaneClient, settings: Settings) -> int:
    """On startup, reset orphan working_state items back to trigger_state so they
    get re-picked (single-process => any working_state item is a crashed run).
    Returns the number reset.
    """
    n = 0
    for project in client.list_projects():
        client.invalidate_states(project.id)
        try:
            working_id = client.resolve_state_id(project.id, settings.working_state)
            trigger_id = client.resolve_state_id(project.id, settings.trigger_state)
        except Exception:  # noqa: BLE001
            continue
        try:
            for wi in client.iter_work_items(project.id):
                if wi.state_id == working_id:
                    client.set_state(project.id, wi.id, trigger_id)
                    n += 1
        except Exception:  # noqa: BLE001
            log.exception("orphan recovery scan failed for %s", project.id)
    if n:
        log.info("recovered %d orphan work item(s) back to %s", n, settings.trigger_state)
    return n
