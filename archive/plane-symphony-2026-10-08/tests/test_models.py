from plane_symphony.models import (
    WorkItem,
    State,
    Comment,
    Project,
    parse_mentioned_user_ids,
    markdown_to_comment_html,
)


def test_workitem_from_api_with_state_uuid():
    raw = {
        "id": "w1",
        "name": "做调研",
        "description_html": "<p>hi</p>",
        "state": "s1",  # not expanded -> uuid string
        "project": "p1",
        "created_by": "u9",
        "sequence_id": 7,
    }
    wi = WorkItem.from_api(raw)
    assert wi.id == "w1"
    assert wi.name == "做调研"
    assert wi.description_html == "<p>hi</p>"
    assert wi.state_id == "s1"
    assert wi.state_name is None
    assert wi.project_id == "p1"
    assert wi.created_by == "u9"
    assert wi.sequence_id == 7


def test_workitem_from_api_with_expanded_state():
    raw = {
        "id": "w2",
        "name": "x",
        "state": {"id": "s2", "name": "AI Todo", "group": "unstarted"},
        "project": "p1",
    }
    wi = WorkItem.from_api(raw)
    assert wi.state_id == "s2"
    assert wi.state_name == "AI Todo"


def test_workitem_from_api_tolerates_missing_fields():
    wi = WorkItem.from_api({"id": "w3"})
    assert wi.id == "w3"
    assert wi.name == ""
    assert wi.state_id is None
    assert wi.description_html == ""


def test_state_from_api():
    s = State.from_api({"id": "s1", "name": "AI Todo", "group": "unstarted", "color": "#fff"})
    assert (s.id, s.name, s.group) == ("s1", "AI Todo", "unstarted")


def test_project_from_api():
    p = Project.from_api({"id": "p1", "name": "neocss", "identifier": "NEO"})
    assert (p.id, p.name, p.identifier) == ("p1", "neocss", "NEO")


def test_comment_from_api():
    c = Comment.from_api({"id": "c1", "comment_html": "<p>hi</p>", "created_by": "u1"})
    assert c.id == "c1" and c.comment_html == "<p>hi</p>" and c.created_by == "u1"


def test_parse_mentioned_user_ids_extracts_uuids():
    html = (
        '<p>hey <mention-component entity_name="user_mention" '
        'entity_identifier="bot-uuid-123"></mention-component> and '
        '<mention-component entity_name="user_mention" entity_identifier="u-2">'
        "</mention-component></p>"
    )
    assert parse_mentioned_user_ids(html) == ["bot-uuid-123", "u-2"]


def test_parse_mentioned_user_ids_ignores_non_user_mentions():
    html = '<mention-component entity_name="issue_mention" entity_identifier="i-1"></mention-component>'
    assert parse_mentioned_user_ids(html) == []


def test_parse_mentioned_user_ids_dedupes_preserving_order():
    html = (
        '<mention-component entity_name="user_mention" entity_identifier="a"></mention-component>'
        '<mention-component entity_name="user_mention" entity_identifier="a"></mention-component>'
    )
    assert parse_mentioned_user_ids(html) == ["a"]


def test_parse_mentioned_user_ids_empty_and_none():
    assert parse_mentioned_user_ids("<p>no mention</p>") == []
    assert parse_mentioned_user_ids("") == []
    assert parse_mentioned_user_ids(None) == []


def test_markdown_to_comment_html_paragraphs():
    out = markdown_to_comment_html("line one\n\nline two")
    assert "<p>line one</p>" in out
    assert "<p>line two</p>" in out


def test_markdown_to_comment_html_single_line():
    assert markdown_to_comment_html("hello") == "<p>hello</p>"


def test_markdown_to_comment_html_escapes_html():
    out = markdown_to_comment_html("a < b & c")
    assert "&lt;" in out and "&amp;" in out
