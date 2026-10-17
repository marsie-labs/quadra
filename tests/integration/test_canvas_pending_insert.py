import asyncio
import threading

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
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
    """Holds `cards.insert_card` unresolved until released, recording its card id."""

    def __init__(self, real_insert) -> None:
        self._real_insert = real_insert
        self.entered = threading.Event()
        self.release = threading.Event()
        self.card_id: str | None = None

    def __call__(self, *args, **kwargs):
        self.card_id = kwargs.get("card_id")
        self.entered.set()
        assert self.release.wait(timeout=30), "barrier was never released"
        return self._real_insert(*args, **kwargs)


async def _open_ready(user: User, session_id: str) -> None:
    """Open the page and wait until the composers accept submissions."""
    await user.open(f"/sessions/{session_id}")
    await user.should_see("DOES NOT", retries=100)
    submit = user.find(marker="submit-0")
    async with asyncio.timeout(10):
        while not all(button.enabled for button in submit.elements):
            await asyncio.sleep(0.05)


async def _until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


def _marked(user: User, marker: str) -> list:
    """Elements of the page carrying `marker`; empty when there are none."""
    return [e for e in user.current_layout.descendants() if marker in e._markers]


def _quadrant_texts(element) -> list[str]:
    """Texts of the labels inside the quadrant card holding the pending `element`."""
    column = element.parent_slot.parent
    quadrant = column.parent_slot.parent
    return [e.text for e in quadrant.descendants() if isinstance(e, ui.label)]


def _label_texts(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.label)]


def _assert_pending_in_first_quadrant(user: User, session_id: str, card_id: str) -> None:
    """The held insert already shows as one pending card under `card_id` in Q1."""
    (element,) = _marked(user, f"pending-{card_id}")
    assert _label_texts(element) == ["Compare options", "Alice", "Pending"]
    quadrant_texts = _quadrant_texts(element)
    assert ("IS" in quadrant_texts, "DOES" in quadrant_texts) == (True, False)
    assert not cards.list_cards(session_id)


async def _assert_confirmed(user: User, session_id: str, card_id: str) -> None:
    """After release the pending card is replaced by the persisted one."""
    await _until(lambda: not _marked(user, f"pending-{card_id}"))
    await _until(lambda: [c.id for c in cards.list_cards(session_id)] == [card_id])
    assert "Pending" not in _label_texts(user.current_layout)


async def test_pending_card_shows_in_target_quadrant_until_the_insert_resolves(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Pending Insert").session_id
    barrier = _Barrier(cards.insert_card)
    monkeypatch.setattr(cards, "insert_card", barrier)
    try:
        await _open_ready(user, session_id)
        user.find(marker="display-name").type("Alice")
        user.find(marker="content-0").type("Compare options")
        user.find(marker="submit-0").click()
        await asyncio.wait_for(asyncio.to_thread(barrier.entered.wait, 10), timeout=15)

        # The request is held: the card must already be shown, pending, under its id.
        assert barrier.card_id
        _assert_pending_in_first_quadrant(user, session_id, barrier.card_id)

        barrier.release.set()
        await _assert_confirmed(user, session_id, barrier.card_id)
    finally:
        barrier.release.set()
        await _release_all(session_id)

    (card,) = cards.list_cards(session_id)
    assert card.id == barrier.card_id
    assert card.content == "Compare options"
