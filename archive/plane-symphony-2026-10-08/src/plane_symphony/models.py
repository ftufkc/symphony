"""Domain models for Plane entities + small HTML/markdown helpers.

All ``from_api`` constructors are tolerant: Plane responses vary by ``expand``
and version, so missing fields fall back to sensible defaults rather than
raising.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


@dataclass
class WorkItem:
    id: str
    name: str = ""
    description_html: str = ""
    state_id: str | None = None
    state_name: str | None = None  # only present when ?expand=state
    project_id: str | None = None
    workspace_id: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    sequence_id: int | None = None
    priority: str | None = None
    assignees: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    parent: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "WorkItem":
        state = d.get("state")
        if isinstance(state, dict):
            state_id = state.get("id")
            state_name = state.get("name")
        else:
            state_id = state
            state_name = None
        return cls(
            id=d["id"],
            name=d.get("name") or "",
            description_html=d.get("description_html") or "",
            state_id=state_id,
            state_name=state_name,
            project_id=d.get("project"),
            workspace_id=d.get("workspace"),
            created_by=d.get("created_by"),
            updated_by=d.get("updated_by"),
            sequence_id=d.get("sequence_id"),
            priority=d.get("priority"),
            assignees=list(d.get("assignees") or []),
            labels=list(d.get("labels") or []),
            parent=d.get("parent"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass
class State:
    id: str
    name: str = ""
    group: str | None = None
    color: str | None = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "State":
        return cls(id=d["id"], name=d.get("name") or "", group=d.get("group"), color=d.get("color"))


@dataclass
class Comment:
    id: str
    comment_html: str = ""
    created_by: str | None = None
    created_at: str | None = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Comment":
        return cls(
            id=d["id"],
            comment_html=d.get("comment_html") or "",
            created_by=d.get("created_by"),
            created_at=d.get("created_at"),
        )


@dataclass
class Project:
    id: str
    name: str = ""
    identifier: str | None = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Project":
        return cls(id=d["id"], name=d.get("name") or "", identifier=d.get("identifier"))


class _MentionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.user_ids: list[str] = []

    def _handle(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "mention-component":
            return
        a = {k: v for k, v in attrs}
        if a.get("entity_name") == "user_mention":
            uid = a.get("entity_identifier")
            if uid:
                self.user_ids.append(uid)

    def handle_starttag(self, tag, attrs):
        self._handle(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._handle(tag, attrs)


def parse_mentioned_user_ids(comment_or_description_html: str | None) -> list[str]:
    """Return the user uuids @-mentioned in Plane HTML, de-duped preserving order.

    Plane embeds mentions as
    ``<mention-component entity_name="user_mention" entity_identifier="<uuid>">``.
    """
    if not comment_or_description_html:
        return []
    p = _MentionParser()
    p.feed(comment_or_description_html)
    seen: set[str] = set()
    ordered: list[str] = []
    for uid in p.user_ids:
        if uid not in seen:
            seen.add(uid)
            ordered.append(uid)
    return ordered


def markdown_to_comment_html(text: str) -> str:
    """Convert plain text / light markdown into Plane ``comment_html``.

    Minimal + safe: HTML-escape, split on blank lines into ``<p>`` paragraphs,
    and turn single newlines into ``<br>``. Plane sanitizes server-side anyway;
    the goal is readable paragraphs without breaking on quotes/newlines.
    """
    if text is None:
        return ""
    blocks = re.split(r"\n\s*\n", text.strip())
    parts: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        escaped = html.escape(block).replace("\n", "<br>")
        parts.append(f"<p>{escaped}</p>")
    return "".join(parts) if parts else "<p></p>"
