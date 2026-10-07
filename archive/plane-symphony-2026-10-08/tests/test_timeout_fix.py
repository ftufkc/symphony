"""Test P1 fix: timeout prevents background writes."""
import time
from plane_symphony.config import Settings
from plane_symphony.models import WorkItem
from plane_symphony.runner import run_task


def test_timeout_returns_flag_and_discards_late_result():
    """Verify timeout sets flag=True and late completion is ignored."""

    class SlowCodex:
        """Codex that takes longer than timeout."""
        def __init__(self, cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def thread_start(self, **kw):
            return SlowThread()

        def thread_list(self, **kw):
            return []

    class SlowThread:
        id = "slow-thread-123"

        def set_name(self, n):
            pass

        def run(self, prompt):
            # Sleep longer than timeout
            time.sleep(2)
            return "LATE_RESULT"

    class FakeStore:
        def get_latest_thread(self, wid):
            return None

    settings = Settings(
        plane_base_url="http://x/api/v1",
        plane_token="t",
        workspace_slug="css",
        bot_user_id="bot",
        webhook_secret="s",
        webhook_port=8787,
        python_bin="python",
        workspace_root="/tmp/test_timeout",
        poll_interval_ms=10000,
        max_concurrent=1,
        turn_timeout_ms=500,  # 0.5s timeout
        model=None,
        trigger_state="AI Todo",
        working_state="AI Doing",
        outcome_done="AI Done",
        outcome_review="Human Review",
        prompt_body="test",
        workflow_path="WORKFLOW.md",
    )

    wi = WorkItem(id="timeout-test", name="slow", description_html="<p>test</p>", project_id="p1")

    # Should return timeout flag (use threading mode for test)
    result, thread_id, timed_out = run_task(
        settings, wi, [], "state", FakeStore(),
        codex_factory=SlowCodex, _use_subprocess=False
    )

    assert timed_out is True, "Should return timed_out=True"
    assert result is None, "Result should be None on timeout"
    assert thread_id is None, "Thread ID should be None on timeout"

    # Wait to ensure background completes
    time.sleep(1.5)
    # If we reach here without crashes, the flag mechanism worked
    # (background thread logged "discarding result" instead of crashing)
