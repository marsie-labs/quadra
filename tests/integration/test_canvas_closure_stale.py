import asyncio
from dataclasses import asdict, dataclass, field, replace

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.realtime import SessionChange
from quadra.sessions import close_session, create_session

# Shared harness of the closure tests.
from test_canvas_closure_pending import (  # noqa: F401
    _assert_no_overlay,
    _assert_read_only,
    _close_elsewhere,
    _held_operation,
    _notice,
    _record_canvases,
    _texts,
    _until,
)
from test_realtime import _release_all  # noqa: F401


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


@dataclass
class _Hold:
    """Holds the next card load of a canvas until released, then returns `result`.

    `result` replaces what the load itself read, so that the response is an older one.
    """

    armed: bool = False
    result: tuple[list[cards.Card], list[str]] | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


def _gated_canvases(monkeypatch: pytest.MonkeyPatch, hold: _Hold) -> list[Canvas]:
    canvases: list[Canvas] = []

    class _Gated(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

        async def _load(self):
            loaded = await super()._load()
            if not hold.armed:
                return loaded
            hold.armed = False
            hold.entered.set()
            await hold.release.wait()
            return hold.result or loaded

    monkeypatch.setattr(quadra_app, "Canvas", _Gated)
    return canvases


def _shown(canvas: Canvas) -> tuple[dict[str, cards.Card], list[list[str]]]:
    """The card snapshot of the view and what its quadrants show."""
    return dict(canvas._cards), [_texts(column) for column in canvas._columns]


def _assert_closed_and_authoritative(
    user: User, canvas: Canvas, before: tuple, persisted: list[cards.Card]
) -> None:
    """The view still shows exactly the reconciled snapshot, which is the persisted one."""
    assert _shown(canvas) == before
    assert canvas._cards == {card.id: card for card in persisted}
    _assert_no_overlay(user, canvas)
    _assert_read_only(user, canvas)


def _assert_kept_once(
    user: User, canvas: Canvas, card: cards.Card, moved: cards.Card, before: tuple
) -> None:
    """The committed move is shown at its committed position, exactly once."""
    _assert_closed_and_authoritative(user, canvas, before, [moved])
    first, second = (_texts(column) for column in canvas._columns[:2])
    assert (first.count("Mover"), second.count("Mover")) == (0, 1)
    assert canvas._cards[card.id].index == moved.index == 3
    assert not _notice(user).visible


async def test_delayed_success_of_a_pre_closure_move_keeps_the_card_once(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    async with _held_operation(user, monkeypatch, "move", after=True) as (
        session_id,
        card,
        canvas,
        held,
    ):
        await _close_elsewhere(session_id, canvas)
        assert canvas._requests  # the success response is still delayed
        (moved,) = cards.list_cards(session_id)
        assert (moved.quadrant_id, moved.index) != (card.quadrant_id, card.index)
        assert canvas._cards == {card.id: moved}
        before = _shown(canvas)

        held.release.set()
        await _until(lambda: not canvas._requests)
        await asyncio.sleep(0.3)

        _assert_kept_once(user, canvas, card, moved, before)

    caplog.clear()


async def test_delayed_failure_after_closure_reconciliation_is_ignored(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def failing(*args, **kwargs):
        raise cards.CardPersistenceError("delayed failure")

    monkeypatch.setattr(cards, "relocate_card", failing)
    async with _held_operation(user, monkeypatch, "move") as (session_id, card, canvas, held):
        await _close_elsewhere(session_id, canvas)
        assert canvas._requests  # the failure response is still delayed
        before = _shown(canvas)

        held.release.set()
        await _until(lambda: not canvas._requests)
        await asyncio.sleep(0.3)

        _assert_closed_and_authoritative(user, canvas, before, [card])

    caplog.clear()


async def test_pre_closure_load_after_closure_reconciliation_is_ignored(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    hold = _Hold(armed=True)  # the page's own first load is the one held
    canvases = _gated_canvases(monkeypatch, hold)
    session_id = create_session("Closure Stale Load").session_id
    quadrant_ids = _load_quadrant_ids(session_id)
    card = cards.insert_card(session_id, quadrant_ids[0], 0, "Mover", "Alice")
    opening = asyncio.ensure_future(user.open(f"/sessions/{session_id}"))
    try:
        await asyncio.wait_for(hold.entered.wait(), timeout=15)
        (canvas,) = canvases
        # The load read the card where it was; then it moves and the session closes.
        moved = cards.relocate_card(session_id, card.id, quadrant_ids[1], 3)
        await _close_elsewhere(session_id, canvas)
        assert canvas._cards == {card.id: moved}
        before = _shown(canvas)

        hold.release.set()
        await asyncio.wait_for(opening, timeout=15)
        await asyncio.sleep(0.3)

        _assert_closed_and_authoritative(user, canvas, before, [moved])
    finally:
        hold.release.set()
        await asyncio.gather(opening, return_exceptions=True)
        await _release_all(session_id)


async def test_older_reconciliation_response_after_a_newer_one_is_ignored(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    hold = _Hold()
    canvases = _gated_canvases(monkeypatch, hold)
    session_id = create_session("Closure Stale Reconcile").session_id
    quadrant_ids = _load_quadrant_ids(session_id)
    card = cards.insert_card(session_id, quadrant_ids[0], 0, "Mover", "Alice")
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see(marker=f"move-{card.id}", retries=100)
        (canvas,) = canvases
        # The older response shows the card where it was before it moved.
        hold.result = ([card], list(quadrant_ids))
        hold.armed = True
        moved = cards.relocate_card(session_id, card.id, quadrant_ids[1], 3)
        await asyncio.to_thread(close_session, session_id)
        await asyncio.wait_for(hold.entered.wait(), timeout=15)
        older = canvas._reconciliation

        await canvas._retry_reconcile()  # a newer reconciliation of the closed session
        await _until(lambda: canvas._reconciliation.done() and canvas._reconciled)
        assert canvas._cards == {card.id: moved}
        before = _shown(canvas)

        hold.release.set()
        await asyncio.wait_for(older, timeout=15)

        _assert_closed_and_authoritative(user, canvas, before, [moved])
    finally:
        hold.release.set()
        await _release_all(session_id)


def _delayed(canvas: Canvas, record: dict) -> None:
    """Deliver a card notification that was still on its way when closure reconciled."""
    canvas._on_change(
        SessionChange(
            session_id=canvas._session_id,
            table="cards",
            event="UPDATE",
            record=record,
            old_record={},
            commit_timestamp="2000-01-01T00:00:00+00:00",
        )
    )


async def test_delayed_notifications_of_a_card_deleted_before_closure_are_ignored(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    canvases = _record_canvases(monkeypatch)
    session_id = create_session("Closure Deleted Card").session_id
    quadrant_ids = _load_quadrant_ids(session_id)
    kept = cards.insert_card(session_id, quadrant_ids[0], 0, "Kept", "Alice")
    inserted = cards.insert_card(session_id, quadrant_ids[0], 1, "Doomed", "Bob")
    edited = cards.update_card(session_id, inserted.id, "Doomed edit")
    cards.soft_delete_card(session_id, inserted.id)
    await asyncio.to_thread(close_session, session_id)
    try:
        await user.open(f"/sessions/{session_id}")
        (canvas,) = canvases
        await _until(lambda: canvas._reconciled)
        assert canvas._cards == {kept.id: kept}
        before = _shown(canvas)

        _delayed(canvas, asdict(inserted))  # the insert, from before the deletion
        _delayed(canvas, asdict(edited))  # the update, from before the deletion
        # A retained card's notification, even a newer one, leaves the snapshot alone.
        _delayed(canvas, asdict(replace(kept, content="Newer", updated_at="2999-01-01T00:00:00+00:00")))

        _assert_closed_and_authoritative(user, canvas, before, [kept])
        assert not any("Doomed" in text for column in before[1] for text in column)
        assert "Newer" not in _texts(user.current_layout)
    finally:
        await _release_all(session_id)
