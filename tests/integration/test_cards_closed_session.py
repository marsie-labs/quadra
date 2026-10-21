"""The card service surfaces the database's closed-session rejection as its own error.

Each supported mutation, submitted to a closed session through the card service and by a
direct database write as the application role, is rejected and leaves every card unchanged.
Mutations on an open session keep their contracts, including authorship and creation time.
"""

import os
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from postgrest.exceptions import APIError
from psycopg.rows import dict_row

from quadra import cards
from quadra.sessions import close_session, create_session

CLOSED = "Session is closed"


@dataclass(frozen=True)
class Workspace:
    session_id: str
    quadrants: list[str]
    card_id: str


def _read(query: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(
        os.environ["SUPABASE_DB_URL"], autocommit=True, prepare_threshold=None, row_factory=dict_row
    ) as connection:
        return connection.execute(query, params).fetchall()


def _snapshot(session_id: str) -> list[dict]:
    """Every card row of the session, soft-deleted ones included."""
    return _read("select * from public.cards where session_id = %s order by id", (session_id,))


def _workspace() -> Workspace:
    session_id = create_session("Closed card mutations").session_id
    quadrants = [
        str(row["id"])
        for row in _read(
            "select q.id from public.quadrants q join public.canvases c on c.id = q.canvas_id "
            "where c.workspace_id = %s order by q.position",
            (session_id,),
        )
    ]
    card = cards.insert_card(session_id, quadrants[0], 0, "Original", "Alice")
    return Workspace(session_id, quadrants, card.id)


@pytest.fixture
def closed_workspace() -> Workspace:
    workspace = _workspace()
    assert close_session(workspace.session_id) == "closed"
    return workspace


def _service_calls(workspace: Workspace) -> dict:
    session_id, card_id = workspace.session_id, workspace.card_id
    return {
        "insert": lambda: cards.insert_card(session_id, workspace.quadrants[0], 9, "New", "Bob"),
        "edit": lambda: cards.update_card(session_id, card_id, "Edited"),
        "move": lambda: cards.relocate_card(session_id, card_id, workspace.quadrants[1], 7),
        "soft_delete": lambda: cards.soft_delete_card(session_id, card_id),
    }


def _direct_statements(workspace: Workspace) -> dict:
    return {
        "insert": (
            'insert into public.cards (id, session_id, quadrant_id, "index", content, '
            "creator_display_name) values (%s, %s, %s, 9, 'New', 'Bob')",
            (str(uuid.uuid4()), workspace.session_id, workspace.quadrants[0]),
        ),
        "edit": (
            "update public.cards set content = 'Edited' where id = %s",
            (workspace.card_id,),
        ),
        "move": (
            'update public.cards set quadrant_id = %s, "index" = 7 where id = %s',
            (workspace.quadrants[1], workspace.card_id),
        ),
        "soft_delete": (
            "update public.cards set deleted_at = now() where id = %s",
            (workspace.card_id,),
        ),
    }


MUTATIONS = ("insert", "edit", "move", "soft_delete")


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_card_service_raises_the_closed_session_error(closed_workspace, mutation):
    before = _snapshot(closed_workspace.session_id)

    with pytest.raises(cards.CardSessionClosedError) as raised:
        _service_calls(closed_workspace)[mutation]()

    assert isinstance(raised.value, cards.CardError)
    cause = raised.value.__cause__
    assert isinstance(cause, APIError)
    assert (cause.code, cause.message) == ("P0001", CLOSED)
    assert _snapshot(closed_workspace.session_id) == before


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_direct_writes_are_rejected_with_the_closed_session_error(closed_workspace, mutation):
    before = _snapshot(closed_workspace.session_id)
    statement, params = _direct_statements(closed_workspace)[mutation]

    with psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None) as connection:
        connection.execute("set local role anon")
        with pytest.raises(psycopg.Error) as raised:
            connection.execute(statement, params)

    assert raised.value.sqlstate == "P0001"
    assert raised.value.diag.message_primary == CLOSED
    assert _snapshot(closed_workspace.session_id) == before


def test_other_card_failures_are_not_reported_as_closed():
    workspace = _workspace()
    with pytest.raises(cards.CardError) as raised:
        cards.update_card(workspace.session_id, str(uuid.uuid4()), "Missing")
    assert not isinstance(raised.value, cards.CardSessionClosedError)


def test_open_session_mutations_keep_their_contracts():
    workspace = _workspace()
    original = cards.list_cards(workspace.session_id)[0]

    inserted = cards.insert_card(workspace.session_id, workspace.quadrants[0], 1, "New", "Bob")
    edited = cards.update_card(workspace.session_id, original.id, "Edited")
    moved = cards.relocate_card(workspace.session_id, original.id, workspace.quadrants[1], 3)
    deleted = cards.soft_delete_card(workspace.session_id, inserted.id)

    assert [
        (inserted.content, inserted.creator_display_name),
        edited.content,
        (moved.quadrant_id, moved.index),
        deleted.deleted_at is not None,
    ] == [("New", "Bob"), "Edited", (workspace.quadrants[1], 3), True]
    assert _authorship(edited) == _authorship(moved) == _authorship(original)
    assert _active_ids(workspace.session_id) == [original.id]


def _active_ids(session_id: str) -> list[str]:
    return [card.id for card in cards.list_cards(session_id)]


def _authorship(card: cards.Card) -> tuple[str, str]:
    return card.creator_display_name, card.created_at
