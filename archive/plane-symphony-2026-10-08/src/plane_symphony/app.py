"""Entry point: wire config + client + orchestrator + poller + webhook and run.

Run with: ``python -m plane_symphony`` (loads .env then WORKFLOW.md).
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from .config import apply_env_file, load_settings, Settings
from .orchestrator import Orchestrator, recover_orphans
from .plane_client import PlaneClient
from .poller import Poller
from .store import InMemoryStore
from .webhook import WebhookServer

log = logging.getLogger("plane_symphony")


def build(settings: Settings | None = None):
    if settings is None:
        apply_env_file(".env")
        settings = load_settings(os.environ.get("WORKFLOW_PATH", "WORKFLOW.md"))
    client = PlaneClient(settings.plane_base_url, settings.plane_token, settings.workspace_slug)
    bot_id = settings.bot_user_id or client.get_me()["id"]
    settings.bot_user_id = bot_id
    store = InMemoryStore()
    orch = Orchestrator(settings, client, store)
    poller = Poller(settings, client, orch.submit)
    webhook = WebhookServer(settings.webhook_port, settings.webhook_secret, bot_id, orch.submit)
    return settings, client, orch, poller, webhook


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings, client, orch, poller, webhook = build()
    log.info(
        "plane-symphony starting | ws=%s | bot=%s | api=%s | trigger=%r",
        settings.workspace_slug, settings.bot_user_id, settings.plane_base_url, settings.trigger_state,
    )

    orch.start()
    try:
        recover_orphans(client, settings)
    except Exception:  # noqa: BLE001
        log.exception("startup orphan recovery failed")
    poller.start()
    webhook.start()

    stop = threading.Event()

    def _handle(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    try:
        while not stop.is_set():
            stop.wait(1.0)
    finally:
        poller.stop()
        orch.stop()
        webhook.stop()
        client.close()
        log.info("plane-symphony stopped")


if __name__ == "__main__":
    main()
