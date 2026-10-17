import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from quadra import cards
from quadra.cards import CardValidationError
from quadra.sessions import create_session, get_client

MIGRATION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "0003_cards.sql"
FIELDS = (
    "id session_id quadrant_id index content creator_display_name "
    "created_at updated_at deleted_at"
).split()


def _sql(statement: str, params: tuple | None = None) -> list[tuple]:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as connection:
        cursor = connection.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def _quadrants(session_id: str) -> dict[str, str]:
    """Quadrant ids of the session by name."""
    rows = (
        get_client()
        .table("quadrants")
        .select("id, name, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .execute()
        .data
    )
    return {row["name"]: row["id"] for row in rows}


@pytest.fixture
def session():
    """A session with its quadrant ids by name."""
    created = create_session("Cards Feature")
    return created.session_id, _quadrants(created.session_id)


def _snapshot(*session_ids: str) -> list[tuple]:
    return _sql(
        "select * from public.cards where session_id = any(%s::uuid[]) order by id",
        (list(session_ids),),
    )


def _row(card_id: str) -> tuple:
    (row,) = _sql(
        "select created_at, updated_at, deleted_at, creator_display_name, content "
        "from public.cards where id = %s",
        (card_id,),
    )
    return row


def test_migration_applies_twice_and_card_reads_back_with_all_fields(session):
    sql = MIGRATION.read_text()
    _sql(sql)
    _sql(sql)
    session_id, quadrants = session
    card_id = str(uuid.uuid4())

    _sql(
        'insert into public.cards (id, session_id, quadrant_id, "index", content, '
        "creator_display_name) values (%s, %s, %s, 0, 'Text', 'Alice')",
        (card_id, session_id, quadrants["IS"]),
    )

    (card,) = cards.list_cards(session_id)
    assert [getattr(card, field) for field in FIELDS][:6] == [
        card_id,
        session_id,
        quadrants["IS"],
        0,
        "Text",
        "Alice",
    ]
    assert card.created_at and card.updated_at and card.deleted_at is None
    published = _sql("select 1 from pg_publication_tables where tablename = 'cards'")
    assert published


@pytest.mark.parametrize(
    ("session_id", "index", "violation"),
    [
        (None, 0, errors.ForeignKeyViolation),
        ("existing", -1, errors.CheckViolation),
    ],
    ids=["missing session", "negative index"],
)
def test_invalid_schema_inserts_fail(session, session_id, index, violation):
    existing_id, quadrants = session
    with pytest.raises(violation):
        _sql(
            'insert into public.cards (id, session_id, quadrant_id, "index", content, '
            "creator_display_name) values (%s, %s, %s, %s, 'Text', 'Alice')",
            (
                str(uuid.uuid4()),
                existing_id if session_id else str(uuid.uuid4()),
                quadrants["IS"],
                index,
            ),
        )
    assert _snapshot(existing_id) == []


def test_insert_is_server_authoritative(session):
    session_id, quadrants = session

    card = cards.insert_card(session_id, quadrants["IS"], 2, "Compare", "  Alice ")

    assert (card.creator_display_name, card.content, card.index) == ("Alice", "Compare", 2)
    assert card.created_at == card.updated_at
    assert card.deleted_at is None


MUTATIONS = {
    "edit": (
        lambda sid, card, quadrants: cards.update_card(sid, card.id, "Revised"),
        lambda changed, quadrants: changed.content == "Revised",
    ),
    "delete": (
        lambda sid, card, quadrants: cards.soft_delete_card(sid, card.id),
        lambda changed, quadrants: changed.deleted_at is not None,
    ),
    "relocate": (
        lambda sid, card, quadrants: cards.relocate_card(sid, card.id, quadrants["DOES"], 3),
        lambda changed, quadrants: (changed.quadrant_id, changed.index)
        == (quadrants["DOES"], 3),
    ),
}


@pytest.mark.parametrize("operation", list(MUTATIONS))
def test_mutations_keep_authorship_and_advance_updated_at(session, operation):
    session_id, quadrants = session
    card = cards.insert_card(session_id, quadrants["IS"], 0, "Draft", "Alice")
    mutate, effect_applied = MUTATIONS[operation]

    changed = mutate(session_id, card, quadrants)

    assert changed.creator_display_name == "Alice"
    assert changed.created_at == card.created_at
    assert changed.updated_at > card.updated_at
    assert effect_applied(changed, quadrants)


def test_list_cards_orders_by_quadrant_index_then_id(session):
    session_id, quadrants = session
    other_id = create_session("Other").session_id
    other_quadrants = _quadrants(other_id)
    ids = sorted(str(uuid.uuid4()) for _ in range(2))
    cards.insert_card(session_id, quadrants["DOES"], 0, "d", "A")
    cards.insert_card(session_id, quadrants["IS"], 2, "a", "A")
    cards.insert_card(session_id, quadrants["IS"], 1, "same-high", "A", card_id=ids[1])
    cards.insert_card(session_id, quadrants["IS"], 1, "same-low", "A", card_id=ids[0])
    cards.insert_card(other_id, other_quadrants["IS"], 0, "elsewhere", "A")

    listed = cards.list_cards(session_id)

    assert [card.content for card in listed] == ["same-low", "same-high", "a", "d"]


def _assert_cross_session_attempts_rejected(s1, c1, q2, c2):
    _assert_rejected(cards.insert_card, s1, q2["IS"], 0, "x", "Alice")
    _assert_rejected(cards.update_card, s1, c2.id, "x")
    _assert_rejected(cards.soft_delete_card, s1, c2.id)
    _assert_rejected(cards.relocate_card, s1, c1.id, q2["IS"], 1)


def _assert_rejected(operation, *args):
    with pytest.raises(CardValidationError):
        operation(*args)


def _listed_ids(session_id: str) -> list[str]:
    return [card.id for card in cards.list_cards(session_id)]


def test_session_scope_and_soft_delete(session):
    s1, q1 = session
    s2 = create_session("Second Feature").session_id
    q2 = _quadrants(s2)
    c1 = cards.insert_card(s1, q1["IS"], 0, "one", "Alice")
    c2 = cards.insert_card(s2, q2["IS"], 0, "two", "Bob")
    before = _snapshot(s1, s2)

    _assert_cross_session_attempts_rejected(s1, c1, q2, c2)
    assert _snapshot(s1, s2) == before

    deleted = cards.soft_delete_card(s1, c1.id)

    _assert_soft_deleted(deleted, s1, s2, c2.id)
    _assert_rejected(cards.update_card, s1, c1.id, "edit after delete")


def _assert_soft_deleted(deleted, deleted_session_id, other_session_id, other_card_id):
    assert deleted.deleted_at is not None
    assert _row(deleted.id)[2] is not None  # the row still exists, flagged deleted
    assert cards.list_cards(deleted_session_id) == []
    assert _listed_ids(other_session_id) == [other_card_id]
