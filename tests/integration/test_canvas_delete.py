import asyncio
import os
import threading

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
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


class _Delayed:
    """Runs a real card operation, then holds its response until released."""

    def __init__(self, real) -> None:
        self._real = real
        self.entered = threading.Event()
        self.release = threading.Event()
        self.card: cards.Card | None = None

    def __call__(self, *args, **kwargs):
        self.card = self._real(*args, **kwargs)
        self.entered.set()
        assert self.release.wait(timeout=30), "delayed response was never released"
        return self.card


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list:
    """Make the page build canvases that note when a fold-path operation has finished."""
    canvases: list = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            self.finished = asyncio.Event()
            canvases.append(self)

        async def _submit(self, *args) -> None:
            await super()._submit(*args)
            self.finished.set()

        async def _save_edit(self, *args) -> None:
            await super()._save_edit(*args)
            self.finished.set()

        async def _move(self, *args) -> None:
            await super()._move(*args)
            self.finished.set()

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


async def _delete_while_held(delayed: _Delayed, canvas, session_id: str) -> cards.Card:
    """Delete the card whose response is held; wait until the canvas has seen the deletion."""
    await asyncio.wait_for(asyncio.to_thread(delayed.entered.wait, 10), timeout=15)
    card = delayed.card
    await asyncio.to_thread(cards.soft_delete_card, session_id, card.id)
    await _wait_until(lambda: card.id in canvas._deleted and card.id not in canvas._cards)
    return card


async def _release_and_finish(delayed: _Delayed, canvas) -> None:
    delayed.release.set()
    async with asyncio.timeout(10):
        await canvas.finished.wait()


def _assert_not_shown(user: User, canvas, card: cards.Card, text: str) -> None:
    """The canvas holds and renders nothing of the card."""
    assert card.id not in canvas._cards
    assert text not in _texts(user)
    markers = _markers(user)
    assert f"delete-{card.id}" not in markers
    assert f"pending-{card.id}" not in markers


def _assert_gone(user: User, canvas, card: cards.Card, session_id: str, text: str) -> None:
    """The delayed response did not restore the card; the database keeps it deleted."""
    _assert_not_shown(user, canvas, card, text)
    ((deleted_at,),) = _rows(card.id)
    assert deleted_at is not None
    assert card.id not in [active.id for active in cards.list_cards(session_id)]


async def test_delayed_insert_response_does_not_restore_a_deleted_card(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Delayed Insert").session_id
    delayed = _Delayed(cards.insert_card)
    monkeypatch.setattr(cards, "insert_card", delayed)
    canvases = _record_canvases(monkeypatch)
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see("DOES NOT", retries=100)
        (canvas,) = canvases
        await _wait_until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))
        user.find(marker="display-name").type("Alice")
        user.find(marker="content-0").type("Raced insert")
        user.find(marker="submit-0").click()
        card = await _delete_while_held(delayed, canvas, session_id)
        await _release_and_finish(delayed, canvas)
        assert card.id not in canvas._cards  # at once, not only after later pushes
        # A later card with a different id is unaffected by the retained deletion.
        later = cards.insert_card(session_id, card.quadrant_id, 1, "Later card", "Bob")
        await _wait_until(lambda: later.id in canvas._cards)
        await _wait_until(lambda: "Later card" in _texts(user))
        _assert_gone(user, canvas, card, session_id, "Raced insert")
    finally:
        delayed.release.set()
        await _release_all(session_id)


async def test_delayed_edit_response_does_not_restore_a_deleted_card(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Delayed Edit").session_id
    delayed = _Delayed(cards.update_card)
    monkeypatch.setattr(cards, "update_card", delayed)
    canvases = _record_canvases(monkeypatch)
    try:
        card = await _open_with_card([user], session_id)
        (canvas,) = canvases
        user.find(marker="display-name").type("Bob")
        user.find(marker=f"edit-{card.id}").click()
        await user.should_see(marker=f"save-{card.id}")
        (editor,) = user.find(marker=f"edit-content-{card.id}").elements
        editor.value = "Raced edit"
        user.find(marker=f"save-{card.id}").click()
        await _delete_while_held(delayed, canvas, session_id)
        await _release_and_finish(delayed, canvas)
        _assert_gone(user, canvas, card, session_id, "Raced edit")
        assert f"save-{card.id}" not in _markers(user)
    finally:
        delayed.release.set()
        await _release_all(session_id)


async def test_delayed_relocation_response_does_not_restore_a_deleted_card(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Delayed Move").session_id
    delayed = _Delayed(cards.relocate_card)
    monkeypatch.setattr(cards, "relocate_card", delayed)
    canvases = _record_canvases(monkeypatch)
    try:
        card = await _open_with_card([user], session_id)
        (canvas,) = canvases
        (target,) = user.find(marker=f"move-quadrant-{card.id}").elements
        (at,) = user.find(marker=f"move-index-{card.id}").elements
        target.value, at.value = 1, 3
        user.find(marker=f"move-{card.id}").click()
        await _delete_while_held(delayed, canvas, session_id)
        await _release_and_finish(delayed, canvas)
        _assert_gone(user, canvas, card, session_id, "Obsolete")
    finally:
        delayed.release.set()
        await _release_all(session_id)
