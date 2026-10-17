import asyncio
import os

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import _load_quadrant_ids
from quadra.sessions import create_session

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401

_ROW = "select deleted_at from public.cards where id = %s"


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _rows(card_id: str) -> list[tuple]:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        return connection.execute(_ROW, (card_id,)).fetchall()


def _texts(user: User) -> list[str]:
    return [
        element.text
        for element in user.current_layout.descendants()
        if isinstance(element, ui.label)
    ]


def _markers(user: User) -> set[str]:
    return {marker for element in user.current_layout.descendants() for marker in element._markers}


async def _wait_until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


async def _open_with_card(users: list[User], session_id: str) -> cards.Card:
    quadrant_id = _load_quadrant_ids(session_id)[0]
    card = cards.insert_card(session_id, quadrant_id, 0, "Obsolete", "Alice")
    for user in users:
        await user.open(f"/sessions/{session_id}")
        await user.should_see(marker=f"delete-{card.id}", retries=100)
    return card


async def test_deleted_card_leaves_every_canvas_and_keeps_its_row(user: User, create_user):
    other = create_user()
    session_id = create_session("Delete Card").session_id
    try:
        card = await _open_with_card([user, other], session_id)
        assert "Obsolete" in _texts(user) and "Obsolete" in _texts(other)

        user.find(marker=f"delete-{card.id}").click()
        await user.should_see(marker=f"confirm-delete-{card.id}")
        assert _rows(card.id) == [(None,)]  # nothing is deleted before confirmation
        user.find(marker=f"confirm-delete-{card.id}").click()

        await _wait_until(lambda: "Obsolete" not in _texts(user))
        await _wait_until(lambda: "Obsolete" not in _texts(other))
        await _wait_until(lambda: f"confirm-delete-{card.id}" not in _markers(user))
        remaining = cards.list_cards(session_id)
    finally:
        await _release_all(session_id)

    ((deleted_at,),) = _rows(card.id)
    assert deleted_at is not None
    assert card.id not in [active.id for active in remaining]


async def test_cancelled_deletion_keeps_the_card(user: User, create_user):
    other = create_user()
    session_id = create_session("Cancel Delete").session_id
    try:
        card = await _open_with_card([user, other], session_id)
        user.find(marker=f"delete-{card.id}").click()
        await user.should_see(marker=f"cancel-delete-{card.id}")
        user.find(marker=f"cancel-delete-{card.id}").click()
        await _wait_until(lambda: f"cancel-delete-{card.id}" not in _markers(user))
        await asyncio.sleep(0.5)
        texts = _texts(user), _texts(other)
    finally:
        await _release_all(session_id)

    assert all("Obsolete" in shown for shown in texts)
    assert _rows(card.id) == [(None,)]


async def test_failed_deletion_keeps_the_card(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Failing Delete").session_id

    def failing(*args, **kwargs):
        raise cards.CardPersistenceError("boom")

    monkeypatch.setattr(cards, "soft_delete_card", failing)
    try:
        card = await _open_with_card([user], session_id)
        user.find(marker=f"delete-{card.id}").click()
        await user.should_see(marker=f"confirm-delete-{card.id}")
        user.find(marker=f"confirm-delete-{card.id}").click()
        await user.should_see("Card could not be deleted", retries=100)
        texts = _texts(user)
    finally:
        await _release_all(session_id)

    assert "Obsolete" in texts
    assert _rows(card.id) == [(None,)]
    caplog.clear()
