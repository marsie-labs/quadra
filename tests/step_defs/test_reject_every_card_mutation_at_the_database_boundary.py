import os
import uuid

import psycopg
import pytest
from pytest_bdd import given, scenario, then, when

from quadra import cards
from quadra.sessions import close_session, create_session, get_client

MUTATIONS = ("insert", "edit", "move", "soft_delete")
PATHS = ("card_service", "direct_write")


@scenario(
    "../features/reject_every_card_mutation_at_the_database_boundary.feature",
    "Reject every card mutation at the database boundary",
)
def test_reject_every_card_mutation_at_the_database_boundary():
    pass


@pytest.fixture
def context():
    return {}


def _snapshot(session_id):
    return (
        get_client().table("cards").select("*").eq("session_id", session_id).order("id")
        .execute().data
    )


def _service_call(context, mutation):
    session_id, card_id, quadrants = context["session_id"], context["card_id"], context["quadrants"]
    return {
        "insert": lambda: cards.insert_card(session_id, quadrants[0], 9, "New", "Bob"),
        "edit": lambda: cards.update_card(session_id, card_id, "Edited"),
        "move": lambda: cards.relocate_card(session_id, card_id, quadrants[1], 7),
        "soft_delete": lambda: cards.soft_delete_card(session_id, card_id),
    }[mutation]


def _direct_statement(context, mutation):
    return {
        "insert": (
            'insert into public.cards (id, session_id, quadrant_id, "index", content, '
            "creator_display_name) values (%s, %s, %s, 9, 'New', 'Bob')",
            (str(uuid.uuid4()), context["session_id"], context["quadrants"][0]),
        ),
        "edit": (
            "update public.cards set content = 'Edited' where id = %s",
            (context["card_id"],),
        ),
        "move": (
            'update public.cards set quadrant_id = %s, "index" = 7 where id = %s',
            (context["quadrants"][1], context["card_id"]),
        ),
        "soft_delete": (
            "update public.cards set deleted_at = now() where id = %s",
            (context["card_id"],),
        ),
    }[mutation]


def _submit_directly(context, mutation):
    statement, params = _direct_statement(context, mutation)
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None) as connection:
        connection.execute("set local role anon")
        connection.execute(statement, params)


def _submit_through_service(context, mutation):
    try:
        _service_call(context, mutation)()
    except cards.CardSessionClosedError as error:
        return error
    return None


@given("Session S is closed and contains card C")
def _closed_session(context):
    session_id = create_session("Reject card mutations").session_id
    rows = (
        get_client()
        .table("quadrants")
        .select("id, position, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .order("position")
        .execute()
        .data
    )
    context["quadrants"] = [row["id"] for row in rows]
    context["card_id"] = cards.insert_card(session_id, context["quadrants"][0], 0, "C", "Alice").id
    assert close_session(session_id) == "closed"
    context["session_id"] = session_id


@given("The test is parameterized over insert, content edit, quadrant/index move and setting deleted_at")
def _mutations(context):
    context["mutations"] = MUTATIONS


@given(
    "The test is parameterized over the card-service path and direct database writes "
    "using the application's database access role"
)
def _paths(context):
    context["paths"] = PATHS


def _submit_directly_capturing(context, mutation):
    try:
        _submit_directly(context, mutation)
    except psycopg.Error as error:
        return error
    return None


_SUBMITTERS = {
    "card_service": _submit_through_service,
    "direct_write": _submit_directly_capturing,
}


@when("The selected mutation is submitted through the selected path")
def _submit(context):
    context["snapshot"] = _snapshot(context["session_id"])
    assert len(context["snapshot"]) == 1
    context["outcomes"] = {
        (mutation, path): _SUBMITTERS[path](context, mutation)
        for mutation in context["mutations"]
        for path in context["paths"]
    }


def _assert_closed_rejection(mutation, path, error):
    assert error is not None, f"{mutation} via {path} was accepted"
    if path == "direct_write":
        assert error.sqlstate == "P0001", (mutation, path)
        assert error.diag.message_primary == "Session is closed", (mutation, path)


@then("The mutation is rejected with the closed-session error")
def _rejected(context):
    assert len(context["outcomes"]) == len(MUTATIONS) * len(PATHS)
    for (mutation, path), error in context["outcomes"].items():
        _assert_closed_rejection(mutation, path, error)


@then("No card record in S differs from the pre-request snapshot")
def _unchanged(context):
    assert _snapshot(context["session_id"]) == context["snapshot"]
