import os

from plane_symphony.config import Settings
from plane_symphony.models import WorkItem
from plane_symphony import runner


def _settings(workspace_root):
    return Settings(
        plane_base_url="http://localhost:8000/api/v1",
        plane_token="plane_api_x",
        workspace_slug="css",
        bot_user_id="bot-1",
        webhook_secret="s",
        webhook_port=8787,
        python_bin="/usr/bin/python3",
        workspace_root=str(workspace_root),
        poll_interval_ms=15000,
        max_concurrent=3,
        turn_timeout_ms=1000,
        model=None,
        trigger_state="AI Todo",
        working_state="AI Doing",
        outcome_done="AI Done",
        outcome_review="Human Review",
        prompt_body="BASE INSTRUCTIONS",
        workflow_path="WORKFLOW.md",
    )


def test_build_config_overrides_has_mcp_and_network(tmp_path):
    ov = runner.build_config_overrides(_settings(tmp_path))
    joined = "\n".join(ov)
    assert "mcp_servers.plane.command=/usr/bin/python3" in joined
    assert 'mcp_servers.plane.args=["-m","plane_symphony.plane_mcp"]' in joined
    assert "mcp_servers.plane.env.PLANE_API_TOKEN=plane_api_x" in joined
    assert "mcp_servers.plane.env.PLANE_BASE_URL=http://localhost:8000/api/v1" in joined
    assert "sandbox_workspace_write.network_access=true" in joined


def test_make_workspace_sanitizes_and_is_idempotent(tmp_path):
    p1 = runner.make_workspace(str(tmp_path), "abc/../x y")
    p2 = runner.make_workspace(str(tmp_path), "abc/../x y")
    assert p1 == p2
    assert os.path.isdir(p1)
    assert ".." not in os.path.basename(p1) and "/" not in os.path.basename(p1)
    assert os.path.dirname(p1) == str(tmp_path)


def test_run_task_wires_prompt_and_returns_result(tmp_path):
    captured = {}

    class FakeThread:
        id = "thread-123"

        def set_name(self, n):
            captured["named"] = n

        def run(self, prompt, **kw):
            captured["prompt"] = prompt
            return "TURN_RESULT"

    class FakeCodex:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def thread_start(self, **kw):
            captured["thread_start_kwargs"] = kw
            return FakeThread()

        def thread_list(self, **kw):
            # Return mock ThreadListResponse with .data attribute
            class MockResponse:
                data = []
            return MockResponse()

    class FakeStore:
        def get_latest_thread(self, wid):
            return None

    wi = WorkItem(id="w-42", name="task", description_html="<p>hi</p>", project_id="p1")
    result, thread_id, timed_out = runner.run_task(
        _settings(tmp_path), wi, [], "state", FakeStore(),
        codex_factory=FakeCodex, _use_subprocess=False  # Use threading for tests
    )

    assert result == "TURN_RESULT"
    assert thread_id == "thread-123"
    assert timed_out is False
    assert captured["named"] == "w-42"
    assert "work_item_id: w-42" in captured["prompt"]
    # autonomous config wired through
    assert captured["thread_start_kwargs"]["base_instructions"] == "BASE INSTRUCTIONS"
    assert str(captured["thread_start_kwargs"]["sandbox"]) in ("Sandbox.workspace_write", "workspace-write")
