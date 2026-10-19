import pytest
from pytest_bdd import given, scenario, then, when

from quadra import cards
from quadra.sessions import (
    close_session,
    create_session,
    get_client,
    get_session_status,
)


@scenario("../features/repeat_closure_idempotently.feature", "Repeat closure idempotently")
def test_repeat_closure_idempotently():
    pass


@pytest.fixture
def context():
    return {}


def _snapshot(session_id):
    return (
        get_client().table("cards").select("*").eq("session_id", session_id).order("id")
        .execute().data
    )


@given("Session S is already closed with a recorded card snapshot")
def _closed_session(context):
    session_id = create_session("Repeat closure").session_id
    quadrant_id = (
        get_client()
        .table("quadrants")
        .select("id, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .limit(1)
        .execute()
        .data[0]["id"]
    )
    cards.insert_card(session_id, quadrant_id, 0, "Recorded", "Alice")
    assert close_session(session_id) == "closed"
    context["session_id"] = session_id
    context["snapshot"] = _snapshot(session_id)
    assert len(context["snapshot"]) == 1


@when("The session service is asked to close S again")
def _close_again(context):
    context["result"] = close_session(context["session_id"])


@then("The service returns the closed state without an error")
def _returns_closed(context):
    assert context["result"] == "closed"


@then("The stored status and card snapshot remain unchanged")
def _unchanged(context):
    assert get_session_status(context["session_id"]) == "closed"
    assert _snapshot(context["session_id"]) == context["snapshot"]
