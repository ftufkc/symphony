import json
from urllib.parse import urlparse, parse_qs

import httpx
import pytest

from plane_symphony.plane_client import PlaneClient, StateNotFound, StateAmbiguous

BASE = "http://x/api/v1"


def make(handler, **kw):
    return PlaneClient(
        base_url=BASE,
        token="t",
        workspace="ws",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        **kw,
    )


def test_get_me_sends_api_key_header_and_url():
    seen = {}

    def h(req):
        seen["k"] = req.headers.get("x-api-key")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"id": "bot-1", "email": "b@x"})

    me = make(h).get_me()
    assert me["id"] == "bot-1"
    assert seen["k"] == "t"
    assert seen["url"] == f"{BASE}/users/me/"


def test_list_projects():
    def h(req):
        assert str(req.url).startswith(f"{BASE}/workspaces/ws/projects/")
        return httpx.Response(
            200,
            json={"results": [{"id": "p1", "name": "P", "identifier": "PP"}], "next_page_results": False},
        )

    ps = make(h).list_projects()
    assert len(ps) == 1 and ps[0].id == "p1" and ps[0].identifier == "PP"


def test_resolve_state_id_case_insensitive():
    def h(req):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "s1", "name": "AI Todo", "group": "unstarted"},
                    {"id": "s2", "name": "Done", "group": "completed"},
                ],
                "next_page_results": False,
            },
        )

    assert make(h).resolve_state_id("p1", "ai todo") == "s1"


def test_resolve_state_id_missing_raises():
    def h(req):
        return httpx.Response(200, json={"results": [{"id": "s2", "name": "Done"}], "next_page_results": False})

    with pytest.raises(StateNotFound):
        make(h).resolve_state_id("p1", "AI Todo")


def test_resolve_state_id_ambiguous_raises():
    def h(req):
        return httpx.Response(
            200,
            json={
                "results": [{"id": "s1", "name": "AI Todo"}, {"id": "s2", "name": "ai todo"}],
                "next_page_results": False,
            },
        )

    with pytest.raises(StateAmbiguous):
        make(h).resolve_state_id("p1", "ai todo")


def test_get_work_item_expands_state():
    def h(req):
        assert "expand=state" in str(req.url)
        return httpx.Response(
            200, json={"id": "w1", "name": "x", "state": {"id": "s1", "name": "AI Todo"}, "project": "p1"}
        )

    wi = make(h).get_work_item("p1", "w1")
    assert wi.state_id == "s1" and wi.state_name == "AI Todo"


def test_iter_work_items_paginates():
    pages = {
        None: {"results": [{"id": "w1"}], "next_page_results": True, "next_cursor": "C2"},
        "C2": {"results": [{"id": "w2"}], "next_page_results": False, "next_cursor": None},
    }

    def h(req):
        cur = parse_qs(urlparse(str(req.url)).query).get("cursor", [None])[0]
        return httpx.Response(200, json=pages[cur])

    ids = [wi.id for wi in make(h).iter_work_items("p1")]
    assert ids == ["w1", "w2"]


def test_set_state_sends_patch_with_state():
    seen = {}

    def h(req):
        seen["method"] = req.method
        seen["body"] = json.loads(req.read())
        return httpx.Response(200, json={"id": "w1"})

    make(h).set_state("p1", "w1", "s9")
    assert seen["method"] == "PATCH" and seen["body"]["state"] == "s9"


def test_add_comment_sends_post_with_comment_html():
    seen = {}

    def h(req):
        seen["method"] = req.method
        seen["body"] = json.loads(req.read())
        return httpx.Response(201, json={"id": "c1", "comment_html": "<p>x</p>"})

    make(h).add_comment("p1", "w1", "<p>x</p>")
    assert seen["method"] == "POST" and seen["body"]["comment_html"] == "<p>x</p>"


def test_429_retries_then_succeeds():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={})
        return httpx.Response(200, json={"id": "bot"})

    me = make(h).get_me()
    assert me["id"] == "bot" and calls["n"] == 2


def test_raw_request_passthrough():
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        seen["method"] = req.method
        return httpx.Response(200, json={"ok": True})

    out = make(h).raw_request("GET", "/users/me/", query={"a": "1"})
    assert out == {"ok": True}
    assert seen["method"] == "GET" and "a=1" in seen["url"]


def test_raw_request_absolute_url_rejected():
    """Absolute URLs are rejected to prevent token leakage to external hosts."""
    client = PlaneClient("http://localhost:8000/api/v1", "token", "ws")
    with pytest.raises(ValueError, match="Absolute URL rejected"):
        client.raw_request("GET", "https://evil.com/steal")
    with pytest.raises(ValueError, match="Absolute URL rejected"):
        client.raw_request("POST", "http://attacker.local/log")

