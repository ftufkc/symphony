from plane_symphony.config import Settings
from plane_symphony.models import Project, WorkItem
from plane_symphony.plane_client import StateNotFound
from plane_symphony.orchestrator import Orchestrator
from plane_symphony.poller import Poller
from plane_symphony.store import InMemoryStore
from plane_symphony.triggers import TriggerEvent


def settings(**over):
    base = dict(
        plane_base_url="http://x/api/v1", plane_token="t", workspace_slug="css", bot_user_id="bot-1",
        webhook_secret="s", webhook_port=8787, python_bin="python", workspace_root="/tmp/ws",
        poll_interval_ms=15000, max_concurrent=2, turn_timeout_ms=1000, model=None,
        trigger_state="AI Todo", working_state="AI Doing", outcome_done="AI Done",
        outcome_review="Human Review", prompt_body="BASE", workflow_path="WORKFLOW.md",
    )
    base.update(over)
    return Settings(**base)


class FakeClient:
    """Minimal Plane client for orchestrator tests; tracks state + side effects."""

    def __init__(self, state_name="AI Todo"):
        self.state_name = state_name
        self.set_state_calls: list[str] = []
        self.comments: list[str] = []
        self._states = {"ai todo": "T", "ai doing": "D", "human review": "R", "ai done": "DONE"}

    def get_work_item(self, pid, wid):
        return WorkItem(id=wid, name="x", project_id=pid, state_name=self.state_name,
                        state_id=self._states.get(self.state_name.strip().lower()))

    def list_comments(self, pid, wid):
        return []

    def resolve_state_id(self, pid, name):
        k = name.strip().lower()
        if k not in self._states:
            raise StateNotFound(name)
        return self._states[k]

    def set_state(self, pid, wid, sid):
        self.set_state_calls.append(sid)
        rev = {v: k for k, v in self._states.items()}
        self.state_name = rev.get(sid, self.state_name)  # reflect new state by name
        return {}

    def add_comment(self, pid, wid, html):
        self.comments.append(html)
        return {}


def make_run(record, client=None, final_state=None, exc=None):
    def run(s, wi, comments, reason, store):
        record.append((wi.id, reason))
        if exc:
            raise exc
        if final_state and client:
            sid = client.resolve_state_id(wi.project_id, final_state)
            client.set_state(wi.project_id, wi.id, sid)
        return (None, "mock-thread-id", False)  # (turn_result, thread_id, timed_out)
    return run



def test_state_trigger_sets_working_then_runs_then_agent_finishes():
    c = FakeClient(state_name="AI Todo")
    rec = []
    orch = Orchestrator(settings(), c, InMemoryStore(), run_task=make_run(rec, c, final_state="AI Done"))
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert rec == [("w1", "state")]
    # working_state set on claim, then agent set AI Done
    assert c.set_state_calls == ["D", "DONE"]
    assert c.state_name == "ai done"


def test_state_trigger_skips_when_not_in_trigger_state():
    c = FakeClient(state_name="Done")  # reconcile finds it's not AI Todo
    rec = []
    orch = Orchestrator(settings(), c, InMemoryStore(), run_task=make_run(rec, c))
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert rec == []
    assert c.set_state_calls == []


def test_dedup_when_already_claimed():
    c = FakeClient()
    rec = []
    orch = Orchestrator(settings(), c, InMemoryStore(), run_task=make_run(rec, c, final_state="AI Done"))
    orch._claimed.add("w1")  # pretend in-flight
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert rec == []


def test_claim_released_after_handle():
    c = FakeClient()
    orch = Orchestrator(settings(), c, InMemoryStore(), run_task=make_run([], c, final_state="AI Done"))
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert "w1" not in orch._claimed


def test_state_trigger_safety_net_moves_to_review_if_left_working():
    c = FakeClient(state_name="AI Todo")
    rec = []
    # run_task does NOT set a final state (simulating a misbehaving agent)
    orch = Orchestrator(settings(), c, InMemoryStore(), run_task=make_run(rec, c, final_state=None))
    orch.handle(TriggerEvent("w1", "p1", "state"))
    # working set, then safety net -> review
    assert c.set_state_calls == ["D", "R"]
    assert len(c.comments) == 1  # safety-net note


def test_mention_does_not_change_state_and_dedupes():
    c = FakeClient(state_name="Done")
    rec = []
    store = InMemoryStore()
    orch = Orchestrator(settings(), c, store, run_task=make_run(rec, c))
    ev = TriggerEvent("w1", "p1", "mention", comment_id="cm1", actor_id="human-9")
    orch.handle(ev)
    assert rec == [("w1", "mention")]
    assert c.set_state_calls == []  # mention never changes state
    assert store.is_mention_handled("cm1")
    # second time: deduped
    orch.handle(ev)
    assert rec == [("w1", "mention")]


def test_failure_comments_and_moves_to_review():
    c = FakeClient(state_name="AI Todo")
    rec = []
    orch = Orchestrator(settings(), c, InMemoryStore(), run_task=make_run(rec, c, exc=RuntimeError("boom")))
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert c.set_state_calls == ["D", "R"]  # working, then review on failure
    assert any("failed" in h for h in c.comments)


# ---- poller ----------------------------------------------------------------
class PollClient:
    def list_projects(self):
        return [Project(id="p1", name="proj", identifier="P")]

    def invalidate_states(self, pid):
        pass

    def resolve_state_id(self, pid, name):
        if name.strip().lower() == "ai todo":
            return "TID"
        raise StateNotFound(name)

    def iter_work_items(self, pid):
        yield WorkItem(id="w1", project_id=pid, state_id="TID")
        yield WorkItem(id="w2", project_id=pid, state_id="OTHER")


def test_poller_scan_once_finds_trigger_state_items():
    p = Poller(settings(), PollClient(), submit=lambda ev: None)
    evs = p.scan_once()
    assert [(e.work_item_id, e.reason) for e in evs] == [("w1", "state")]


def test_claim_failure_deduplicated():
    """Claim failure should not post duplicate comments within backoff window."""
    c = FakeClient(state_name="AI Todo")
    store = InMemoryStore()

    # Make set_state fail
    def fail_set_state(pid, wid, sid):
        raise RuntimeError("claim failed")
    c.set_state = fail_set_state

    orch = Orchestrator(settings(), c, store, run_task=make_run([], c))

    # First failure: should comment
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert len(c.comments) == 1
    assert "Failed to claim" in c.comments[0]

    # Second failure (immediate): should skip comment
    c.comments.clear()
    orch.handle(TriggerEvent("w1", "p1", "state"))
    assert len(c.comments) == 0
