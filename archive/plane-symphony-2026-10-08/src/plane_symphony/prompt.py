"""Render the per-task user prompt for a Codex run.

The static base instructions live in WORKFLOW.md (passed as ``base_instructions``).
This module produces the *specific* turn input: which work item, a readable
snapshot, the trigger reason, and a restatement of the finish policy.
"""

from __future__ import annotations

import html
import re

from .config import Settings
from .models import Comment, WorkItem

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(s: str | None, limit: int = 4000) -> str:
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text).strip()
    return text[:limit]


def render_prompt(settings: Settings, work_item: WorkItem, comments: list[Comment], reason: str) -> str:
    lines: list[str] = []
    trigger = (
        f'state changed to "{settings.trigger_state}"'
        if reason == "state"
        else "you were @mentioned in a comment"
    )
    lines.append(f"You are handling a Plane work item. Trigger: {trigger}.")
    lines.append("")
    lines.append(f"project_id: {work_item.project_id}")
    lines.append(f"work_item_id: {work_item.id}")
    lines.append(f"Title: {work_item.name}")
    desc = _html_to_text(work_item.description_html)
    lines.append("Description:")
    lines.append(desc if desc else "(none)")

    ordered = sorted(comments, key=lambda c: (c.created_at or ""))
    if ordered:
        lines.append("")
        lines.append("Existing comments (oldest first):")
        for c in ordered:
            who = "bot(you)" if c.created_by == settings.bot_user_id else (c.created_by or "user")
            lines.append(f"- [{who}] {_html_to_text(c.comment_html, limit=1500)}")

    lines.append("")
    lines.append(
        f'Outcome states for this project: completed="{settings.outcome_done}", '
        f'needs-human="{settings.outcome_review}".'
    )
    if reason == "mention":
        lines.append(
            "This run was triggered by an @mention: focus on the most recent comment that mentioned "
            "you and address its specific request. Respond with a comment and do NOT change the "
            "state unless the mention explicitly asks you to push the work forward."
        )
    else:
        # state trigger: AI should complete the task and set final state
        lines.append(
            "Do the task now using the `plane` MCP tools. Finish with exactly one summary comment "
            "(what you did / result / problems), then set the state per the policy above."
        )
    return "\n".join(lines)
