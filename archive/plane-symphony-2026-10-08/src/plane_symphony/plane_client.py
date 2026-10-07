"""Thin Plane REST client (self-hosted, ``X-API-Key`` auth).

Base URL is the public API root including ``/api/v1`` (on this deployment:
``http://localhost:8000/api/v1`` — note :8000, not the :3000 frontend).

States are resolved **by name** (case-insensitive) because state ids differ
per project; an ambiguous match (e.g. both "AI Todo" and "AI TODO") raises
rather than guessing.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import httpx

from .models import Comment, Project, State, WorkItem


class StateNotFound(Exception):
    pass


class StateAmbiguous(Exception):
    pass


class PlaneClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        workspace: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace
        self._sleep = sleep
        self._max_retries = max_retries
        self._client = httpx.Client(headers={"X-API-Key": token}, transport=transport, timeout=timeout)
        self._states_cache: dict[str, list[State]] = {}

    # ---- low level ----------------------------------------------------------
    def _ws(self) -> str:
        return f"{self.base_url}/workspaces/{self.workspace}"

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        attempt = 0
        while True:
            r = self._client.request(method, url, **kw)
            if r.status_code == 429 and attempt < self._max_retries:
                retry_after = r.headers.get("retry-after")
                delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
                self._sleep(delay)
                attempt += 1
                continue
            r.raise_for_status()
            return r

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        base_params = dict(params or {})
        base_params.setdefault("per_page", 100)
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            p = dict(base_params)
            if cursor:
                p["cursor"] = cursor
            data = self._request("GET", url, params=p).json()
            if isinstance(data, list):  # defensive: some endpoints may return a bare list
                out.extend(data)
                break
            out.extend(data.get("results", []))
            if data.get("next_page_results") and data.get("next_cursor"):
                cursor = data["next_cursor"]
                continue
            break
        return out

    # ---- identity -----------------------------------------------------------
    def get_me(self) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}/users/me/").json()

    # ---- projects / states --------------------------------------------------
    def list_projects(self) -> list[Project]:
        return [Project.from_api(d) for d in self._paginate(f"{self._ws()}/projects/")]

    def list_states(self, project_id: str, use_cache: bool = True) -> list[State]:
        if use_cache and project_id in self._states_cache:
            return self._states_cache[project_id]
        states = [State.from_api(d) for d in self._paginate(f"{self._ws()}/projects/{project_id}/states/")]
        self._states_cache[project_id] = states
        return states

    def invalidate_states(self, project_id: str | None = None) -> None:
        if project_id is None:
            self._states_cache.clear()
        else:
            self._states_cache.pop(project_id, None)

    def resolve_state_id(self, project_id: str, name: str) -> str:
        target = name.strip().lower()
        matches = [s for s in self.list_states(project_id) if s.name.strip().lower() == target]
        if not matches:
            raise StateNotFound(f"state {name!r} not found in project {project_id}")
        if len(matches) > 1:
            raise StateAmbiguous(f"state {name!r} matches {len(matches)} states in project {project_id}")
        return matches[0].id

    # ---- work items ---------------------------------------------------------
    def get_work_item(self, project_id: str, work_item_id: str) -> WorkItem:
        d = self._request(
            "GET",
            f"{self._ws()}/projects/{project_id}/work-items/{work_item_id}/",
            params={"expand": "state"},
        ).json()
        return WorkItem.from_api(d)

    def iter_work_items(self, project_id: str, per_page: int = 100) -> Iterator[WorkItem]:
        url = f"{self._ws()}/projects/{project_id}/work-items/"
        for d in self._paginate(url, params={"expand": "state", "per_page": per_page}):
            yield WorkItem.from_api(d)

    def set_state(self, project_id: str, work_item_id: str, state_id: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"{self._ws()}/projects/{project_id}/work-items/{work_item_id}/",
            json={"state": state_id},
        ).json()

    # ---- comments -----------------------------------------------------------
    def list_comments(self, project_id: str, work_item_id: str) -> list[Comment]:
        url = f"{self._ws()}/projects/{project_id}/work-items/{work_item_id}/comments/"
        return [Comment.from_api(d) for d in self._paginate(url)]

    def add_comment(self, project_id: str, work_item_id: str, comment_html: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self._ws()}/projects/{project_id}/work-items/{work_item_id}/comments/",
            json={"comment_html": comment_html},
        ).json()

    # ---- raw passthrough (unbounded capability) -----------------------------
    def raw_request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Call any Plane REST endpoint with the bot's credentials.

        ``path`` must be relative to the API base (e.g. ``workspaces/css/projects/``).
        Absolute URLs are rejected to prevent token leakage to external hosts.
        """
        if path.startswith(("http://", "https://")):
            raise ValueError(f"Absolute URL rejected (token leak risk): {path}")
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = self._request(method.upper(), url, params=query, json=body)
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text}

    def close(self) -> None:
        self._client.close()
