"""GET /sessions/{session_id}/data and get_finalized_session_data against real Supabase."""

import uuid

import pytest

from quadra import cards
from quadra.sessions import (
    SessionStillOpenError,
    close_session,
    create_session,
    get_client,
    get_finalized_session_data,
)


def _first_quadrant_id(session_id: str) -> str:
    return (
        get_client()
        .table("quadrants")
        .select("id, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .limit(1)
        .execute()
        .data[0]["id"]
    )


def test_unknown_session_returns_404_without_card_data(client):
    response = client.get(f"/sessions/{uuid.uuid4()}/data")

    assert response.status_code == 404
    assert "quadrants" not in response.json()


def test_open_session_returns_409_without_card_data(client):
    session_id = create_session("Open retrieval").session_id
    cards.insert_card(session_id, _first_quadrant_id(session_id), 0, "secret", "Alice")

    response = client.get(f"/sessions/{session_id}/data")

    assert response.status_code == 409
    assert response.json() == {"detail": "Session is still open"}


def test_invalid_uuid_is_rejected_by_validation(client):
    assert client.get("/sessions/not-a-uuid/data").status_code == 422


def _close_session_with_cards(quadrant_indexes=(2, 0, 1, 3, 4)) -> str:
    session_id = create_session("Closed retrieval").session_id
    quadrant_id = _first_quadrant_id(session_id)
    for index in quadrant_indexes:
        cards.insert_card(session_id, quadrant_id, index, f"c{index}", "Alice")
    deleted = cards.insert_card(session_id, quadrant_id, 9, "deleted", "Alice")
    cards.soft_delete_card(session_id, deleted.id)
    assert close_session(session_id) == "closed"
    return session_id


def _summary(quadrants):
    return [(q["position"], q["name"]) for q in quadrants]


def _contents(quadrants):
    return [[c["content"] for c in q["cards"]] for q in quadrants]


def _assert_complete_body(body, session_id):
    assert body["session_id"] == session_id
    assert body["status"] == "closed"
    quadrants = body["quadrants"]
    assert _summary(quadrants) == [
        (0, "IS"), (1, "IS NOT"), (2, "DOES"), (3, "DOES NOT")
    ]
    assert _contents(quadrants) == [["c0", "c1", "c2", "c3", "c4"], [], [], []]


def test_closed_session_returns_all_cards_across_pages(client, monkeypatch):
    monkeypatch.setattr(cards, "_PAGE_SIZE", 2)
    session_id = _close_session_with_cards()

    response = client.get(f"/sessions/{session_id}/data")

    assert response.status_code == 200
    body = response.json()
    _assert_complete_body(body, session_id)
    assert set(body["quadrants"][0]["cards"][0]) == {
        "id", "content", "author_display_name", "index", "created_at", "updated_at"
    }


def test_service_returns_none_for_unknown_and_raises_for_open():
    assert get_finalized_session_data(str(uuid.uuid4())) is None
    with pytest.raises(SessionStillOpenError):
        get_finalized_session_data(create_session("Open service").session_id)
