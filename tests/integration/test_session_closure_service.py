"""Session service closure (quadra.sessions.close_session) against real Supabase."""

import uuid

from quadra import cards
from quadra.sessions import (
    close_session,
    create_session,
    get_client,
    get_session_status,
)


def _session_with_card() -> str:
    session = create_session("Closure service")
    quadrant_id = (
        get_client()
        .table("quadrants")
        .select("id, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session.session_id)
        .limit(1)
        .execute()
        .data[0]["id"]
    )
    cards.insert_card(session.session_id, quadrant_id, 0, "Recorded", "Alice")
    return session.session_id


def _snapshot(session_id: str) -> list[dict]:
    return (
        get_client().table("cards").select("*").eq("session_id", session_id).order("id")
        .execute().data
    )


def test_close_open_session_returns_closed_and_stores_it():
    session_id = _session_with_card()
    assert get_session_status(session_id) == "open"

    assert close_session(session_id) == "closed"

    assert get_session_status(session_id) == "closed"


def test_close_closed_session_is_idempotent_and_leaves_cards_unchanged():
    session_id = _session_with_card()
    close_session(session_id)
    snapshot = _snapshot(session_id)

    assert close_session(session_id) == "closed"

    assert get_session_status(session_id) == "closed"
    assert _snapshot(session_id) == snapshot
    assert len(snapshot) == 1


def test_unknown_session_is_not_found():
    unknown = str(uuid.uuid4())

    assert close_session(unknown) is None
    assert get_session_status(unknown) is None
