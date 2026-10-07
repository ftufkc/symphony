"""Plane MCP server — the agent's only channel to Plane.

Launched by Codex as a subprocess (see runner). Credentials come from this
process's own environment (injected via ``mcp_servers.plane.env.*``):
``PLANE_BASE_URL``, ``PLANE_API_TOKEN``, ``PLANE_WORKSPACE_SLUG``.

Exposes curated convenience tools plus an unbounded ``plane_request`` raw
passthrough (analogous to symphony's ``linear_graphql``) so the agent's
capability is never capped by the tool surface.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from plane_symphony.models import markdown_to_comment_html
from plane_symphony.plane_client import PlaneClient

mcp = FastMCP("plane")

_client: PlaneClient | None = None


def _c() -> PlaneClient:
    global _client
    if _client is None:
        _client = PlaneClient(
            base_url=os.environ["PLANE_BASE_URL"],
            token=os.environ["PLANE_API_TOKEN"],
            workspace=os.environ["PLANE_WORKSPACE_SLUG"],
        )
    return _client


def _wi_dict(wi) -> dict[str, Any]:
    return {
        "id": wi.id,
        "name": wi.name,
        "description_html": wi.description_html,
        "state_id": wi.state_id,
        "state_name": wi.state_name,
        "project_id": wi.project_id,
        "created_by": wi.created_by,
        "sequence_id": wi.sequence_id,
        "priority": wi.priority,
        "assignees": wi.assignees,
        "labels": wi.labels,
    }


@mcp.tool()
def get_work_item(project_id: str, work_item_id: str) -> dict:
    """Read a Plane work item (name, description_html, state, ...)."""
    return _wi_dict(_c().get_work_item(project_id, work_item_id))


@mcp.tool()
def list_comments(project_id: str, work_item_id: str) -> list:
    """List comments on a work item (most-recent context for the task)."""
    return [
        {"id": c.id, "comment_html": c.comment_html, "created_by": c.created_by, "created_at": c.created_at}
        for c in _c().list_comments(project_id, work_item_id)
    ]


@mcp.tool()
def add_comment(project_id: str, work_item_id: str, markdown: str) -> dict:
    """Post a comment on a work item. `markdown` is converted to Plane comment HTML."""
    return _c().add_comment(project_id, work_item_id, markdown_to_comment_html(markdown))


@mcp.tool()
def set_state(project_id: str, work_item_id: str, state_name: str) -> dict:
    """Move a work item to the state with the given name (resolved per-project, case-insensitive)."""
    state_id = _c().resolve_state_id(project_id, state_name)
    return _c().set_state(project_id, work_item_id, state_id)


@mcp.tool()
def list_states(project_id: str) -> list:
    """List the available states (name + group) for a project."""
    return [{"id": s.id, "name": s.name, "group": s.group} for s in _c().list_states(project_id, use_cache=False)]


@mcp.tool()
def search_work_items(query: str) -> Any:
    """Search work items across the workspace by text."""
    return _c().raw_request(
        "GET", f"workspaces/{_c().workspace}/work-items/search/", query={"search": query}
    )


@mcp.tool()
def get_me() -> dict:
    """Return the bot's own Plane user (id, email, ...)."""
    return _c().get_me()


@mcp.tool()
def plane_request(method: str, path: str, query: dict | None = None, body: dict | None = None) -> Any:
    """Raw authenticated call to any Plane REST endpoint.

    path: relative to /api/v1 (e.g. "workspaces/css/projects/"). Absolute URLs rejected.
    Use this for anything the convenience tools don't cover: creating sub-items,
    uploading attachments, editing arbitrary fields, etc.
    """
    return _c().raw_request(method, path, query=query, body=body)
