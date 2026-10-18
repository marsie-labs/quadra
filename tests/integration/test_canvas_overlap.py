"""A held response overlapped by a newer remote change ends in the authoritative state."""

import asyncio
import threading
from dataclasses import replace

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.sessions import create_session

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401

_CONTENTS = {"Original", "Mine", "Fresh", "Theirs"}
_REAL = {
    "insert_card": cards.insert_card,
    "update_card": cards.update_card,
    "relocate_card": cards.relocate_card,
    "soft_delete_card": cards.soft_delete_card,
}


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


class _Held:
    """Holds a `cards` function until released.

    Before the commit, it then returns `response` instead of committing, i.e. a
    response computed earlier than whatever happened meanwhile. After the commit, it
    returns the committed card late.
    """

    def __init__(self, name: str, response=None, after_commit: bool = False) -> None:
        self._real = _REAL[name]
        self._response = response
        self._after_commit = after_commit
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        result = self._real(*args, **kwargs) if self._after_commit else None
        self.entered.set()
        assert self.release.wait(timeout=30), "held response was never released"
        if self._after_commit:
            return result
        return self._response(*args) if self._response else self._real(*args, **kwargs)


def _hold(monkeypatch: pytest.MonkeyPatch, name: str, **options) -> _Held:
    held = _Held(name, **options)
    monkeypatch.setattr(cards, name, held)
    return held


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


async def _until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


async def _entered(held: _Held) -> None:
    await asyncio.wait_for(asyncio.to_thread(held.entered.wait, 10), timeout=15)


def _marked(user: User, marker: str) -> list:
    return [e for e in user.current_layout.descendants() if marker in e._markers]


def _label_texts(root) -> list[str]:
    return [e.text for e in root.descendants() if isinstance(e, ui.label)]


def _shown(canvas: Canvas) -> dict[int, list[str]]:
    """Card contents shown in each quadrant position, which hold any."""
    shown = {position: _contents(column) for position, column in enumerate(canvas._columns)}
    return {position: texts for position, texts in shown.items() if texts}


def _contents(column) -> list[str]:
    return [text for text in _label_texts(column) if text in _CONTENTS]


def _assert_no_optimistic_trace(user: User, canvas: Canvas, card_id: str) -> None:
    assert not (canvas._pending or canvas._moving or canvas._drafts)
    _assert_no_optimistic_view(user, card_id)


def _assert_no_optimistic_view(user: User, card_id: str) -> None:
    assert not _marked(user, f"pending-{card_id}")
    assert not _marked(user, f"save-{card_id}")
    assert "Pending" not in _label_texts(user.current_layout)


def _assert_gone(canvas: Canvas, session_id: str, card_id: str) -> None:
    assert _shown(canvas) == {}
    assert card_id not in canvas._cards
    assert cards.list_cards(session_id) == []


def _assert_authoritative(
    user: User, canvas: Canvas, session_id: str, card_id: str, expected: tuple[str, int] | None
) -> None:
    """The card is shown exactly as `expected` (content, quadrant position), or is gone."""
    _assert_no_optimistic_trace(user, canvas, card_id)
    if expected is None:
        _assert_gone(canvas, session_id, card_id)
        return
    content, position = expected
    assert _shown(canvas) == {position: [content]}
    (card,) = cards.list_cards(session_id)
    assert canvas._cards[card_id] == card
    assert (card.content, card.quadrant_id) == (content, canvas._quadrant_ids[position])


async def _remote(
    session_id: str, canvas: Canvas, card_id: str, change: str, content: str = "Original"
) -> tuple | None:
    """Another participant changes the card; the canvas has seen it. Expected end state."""
    q3 = canvas._quadrant_ids[2]
    if change == "edit":
        await asyncio.to_thread(_REAL["update_card"], session_id, card_id, "Theirs")
        await _until(lambda: card_id in canvas._cards and canvas._cards[card_id].content == "Theirs")
    elif change == "move":
        await asyncio.to_thread(_REAL["relocate_card"], session_id, card_id, q3, 0)
        await _until(lambda: card_id in canvas._cards and canvas._cards[card_id].quadrant_id == q3)
    else:
        await asyncio.to_thread(_REAL["soft_delete_card"], session_id, card_id)
        await _until(lambda: card_id in canvas._deleted)
    return {"edit": ("Theirs", 0), "move": (content, 2), "delete": None}[change]


async def _settle(held: _Held, session_id: str) -> None:
    held.release.set()
    await asyncio.sleep(1)


async def _open_with_card(
    user: User, session_id: str, canvases: list[Canvas], content: str = "Original"
) -> tuple[Canvas, cards.Card]:
    card = cards.insert_card(session_id, _load_quadrant_ids(session_id)[0], 0, content, "Alice")
    await user.open(f"/sessions/{session_id}")
    await user.should_see(marker=f"move-{card.id}", retries=100)
    (canvas,) = canvases
    return canvas, card


async def _request_move(user: User, card_id: str, position: int, index: int) -> None:
    (target,) = user.find(marker=f"move-quadrant-{card_id}").elements
    (at,) = user.find(marker=f"move-index-{card_id}").elements
    target.value, at.value = position, index
    user.find(marker=f"move-{card_id}").click()


@pytest.mark.parametrize("change", ["edit", "move", "delete"])
async def test_held_insert_overlapped_by_a_remote_change(
    user: User, monkeypatch: pytest.MonkeyPatch, change: str
):
    session_id = create_session(f"Overlap Insert {change}").session_id
    held = _hold(monkeypatch, "insert_card", after_commit=True)
    canvases = _record_canvases(monkeypatch)
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see("DOES NOT", retries=100)
        (canvas,) = canvases
        await _until(lambda: all(b.enabled for b in canvas._submit_buttons))
        user.find(marker="display-name").type("Alice")
        user.find(marker="content-0").type("Fresh")
        user.find(marker="submit-0").click()
        await _entered(held)
        (card,) = cards.list_cards(session_id)

        expected = await _remote(session_id, canvas, card.id, change, "Fresh")
        await _settle(held, session_id)
        _assert_authoritative(user, canvas, session_id, card.id, expected)
    finally:
        held.release.set()
        await _release_all(session_id)


@pytest.mark.parametrize("change", ["edit", "move", "delete"])
async def test_held_edit_overlapped_by_a_remote_change(
    user: User, monkeypatch: pytest.MonkeyPatch, change: str
):
    session_id = create_session(f"Overlap Edit {change}").session_id
    canvases = _record_canvases(monkeypatch)
    try:
        canvas, card = await _open_with_card(user, session_id, canvases)
        held = _hold(
            monkeypatch, "update_card", response=lambda *_: replace(card, content="Mine")
        )
        user.find(marker=f"edit-{card.id}").click()
        await user.should_see(marker=f"save-{card.id}")
        (editor,) = user.find(marker=f"edit-content-{card.id}").elements
        editor.value = "Mine"
        user.find(marker=f"save-{card.id}").click()
        await _entered(held)

        expected = await _remote(session_id, canvas, card.id, change)
        await _settle(held, session_id)
        _assert_authoritative(user, canvas, session_id, card.id, expected)
    finally:
        held.release.set()
        await _release_all(session_id)


@pytest.mark.parametrize("change", ["edit", "move", "delete"])
async def test_held_move_overlapped_by_a_remote_change(
    user: User, monkeypatch: pytest.MonkeyPatch, change: str
):
    session_id = create_session(f"Overlap Move {change}").session_id
    q2 = _load_quadrant_ids(session_id)[1]
    canvases = _record_canvases(monkeypatch)
    try:
        canvas, card = await _open_with_card(user, session_id, canvases)
        held = _hold(
            monkeypatch,
            "relocate_card",
            response=lambda *_: replace(card, quadrant_id=q2, index=3),
        )
        await _request_move(user, card.id, 1, 3)
        await _entered(held)
        assert _marked(user, f"pending-{card.id}")

        expected = await _remote(session_id, canvas, card.id, change)
        await _settle(held, session_id)
        _assert_authoritative(user, canvas, session_id, card.id, expected)
    finally:
        held.release.set()
        await _release_all(session_id)


async def test_older_held_move_to_q2_overlapped_by_newer_move_to_q3_ends_only_in_q3(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Overlap Land").session_id
    q2, q3 = _load_quadrant_ids(session_id)[1:3]
    canvases = _record_canvases(monkeypatch)
    try:
        canvas, card = await _open_with_card(user, session_id, canvases)
        held = _hold(
            monkeypatch,
            "relocate_card",
            response=lambda *_: replace(card, quadrant_id=q2, index=3),
        )
        await _request_move(user, card.id, 1, 3)
        await _entered(held)
        assert _shown(canvas) == {1: ["Original"]}  # pending in Q2

        await asyncio.to_thread(_REAL["relocate_card"], session_id, card.id, q3, 0)
        await _until(lambda: card.id in canvas._cards and canvas._cards[card.id].quadrant_id == q3)

        rendered: list[str] = []
        render = canvas._render

        def recording_render(quadrant_id: str) -> None:
            rendered.append(quadrant_id)
            render(quadrant_id)

        monkeypatch.setattr(canvas, "_render", recording_render)
        await _settle(held, session_id)

        # The stale response is discarded, but both the cleared target and the
        # authoritative quadrant are rendered again.
        assert {q2, q3} <= set(rendered)
        assert _shown(canvas) == {2: ["Original"]}  # Q1 and Q2 show no copy
        _assert_authoritative(user, canvas, session_id, card.id, ("Original", 2))
    finally:
        held.release.set()
        await _release_all(session_id)
