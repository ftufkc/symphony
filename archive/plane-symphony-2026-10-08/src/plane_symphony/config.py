"""Settings: env (secrets/runtime) + WORKFLOW.md frontmatter (task policy)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n?---\s*\n?(.*)$", re.DOTALL)


@dataclass
class Settings:
    # --- from environment (secrets / deployment) ---
    plane_base_url: str
    plane_token: str
    workspace_slug: str
    bot_user_id: str | None
    webhook_secret: str
    webhook_port: int
    python_bin: str
    workspace_root: str
    # --- from WORKFLOW.md frontmatter (task policy) ---
    poll_interval_ms: int
    max_concurrent: int
    turn_timeout_ms: int
    model: str | None
    trigger_state: str
    working_state: str
    outcome_done: str
    outcome_review: str
    # --- WORKFLOW.md body (base instructions for the agent) ---
    prompt_body: str
    workflow_path: str


def parse_workflow(path: str | os.PathLike) -> tuple[dict, str]:
    """Split a WORKFLOW.md into (frontmatter dict, markdown body)."""
    text = Path(path).read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    frontmatter = yaml.safe_load(m.group(1)) or {}
    return frontmatter, m.group(2).strip()


def _require(env: Mapping[str, str], key: str) -> str:
    val = env.get(key)
    if not val:
        raise ValueError(f"missing required env var: {key}")
    return val


def load_settings(workflow_path: str | os.PathLike, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    fm, body = parse_workflow(workflow_path)
    outcomes = fm.get("outcomes") or {}
    return Settings(
        plane_base_url=_require(env, "PLANE_BASE_URL"),
        plane_token=_require(env, "PLANE_API_TOKEN"),
        workspace_slug=_require(env, "PLANE_WORKSPACE_SLUG"),
        bot_user_id=(env.get("PLANE_BOT_USER_ID") or None),
        webhook_secret=env.get("WEBHOOK_SECRET", ""),
        webhook_port=int(env.get("WEBHOOK_PORT", "8787")),
        python_bin=env.get("PYTHON_BIN", "python"),
        workspace_root=env.get("WORKSPACE_ROOT", ".tmp/workspaces"),
        poll_interval_ms=int(fm.get("poll_interval_ms", 15000)),
        max_concurrent=int(fm.get("max_concurrent", 3)),
        turn_timeout_ms=int(fm.get("turn_timeout_ms", 1_800_000)),
        model=(fm.get("model") or None),
        trigger_state=fm.get("trigger_state", "AI Todo"),
        working_state=fm.get("working_state", "AI Doing"),
        outcome_done=outcomes.get("done", "AI Done"),
        outcome_review=outcomes.get("review", "Human Review"),
        prompt_body=body,
        workflow_path=str(workflow_path),
    )


def load_env_file(path: str | os.PathLike) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (no interpolation)."""
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def apply_env_file(path: str | os.PathLike) -> None:
    """Load .env into os.environ without overriding already-set vars."""
    for key, value in load_env_file(path).items():
        os.environ.setdefault(key, value)
