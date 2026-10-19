import asyncio
import threading

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


class _FailingBarrier:
    """Holds a card-service call unresolved until released, then fails it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        self.entered.set()
        assert self.release.wait(timeout=30), "barrier was never released"
        raise cards.CardPersistenceError("persistence rejected the operation")


def _marked(user: User, marker: str) -> list:
    """Elements of the page carrying `marker`; empty when there are none."""
    return [e for e in user.current_layout.descendants() if marker in e._markers]


def _label_texts(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.label)]


async def _until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


async def _submit_card_and_hold(user: User, barrier: _FailingBarrier, session_id: str) -> None:
    await user.open(f"/sessions/{session_id}")
    await user.should_see("DOES NOT", retries=100)
    await _until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))
    user.find(marker="display-name").type("Alice")
    user.find(marker="content-0").type("Compare options")
    user.find(marker="submit-0").click()
    await asyncio.wait_for(asyncio.to_thread(barrier.entered.wait, 10), timeout=15)


def _marked_pending(user: User) -> list:
    return [
        e
        for e in user.current_layout.descendants()
        if any(marker.startswith("pending-") for marker in e._markers)
    ]


def _assert_insertion_rolled_back(user: User, caplog: pytest.LogCaptureFixture) -> None:
    texts = _label_texts(user.current_layout)
    assert "Compare options" not in texts
    assert "Pending" not in texts
    assert "could not be added" in caplog.text  # logged, so expected here


async def test_failed_insertion_removes_the_pending_card_and_shows_a_failure(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Rollback Insert").session_id
    barrier = _FailingBarrier()
    monkeypatch.setattr(cards, "insert_card", barrier)
    try:
        await _submit_card_and_hold(user, barrier, session_id)

        # The insert is held: the card shows as pending, nothing is persisted.
        await _until(lambda: _marked_pending(user))
        assert not cards.list_cards(session_id)

        barrier.release.set()
        await _until(lambda: not _marked_pending(user))
        await user.should_see("Card could not be added")
        _assert_insertion_rolled_back(user, caplog)
    finally:
        barrier.release.set()
        await _release_all(session_id)

    caplog.clear()
    assert not cards.list_cards(session_id)


async def _request_move_and_hold(
    user: User, barrier: _FailingBarrier, session_id: str, card: cards.Card
) -> None:
    await user.open(f"/sessions/{session_id}")
    await user.should_see(marker=f"move-{card.id}", retries=100)
    (target,) = user.find(marker=f"move-quadrant-{card.id}").elements
    (at,) = user.find(marker=f"move-index-{card.id}").elements
    target.value, at.value = 1, 3
    user.find(marker=f"move-{card.id}").click()
    await asyncio.wait_for(asyncio.to_thread(barrier.entered.wait, 10), timeout=15)


def _assert_move_rolled_back(
    user: User, canvas: Canvas, card: cards.Card, caplog: pytest.LogCaptureFixture
) -> None:
    assert not canvas._moving
    _assert_card_shown_only_in_origin(canvas)
    shown = canvas._cards[card.id]
    assert (shown.quadrant_id, shown.index) == (card.quadrant_id, card.index)
    (shown_index,) = user.find(marker=f"move-index-{card.id}").elements
    assert shown_index.value == card.index
    assert "could not be moved" in caplog.text  # logged, so expected here


def _assert_card_shown_only_in_origin(canvas: Canvas) -> None:
    origin, other = canvas._columns[0], canvas._columns[1]
    assert _label_texts(origin).count("Mover") == 1
    assert "Mover" not in _label_texts(other)


async def test_failed_movement_returns_the_card_to_its_original_quadrant_and_index(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Rollback Move").session_id
    q1, q2 = _load_quadrant_ids(session_id)[:2]
    card = cards.insert_card(session_id, q1, 0, "Mover", "Alice")
    barrier = _FailingBarrier()
    monkeypatch.setattr(cards, "relocate_card", barrier)
    canvases = _record_canvases(monkeypatch)
    try:
        await _request_move_and_hold(user, barrier, session_id, card)

        # The move is held: the card shows as pending, the database has not changed.
        (canvas,) = canvases
        assert _marked(user, f"pending-{card.id}")
        assert canvas._moving[card.id].quadrant_id == q2

        barrier.release.set()
        await _until(lambda: not _marked(user, f"pending-{card.id}"))
        await user.should_see("Card could not be moved")
        _assert_move_rolled_back(user, canvas, card, caplog)
    finally:
        barrier.release.set()
        await _release_all(session_id)

    caplog.clear()
    (persisted,) = cards.list_cards(session_id)
    assert (persisted.quadrant_id, persisted.index) == (q1, 0)
