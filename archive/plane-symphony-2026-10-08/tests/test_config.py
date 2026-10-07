import pytest

from plane_symphony.config import load_settings, parse_workflow, load_env_file

WORKFLOW = """---
poll_interval_ms: 9000
max_concurrent: 5
turn_timeout_ms: 600000
model: gpt-5.5
trigger_state: AI Todo
working_state: AI Doing
outcomes:
  done: AI Done
  review: Human Review
---
You are the agent. Do the task described by the work item.
Always leave a summary comment.
"""

ENV = {
    "PLANE_BASE_URL": "http://localhost:8000/api/v1",
    "PLANE_API_TOKEN": "plane_api_x",
    "PLANE_WORKSPACE_SLUG": "css",
    "PLANE_BOT_USER_ID": "bot-1",
    "WEBHOOK_SECRET": "s3cret",
    "WEBHOOK_PORT": "8787",
    "PYTHON_BIN": "/usr/bin/python3",
    "WORKSPACE_ROOT": "/tmp/ws",
}


def _write(tmp_path, text=WORKFLOW):
    p = tmp_path / "WORKFLOW.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_workflow_splits_frontmatter_and_body(tmp_path):
    fm, body = parse_workflow(_write(tmp_path))
    assert fm["poll_interval_ms"] == 9000
    assert fm["outcomes"]["done"] == "AI Done"
    assert body.startswith("You are the agent.")
    assert "summary comment" in body


def test_load_settings_maps_all_fields(tmp_path):
    s = load_settings(_write(tmp_path), env=ENV)
    assert s.plane_base_url == "http://localhost:8000/api/v1"
    assert s.plane_token == "plane_api_x"
    assert s.workspace_slug == "css"
    assert s.bot_user_id == "bot-1"
    assert s.webhook_secret == "s3cret"
    assert s.webhook_port == 8787  # coerced to int
    assert s.poll_interval_ms == 9000
    assert s.max_concurrent == 5
    assert s.model == "gpt-5.5"
    assert s.trigger_state == "AI Todo"
    assert s.working_state == "AI Doing"
    assert s.outcome_done == "AI Done"
    assert s.outcome_review == "Human Review"
    assert s.prompt_body.startswith("You are the agent.")


def test_load_settings_defaults_when_frontmatter_minimal(tmp_path):
    p = tmp_path / "WORKFLOW.md"
    p.write_text("---\n---\nbody only\n", encoding="utf-8")
    s = load_settings(p, env=ENV)
    assert s.trigger_state == "AI Todo"
    assert s.working_state == "AI Doing"
    assert s.outcome_done == "AI Done"
    assert s.outcome_review == "Human Review"
    assert s.poll_interval_ms == 15000
    assert s.model is None
    assert s.prompt_body == "body only"


def test_load_settings_missing_required_env_raises(tmp_path):
    with pytest.raises(ValueError):
        load_settings(_write(tmp_path), env={"PLANE_API_TOKEN": "x", "PLANE_WORKSPACE_SLUG": "y"})


def test_bot_user_id_optional(tmp_path):
    env = dict(ENV)
    del env["PLANE_BOT_USER_ID"]
    s = load_settings(_write(tmp_path), env=env)
    assert s.bot_user_id is None


def test_load_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nA=1\nB = two \n\nBAD_LINE\n", encoding="utf-8")
    d = load_env_file(p)
    assert d == {"A": "1", "B": "two"}
