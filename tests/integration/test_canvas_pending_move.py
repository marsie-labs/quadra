import asyncio
import os
import threading
from dataclasses import replace

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


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


class _Barrier:
    """Holds `cards.relocate_card` unresolved until released."""

    def __init__(self, real_relocate, settle=None) -> None:
        self._real_relocate = real_relocate
        self._settle = settle  # replaces the real relocation once released, if given
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        self.entered.set()
        assert self.release.wait(timeout=30), "barrier was never released"
        return (self._settle or self._real_relocate)(*args, **kwargs)


def _marked(user: User, marker: str) -> list:
    """Elements of the page carrying `marker`; empty when there are none."""
    return [e for e in user.current_layout.descendants() if marker in e._markers]


def _label_texts(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.label)]


def _quadrant_texts(element) -> list[str]:
    """Texts of the labels inside the quadrant card holding the pending `element`."""
    column = element.parent_slot.parent
    quadrant = column.parent_slot.parent
    return [e.text for e in quadrant.descendants() if isinstance(e, ui.label)]


async def _until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


def _assert_pending_in_target(user: User, card_id: str) -> None:
    """C is shown once, pending, in Q2 (IS NOT) and not in Q1 (IS)."""
    (element,) = _marked(user, f"pending-{card_id}")
    assert _label_texts(element) == ["Mover", "Alice", "Pending"]
    quadrant_texts = _quadrant_texts(element)
    assert ("IS NOT" in quadrant_texts, "IS" in quadrant_texts) == (True, False)
    assert _label_texts(user.current_layout).count("Mover") == 1


def _assert_origin_remembered(canvas: Canvas, card_id: str, q1: str, q2: str) -> None:
    move = canvas._moving[card_id]
    assert (move.origin_quadrant_id, move.origin_index) == (q1, 0)
    assert (move.quadrant_id, move.index) == (q2, 3)


def _assert_persisted(session_id: str, quadrant_id: str, index: int) -> None:
    (persisted,) = cards.list_cards(session_id)
    assert (persisted.quadrant_id, persisted.index) == (quadrant_id, index)


async def _request_move(user: User, card_id: str) -> None:
    await user.should_see(marker=f"move-{card_id}", retries=100)
    (target,) = user.find(marker=f"move-quadrant-{card_id}").elements
    (at,) = user.find(marker=f"move-index-{card_id}").elements
    target.value, at.value = 1, 3
    user.find(marker=f"move-{card_id}").click()


async def test_moved_card_shows_pending_in_target_quadrant_until_the_move_resolves(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Pending Move").session_id
    q1, q2 = _load_quadrant_ids(session_id)[:2]
    card = cards.insert_card(session_id, q1, 0, "Mover", "Alice")
    barrier = _Barrier(cards.relocate_card)
    monkeypatch.setattr(cards, "relocate_card", barrier)
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    try:
        await user.open(f"/sessions/{session_id}")
        await _request_move(user, card.id)
        await asyncio.wait_for(asyncio.to_thread(barrier.entered.wait, 10), timeout=15)

        # The request is held: C must already be shown in Q2, pending, under its id.
        _assert_pending_in_target(user, card.id)
        # The view remembers where C came from, and the database has not changed.
        (canvas,) = canvases
        _assert_origin_remembered(canvas, card.id, q1, q2)
        _assert_persisted(session_id, q1, 0)

        barrier.release.set()
        await _until(lambda: not _marked(user, f"pending-{card.id}"))
        await _until(lambda: not canvas._moving)
        assert "Pending" not in _label_texts(user.current_layout)
    finally:
        barrier.release.set()
        await _release_all(session_id)

    _assert_persisted(session_id, q2, 3)


def _settled_as(card, quadrant_id: str, outcome: str):
    """What the held relocation does once released: fail for real, or succeed stale."""
    if outcome == "fails":
        return None
    # A success response computed before the deletion: the card at its new place.
    return lambda *_: replace(card, quadrant_id=quadrant_id, index=3)


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


def _assert_card_gone(user: User, canvas: Canvas, card_id: str) -> None:
    assert card_id not in canvas._moving and card_id not in canvas._cards
    assert "Mover" not in _label_texts(user.current_layout)
    assert "Pending" not in _label_texts(user.current_layout)
    assert not _marked(user, f"pending-{card_id}")


def _assert_row_kept_deleted(session_id: str, card_id: str) -> None:
    assert cards.list_cards(session_id) == []
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        rows = connection.execute(
            "select deleted_at from public.cards where id = %s", (card_id,)
        ).fetchall()
    assert len(rows) == 1 and rows[0][0] is not None


@pytest.mark.parametrize("outcome", ["fails", "succeeds"])
async def test_deletion_during_a_held_move_removes_the_card_everywhere(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, outcome: str
):
    session_id = create_session("Delete During Move").session_id
    q1, q2 = _load_quadrant_ids(session_id)[:2]
    card = cards.insert_card(session_id, q1, 0, "Mover", "Alice")
    barrier = _Barrier(cards.relocate_card, _settled_as(card, q2, outcome))
    monkeypatch.setattr(cards, "relocate_card", barrier)
    canvases = _record_canvases(monkeypatch)
    try:
        await user.open(f"/sessions/{session_id}")
        await _request_move(user, card.id)
        await asyncio.wait_for(asyncio.to_thread(barrier.entered.wait, 10), timeout=15)
        (canvas,) = canvases
        _assert_pending_in_target(user, card.id)

        # Another participant deletes C while the relocation is unresolved and uncommitted.
        await asyncio.to_thread(cards.soft_delete_card, session_id, card.id)
        await _until(lambda: card.id in canvas._deleted)
        _assert_card_gone(user, canvas, card.id)

        # The relocation then settles; the deleted card must not come back.
        barrier.release.set()
        await asyncio.sleep(1)
        _assert_card_gone(user, canvas, card.id)
    finally:
        barrier.release.set()
        await _release_all(session_id)

    caplog.clear()  # the rejected relocation of a deleted card is logged by design
    _assert_row_kept_deleted(session_id, card.id)
