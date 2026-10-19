import os
from datetime import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_bdd import given, scenario, then, when

from quadra import api, cards
from quadra.sessions import close_session, create_session, get_client

ROOT = Path(__file__).resolve().parents[2]
PAGE_SIZE = 2


@scenario(
    "../features/retrieve_complete_finalized_quadrant_data.feature",
    "Retrieve complete finalized quadrant data",
)
def test_retrieve_complete_finalized_quadrant_data():
    pass


@pytest.fixture
def context(monkeypatch):
    load_dotenv(ROOT / ".env")
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_PUBLISHABLE_KEY")):
        pytest.skip("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required")
    monkeypatch.setattr(cards, "_PAGE_SIZE", PAGE_SIZE)
    return {}


def _quadrants(session_id):
    rows = (
        get_client()
        .table("quadrants")
        .select("id, name, position, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .order("position")
        .execute()
        .data
    )
    return [{k: row[k] for k in ("id", "name", "position")} for row in rows]


@given("S is closed and has four persisted quadrants, including one empty quadrant")
def _session(context):
    context["session_id"] = create_session("Finalized retrieval").session_id
    context["quadrants"] = _quadrants(context["session_id"])
    assert len(context["quadrants"]) == 4


@given("S has more active cards than the configured Supabase single-page limit")
def _active_cards(context):
    session_id = context["session_id"]
    first, second, third, _empty = context["quadrants"]
    # Three quadrants hold five cards in total; the fourth stays empty.
    for quadrant, index, content in [
        (third, 0, "does-0"),
        (first, 1, "is-1"),
        (first, 0, "is-0"),
        (second, 0, "is-not-0"),
        (first, 2, "is-2"),
    ]:
        cards.insert_card(session_id, quadrant["id"], index, content, "Alice")
    assert len(context["quadrants"][3:]) == 1
    assert 5 > PAGE_SIZE


@given("S also has soft-deleted cards and another session has unrelated cards")
def _noise(context):
    session_id = context["session_id"]
    deleted = cards.insert_card(
        session_id, context["quadrants"][0]["id"], 9, "deleted", "Bob"
    )
    cards.soft_delete_card(session_id, deleted.id)
    other_id = create_session("Other").session_id
    cards.insert_card(other_id, _quadrants(other_id)[0]["id"], 0, "elsewhere", "Carol")
    assert close_session(session_id) == "closed"


@given("Expected card values and deterministic ordering are recorded from persisted state")
def _expected(context):
    rows = (
        get_client()
        .table("cards")
        .select("*")
        .eq("session_id", context["session_id"])
        .is_("deleted_at", "null")
        .execute()
        .data
    )
    context["expected"] = {
        quadrant["id"]: [
            {
                "id": row["id"],
                "content": row["content"],
                "author_display_name": row["creator_display_name"],
                "index": row["index"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in sorted(rows, key=lambda r: (r["index"], r["id"]))
            if row["quadrant_id"] == quadrant["id"]
        ]
        for quadrant in context["quadrants"]
    }
    assert sum(len(cards_) for cards_ in context["expected"].values()) == 5


@when("An unauthenticated caller sends GET /sessions/S/data")
def _get(context):
    app = FastAPI()
    app.include_router(api.router)
    context["response"] = TestClient(app).get(f"/sessions/{context['session_id']}/data")


@then("The response status is 200")
def _status(context):
    assert context["response"].status_code == 200


@then("The response contains session_id S, status closed and exactly S's four quadrant groups")
def _groups(context):
    body = context["response"].json()
    assert body["session_id"] == context["session_id"]
    assert body["status"] == "closed"
    assert len(body["quadrants"]) == 4


@then("Each quadrant group contains its persisted id, name and position")
def _quadrant_fields(context):
    got = [
        {k: q[k] for k in ("id", "name", "position")}
        for q in context["response"].json()["quadrants"]
    ]
    assert got == context["quadrants"]


@then(
    "Each group contains every non-deleted card in that quadrant exactly once, "
    "with the empty quadrant represented by an empty cards list"
)
def _cards_present_once(context):
    for group in context["response"].json()["quadrants"]:
        ids = [card["id"] for card in group["cards"]]
        assert sorted(ids) == sorted(c["id"] for c in context["expected"][group["id"]])
    assert context["response"].json()["quadrants"][3]["cards"] == []


CARD_KEYS = {"id", "content", "author_display_name", "index", "created_at", "updated_at"}


def _response_cards(context):
    return [
        card
        for group in context["response"].json()["quadrants"]
        for card in group["cards"]
    ]


def _aware(value):
    return datetime.fromisoformat(value).tzinfo is not None


@then(
    "Each card contains its persisted id, content, author_display_name, index, "
    "created_at and updated_at"
)
def _card_fields(context):
    returned = _response_cards(context)
    assert returned
    assert all(set(card) == CARD_KEYS for card in returned)
    assert all(_aware(card["created_at"]) and _aware(card["updated_at"]) for card in returned)


def _comparable(card):
    return (
        card["id"],
        card["content"],
        card["author_display_name"],
        card["index"],
        datetime.fromisoformat(card["created_at"]),
        datetime.fromisoformat(card["updated_at"]),
    )


@then("Card values and ordering match persisted state")
def _values_and_order(context):
    got = {q["id"]: q["cards"] for q in context["response"].json()["quadrants"]}
    expected = context["expected"]
    assert list(got) == list(expected)
    for quadrant_id, expected_cards in expected.items():
        assert [_comparable(c) for c in got[quadrant_id]] == [
            _comparable(c) for c in expected_cards
        ]


@then("No soft-deleted card or other session's card is included")
def _no_noise(context):
    contents = {card["content"] for card in _response_cards(context)}
    assert contents == {"is-0", "is-1", "is-2", "is-not-0", "does-0"}
