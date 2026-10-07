"""Codex runner: run one work item as a Codex turn with the Plane MCP attached.

Uses subprocess isolation to ensure timeout can truly terminate the Codex process,
preventing background MCP calls after timeout. The autonomous config is the
empirically-verified combo: ``Sandbox.workspace_write`` + ``ApprovalMode.auto_review``
(server-side auto-approves tool calls, including MCP — ``deny_all`` would reject them).
"""

from __future__ import annotations

import logging
import multiprocessing
import time
from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .config import Settings
from .models import Comment, WorkItem
from .prompt import render_prompt

log = logging.getLogger(__name__)


def build_config_overrides(settings: Settings) -> tuple[str, ...]:
    """`codex --config k=v` entries: attach the Plane MCP + enable agent network."""
    return (
        f"mcp_servers.plane.command={settings.python_bin}",
        'mcp_servers.plane.args=["-m","plane_symphony.plane_mcp"]',
        f"mcp_servers.plane.env.PLANE_BASE_URL={settings.plane_base_url}",
        f"mcp_servers.plane.env.PLANE_API_TOKEN={settings.plane_token}",
        f"mcp_servers.plane.env.PLANE_WORKSPACE_SLUG={settings.workspace_slug}",
        "sandbox_workspace_write.network_access=true",
    )


def make_workspace(root: str, work_item_id: str) -> str:
    """Create (idempotently) a per-work-item scratch dir under ``root``."""
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in work_item_id)
    path = Path(root).expanduser() / safe
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _codex_worker(
    result_queue: multiprocessing.Queue,
    cfg_dict: dict[str, Any],
    workspace: str,
    work_item_id: str,
    prompt: str,
    existing_thread_id: str | None,
    settings_dict: dict[str, Any],
) -> None:
    """Worker function that runs in subprocess. Puts result in queue.

    Note: The parent process sets this worker's process group via os.setpgid()
    immediately after spawning, eliminating the race condition where this worker's
    setpgrp() might not complete before the parent needs to kill the group.
    """
    try:
        # Reconstruct config and settings (can't pickle them directly)
        from .config import Settings
        settings = Settings(**settings_dict)
        cfg = CodexConfig(
            cwd=cfg_dict["cwd"],
            config_overrides=tuple(cfg_dict["config_overrides"])
        )

        with Codex(cfg) as codex:
            # If no thread_id in memory, try to recover from Codex (inside timeout boundary)
            thread_id_to_use = existing_thread_id
            if not thread_id_to_use:
                try:
                    resp = codex.thread_list(search_term=work_item_id)
                    if resp.data:
                        thread_id_to_use = resp.data[0].id
                        log.info("recovered thread %s from codex for %s", thread_id_to_use, work_item_id)
                except Exception as e:
                    log.warning("thread_list search failed for %s: %s", work_item_id, e)

            # Resume or start thread
            if thread_id_to_use:
                try:
                    thread = codex.thread_resume(
                        thread_id_to_use,
                        cwd=workspace,
                        sandbox=Sandbox.workspace_write,
                        approval_mode=ApprovalMode.auto_review,
                        model=settings.model,
                        base_instructions=settings.prompt_body,
                    )
                    log.info("resumed thread %s for %s", thread_id_to_use, work_item_id)
                except Exception as e:
                    log.warning("thread_resume failed (%s), creating fresh: %s", thread_id_to_use, e)
                    thread = codex.thread_start(
                        sandbox=Sandbox.workspace_write,
                        approval_mode=ApprovalMode.auto_review,
                        cwd=workspace,
                        model=settings.model,
                        base_instructions=settings.prompt_body,
                    )
            else:
                thread = codex.thread_start(
                    sandbox=Sandbox.workspace_write,
                    approval_mode=ApprovalMode.auto_review,
                    cwd=workspace,
                    model=settings.model,
                    base_instructions=settings.prompt_body,
                )

            try:
                thread.set_name(work_item_id)
            except Exception:
                pass  # naming is best-effort

            turn_result = thread.run(prompt)
            result_queue.put({"result": turn_result, "thread_id": thread.id})
    except Exception as e:
        result_queue.put({"error": str(e), "error_type": type(e).__name__})


def run_task(
    settings: Settings,
    work_item: WorkItem,
    comments: list[Comment],
    reason: str,
    store,  # Store protocol with get_latest_thread
    codex_factory: Callable[[CodexConfig], Codex] = Codex,
    _use_subprocess: bool = True,  # Can be set to False for testing with mocks
):
    """Run a single work item to completion; returns (TurnResult, thread_id, timed_out).

    Resumes existing thread if available (symphony-style continuity), otherwise creates fresh.
    Enforces turn_timeout_ms by running in a subprocess that can be terminated on timeout.
    Returns (result, thread_id, False) on success, raises on hard errors.
    If timeout, returns (None, None, True) after killing the subprocess.

    Args:
        _use_subprocess: If False, uses threading mode (for tests with mocks). Production always uses subprocess.
    """
    workspace = make_workspace(settings.workspace_root, work_item.id)
    prompt = render_prompt(settings, work_item, comments, reason)
    cfg = CodexConfig(cwd=workspace, config_overrides=build_config_overrides(settings))

    # Check for existing thread in memory store
    existing_thread_id = store.get_latest_thread(work_item.id)

    # Note: thread_list lookup moved inside worker (protected by timeout)

    # Test mode: use threading (allows mocking codex_factory)
    if not _use_subprocess:
        return _run_with_threading(
            codex_factory, cfg, workspace, work_item.id, prompt,
            existing_thread_id, settings
        )

    # Production: use subprocess isolation
    return _run_with_subprocess(
        cfg, workspace, work_item.id, prompt, existing_thread_id, settings
    )


def _run_with_threading(
    codex_factory,
    cfg: CodexConfig,
    workspace: str,
    work_item_id: str,
    prompt: str,
    existing_thread_id: str | None,
    settings: Settings,
):
    """Threading-based runner (for tests only, cannot truly kill on timeout)."""
    import threading

    result_box = {}

    def _run():
        try:
            with codex_factory(cfg) as codex:
                if existing_thread_id:
                    try:
                        thread = codex.thread_resume(
                            existing_thread_id,
                            cwd=workspace,
                            sandbox=Sandbox.workspace_write,
                            approval_mode=ApprovalMode.auto_review,
                            model=settings.model,
                            base_instructions=settings.prompt_body,
                        )
                        log.info("resumed thread %s for %s", existing_thread_id, work_item_id)
                    except Exception as e:
                        log.warning("thread_resume failed (%s), creating fresh: %s", existing_thread_id, e)
                        thread = codex.thread_start(
                            sandbox=Sandbox.workspace_write,
                            approval_mode=ApprovalMode.auto_review,
                            cwd=workspace,
                            model=settings.model,
                            base_instructions=settings.prompt_body,
                        )
                else:
                    thread = codex.thread_start(
                        sandbox=Sandbox.workspace_write,
                        approval_mode=ApprovalMode.auto_review,
                        cwd=workspace,
                        model=settings.model,
                        base_instructions=settings.prompt_body,
                    )
                try:
                    thread.set_name(work_item_id)
                except Exception:
                    pass
                turn_result = thread.run(prompt)
                result_box["result"] = (turn_result, thread.id)
        except Exception as e:
            result_box["error"] = e

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    timeout_s = settings.turn_timeout_ms / 1000.0
    worker.join(timeout=timeout_s)

    if worker.is_alive():
        log.warning("codex run for %s exceeded %.1fs timeout (threading mode, cannot kill)", work_item_id, timeout_s)
        return (None, None, True)
    if "error" in result_box:
        raise result_box["error"]
    result = result_box.get("result")
    if result:
        return (*result, False)
    return (None, None, False)


def _run_with_subprocess(
    cfg: CodexConfig,
    workspace: str,
    work_item_id: str,
    prompt: str,
    existing_thread_id: str | None,
    settings: Settings,
):
    """Subprocess-based runner (production, can truly kill on timeout)."""
    # Convert settings to dict for pickling
    settings_dict = {
        "plane_base_url": settings.plane_base_url,
        "plane_token": settings.plane_token,
        "workspace_slug": settings.workspace_slug,
        "bot_user_id": settings.bot_user_id,
        "webhook_secret": settings.webhook_secret,
        "webhook_port": settings.webhook_port,
        "python_bin": settings.python_bin,
        "workspace_root": settings.workspace_root,
        "poll_interval_ms": settings.poll_interval_ms,
        "max_concurrent": settings.max_concurrent,
        "turn_timeout_ms": settings.turn_timeout_ms,
        "model": settings.model,
        "trigger_state": settings.trigger_state,
        "working_state": settings.working_state,
        "outcome_done": settings.outcome_done,
        "outcome_review": settings.outcome_review,
        "prompt_body": settings.prompt_body,
        "workflow_path": settings.workflow_path,
    }

    # Convert config to dict (CodexConfig is not picklable)
    cfg_dict = {
        "cwd": cfg.cwd,
        "config_overrides": list(cfg.config_overrides) if cfg.config_overrides else [],
    }

    # Use multiprocessing queue for IPC
    result_queue: multiprocessing.Queue = multiprocessing.Queue()

    process = multiprocessing.Process(
        target=_codex_worker,
        args=(result_queue, cfg_dict, workspace, work_item_id, prompt, existing_thread_id, settings_dict),
    )

    process.start()

    # Parent sets child's process group immediately after spawn (eliminates race)
    # This ensures the pgid is set before we enter the timeout wait, preventing
    # the critical bug where getpgid() could return the parent's pgid if the
    # child's setpgrp() hadn't completed yet.
    try:
        import os
        os.setpgid(process.pid, process.pid)
    except (OSError, AttributeError):
        pass  # Windows doesn't support setpgid, will fall back to process.terminate()

    timeout_s = settings.turn_timeout_ms / 1000.0
    process.join(timeout=timeout_s)

    if process.is_alive():
        # Timeout: terminate the entire process GROUP (set by parent via setpgid above)
        log.warning("codex run for %s exceeded %.1fs timeout, terminating process group", work_item_id, timeout_s)

        try:
            # Try to kill process group first
            import os
            import signal
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGTERM)
            log.info("sent SIGTERM to process group %d", pgid)
        except (ProcessLookupError, PermissionError, AttributeError) as e:
            # Fallback: kill just the process (Windows or process already dead)
            log.warning("process group kill failed (%s), falling back to process.terminate()", e)
            process.terminate()

        # Wait for process to exit after SIGTERM
        process.join(timeout=2)

        if process.is_alive():
            # Still alive after SIGTERM, use SIGKILL on group
            log.warning("codex process group still alive after SIGTERM, sending SIGKILL")
            try:
                import os
                import signal
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                process.kill()  # Fallback
            process.join(timeout=5)

        return (None, None, True)  # timed_out

    # Process finished, get result
    try:
        result_data = result_queue.get(timeout=1)
        if "error" in result_data:
            raise RuntimeError(f"{result_data['error_type']}: {result_data['error']}")
        return (result_data["result"], result_data["thread_id"], False)
    except Exception as e:
        # Queue empty or other error
        if process.exitcode != 0:
            raise RuntimeError(f"Codex subprocess exited with code {process.exitcode}") from e
        raise
