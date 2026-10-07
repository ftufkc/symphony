from plane_symphony.config import Settings
from plane_symphony.models import Comment, WorkItem
from plane_symphony.prompt import render_prompt


def _settings(**over):
    base = dict(
        plane_base_url="http://x/api/v1",
        plane_token="t",
        workspace_slug="css",
        bot_user_id="bot-1",
        webhook_secret="s",
        webhook_port=8787,
        python_bin="python",
        workspace_root="/tmp/ws",
        poll_interval_ms=15000,
        max_concurrent=3,
        turn_timeout_ms=1000,
        model=None,
        trigger_state="AI Todo",
        working_state="AI Doing",
        outcome_done="AI Done",
        outcome_review="Human Review",
        prompt_body="BASE",
        workflow_path="WORKFLOW.md",
    )
    base.update(over)
    return Settings(**base)


def _wi():
    return WorkItem(id="w1", name="Do research", description_html="<p>find <b>X</b></p>", project_id="p1")


def test_render_prompt_includes_ids_title_and_outcomes():
    out = render_prompt(_settings(), _wi(), [], "state")
    assert "project_id: p1" in out
    assert "work_item_id: w1" in out
    assert "Do research" in out
    assert 'completed="AI Done"' in out
    assert 'needs-human="Human Review"' in out
    assert "find X" in out  # html stripped to text


def test_render_prompt_state_reason_mentions_trigger_state():
    out = render_prompt(_settings(), _wi(), [], "state")
    assert "AI Todo" in out


def test_render_prompt_mention_reason_adds_no_state_change_note():
    out = render_prompt(_settings(), _wi(), [], "mention")
    assert "@mention" in out
    assert "do NOT change the state" in out


def test_render_prompt_renders_comments_and_labels_bot():
    comments = [
        Comment(id="c1", comment_html="<p>hi from human</p>", created_by="human-9", created_at="2026-01-01"),
        Comment(id="c2", comment_html="<p>earlier bot note</p>", created_by="bot-1", created_at="2025-12-31"),
    ]
    out = render_prompt(_settings(), _wi(), comments, "mention")
    assert "hi from human" in out and "earlier bot note" in out
    assert "bot(you)" in out  # bot's own comment labeled
    # oldest-first ordering: 2025-12-31 comment appears before 2026-01-01
    assert out.index("earlier bot note") < out.index("hi from human")
