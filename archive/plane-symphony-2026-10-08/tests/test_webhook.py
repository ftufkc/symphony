import hashlib
import hmac

from plane_symphony.webhook import verify_signature, parse_event

BOT = "bot-1"


def _sig(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_verify_signature_ok_and_bad():
    secret = b"s3cret"
    body = b'{"a":1}'
    assert verify_signature(secret, body, _sig(secret, body)) is True
    assert verify_signature(secret, body, "deadbeef") is False
    assert verify_signature(secret, body, None) is False


def test_parse_event_mention():
    payload = {
        "event": "issue_comment",
        "action": "created",
        "data": {
            "id": "c1",
            "issue": "w1",
            "project": "p1",
            "comment_html": '<mention-component entity_name="user_mention" entity_identifier="bot-1"></mention-component>',
        },
        "activity": {"actor": {"id": "human-9"}},
    }
    ev = parse_event(payload, BOT)
    assert ev is not None
    assert ev.reason == "mention" and ev.work_item_id == "w1" and ev.project_id == "p1"
    assert ev.comment_id == "c1" and ev.actor_id == "human-9"


def test_parse_event_comment_without_bot_mention_is_none():
    payload = {
        "event": "issue_comment",
        "action": "created",
        "data": {"id": "c1", "issue": "w1", "project": "p1", "comment_html": "<p>no mention</p>"},
        "activity": {"actor": {"id": "human-9"}},
    }
    assert parse_event(payload, BOT) is None


def test_parse_event_ignores_bot_own_actions():
    payload = {
        "event": "issue_comment",
        "action": "created",
        "data": {"id": "c1", "issue": "w1", "project": "p1",
                 "comment_html": '<mention-component entity_name="user_mention" entity_identifier="bot-1"></mention-component>'},
        "activity": {"actor": {"id": "bot-1"}},  # actor == bot
    }
    assert parse_event(payload, BOT) is None


def test_parse_event_state_change():
    payload = {
        "event": "issue",
        "action": "updated",
        "data": {"id": "w5", "project": "p1"},
        "activity": {"field": "state", "actor": {"id": "human-9"}},
    }
    ev = parse_event(payload, BOT)
    assert ev is not None and ev.reason == "state" and ev.work_item_id == "w5" and ev.project_id == "p1"


def test_parse_event_non_state_field_update_is_none():
    payload = {
        "event": "issue", "action": "update",
        "data": {"id": "w5", "project": "p1"},
        "activity": {"field": "priority", "actor": {"id": "human-9"}},
    }
    assert parse_event(payload, BOT) is None


def test_parse_event_ignores_bot_when_actor_is_string():
    """Self-loop prevention should work when actor is a string user ID."""
    bot_id = "bot-uuid-123"

    # Plane sends actor as string (not dict)
    payload = {
        "event": "issue",
        "action": "updated",
        "data": {"id": "w-1", "project": "p-1"},
        "activity": {"actor": bot_id, "field": "state"},  # ← string, not dict
    }

    result = parse_event(payload, bot_id)

    # Should be filtered out (bot's own action)
    assert result is None
