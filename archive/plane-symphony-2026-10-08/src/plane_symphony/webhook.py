"""Webhook receiver: verify signature, parse issue/issue_comment events.

Signature is HMAC-SHA256 over the *raw request body bytes* keyed by the
workspace webhook secret (compare against ``X-Plane-Signature``). Events whose
actor is the bot itself are dropped (anti-self-loop).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .models import parse_mentioned_user_ids
from .triggers import TriggerEvent

log = logging.getLogger("plane_symphony.webhook")


def _extract_user_id(value) -> str | None:
    """Extract user ID from string or dict {"id": "..."}."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id")
    return None


def verify_signature(secret: bytes, body: bytes, signature_hex: str | None) -> bool:
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex or "")


def parse_event(payload: dict[str, Any], bot_user_id: str) -> TriggerEvent | None:
    """Map a Plane webhook payload to a TriggerEvent, or None if not actionable."""
    event = payload.get("event")
    action = payload.get("action")
    data = payload.get("data") or {}
    activity = payload.get("activity") or {}

    # Extract actor_id: handle both dict {"id": "..."} and string "<user_id>"
    actor = activity.get("actor")
    actor_id = _extract_user_id(actor)  # Works for both formats

    # anti-self-loop: ignore the bot's own actions (check all possible actor fields)
    if actor_id and actor_id == bot_user_id:
        return None
    if _extract_user_id(data.get("created_by")) == bot_user_id:
        return None
    if _extract_user_id(data.get("actor")) == bot_user_id:
        return None

    if event == "issue_comment" and action in ("create", "created"):
        html = data.get("comment_html") or ""
        if bot_user_id in parse_mentioned_user_ids(html):
            wid = data.get("issue") or data.get("work_item") or data.get("issue_id")
            pid = data.get("project")
            if wid and pid:
                return TriggerEvent(
                    work_item_id=wid, project_id=pid, reason="mention",
                    comment_id=data.get("id"), actor_id=actor_id,
                )
        return None

    if event == "issue" and action in ("update", "updated"):
        # any state-field change is a candidate; the orchestrator reconciles the
        # current state against trigger_state before acting.
        if activity.get("field") == "state":
            wid = data.get("id")
            pid = data.get("project")
            if wid and pid:
                return TriggerEvent(work_item_id=wid, project_id=pid, reason="state", actor_id=actor_id)
        return None

    return None


class WebhookServer:
    """Threaded HTTP server that turns Plane webhooks into TriggerEvents."""

    def __init__(
        self,
        port: int,
        secret: str,
        bot_user_id: str,
        on_event: Callable[[TriggerEvent], None],
        path: str = "/webhook",
    ) -> None:
        self.port = port
        self.secret = (secret or "").encode()
        self.bot_user_id = bot_user_id
        self.on_event = on_event
        self.path = path
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _handler_cls(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence default stderr logging
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                if self.path.rstrip("/") != server.path.rstrip("/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                if server.secret:
                    sig = self.headers.get("X-Plane-Signature")
                    if not verify_signature(server.secret, body, sig):
                        log.warning("webhook signature mismatch; rejecting")
                        self.send_response(401)
                        self.end_headers()
                        return
                # Process webhook, then ACK (allows Plane to retry on failure)
                try:
                    payload = json.loads(body or b"{}")
                    # Use _extract_user_id helper for consistent actor extraction
                    activity_actor = (payload.get("activity") or {}).get("actor")
                    actor_id_for_log = _extract_user_id(activity_actor) if activity_actor else None
                    log.info(
                        "webhook received: event=%s action=%s actor=%s",
                        payload.get("event"), payload.get("action"), actor_id_for_log,
                    )
                    ev = parse_event(payload, server.bot_user_id)
                    if ev:
                        log.info("webhook -> %s %s (actor=%s)", ev.reason, ev.work_item_id, ev.actor_id)
                        server.on_event(ev)
                    else:
                        log.info("webhook ignored (not actionable / self-action)")

                    # ACK only after successful processing
                    self.send_response(200)
                    self.end_headers()
                except json.JSONDecodeError as e:
                    log.error("webhook JSON parse failed: %s", e)
                    self.send_response(400)
                    self.end_headers()
                except Exception as e:
                    log.error("webhook processing failed: %s", e)
                    self.send_response(500)
                    self.end_headers()

        return Handler

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self._handler_cls())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        log.info("webhook listening on :%d%s", self.port, self.path)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
