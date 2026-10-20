"""Closure dominates every older asynchronous result of a canvas (architecture test).

One harness pauses mutation responses, paginates every load (one card per page) and
reorders session/card notifications, over the three situations in which an older result
could undo closure: a change committed before closure whose response lags, a pending
change the database rejects, and closure during the initial load.
"""

import asyncio
from dataclasses import asdict

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas
from quadra.realtime import SessionChange

# Shared harness of the closure tests.
from test_canvas_closure_initial_load import (
    _assert_complete_closed,
    _cleanup,
    _open_with_paused_partial_load,
    _release_older_load,
)
from test_canvas_closure_pending import (  # noqa: F401
    _NOT_SAVED,
    _assert_no_overlay,
    _assert_read_only,
    _close_elsewhere,
    _held_operation,
    _notice,
    _texts,
    _until,
)
from test_canvas_closure_stale import _shown


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _paginate(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Load one card per page from now on; return the cursor of every page requested."""
    monkeypatch.setattr(cards, "_PAGE_SIZE", 1)
    real_fetch, requested = cards._fetch_page, []

    def recording(session_id, after_id, client):
        requested.append(after_id)
        return real_fetch(session_id, after_id, client)

    monkeypatch.setattr(cards, "_fetch_page", recording)
    return requested


def _notification(canvas: Canvas, table: str, record: dict) -> SessionChange:
    return SessionChange(
        session_id=canvas._session_id,
        table=table,
        event="UPDATE",
        record=record,
        old_record={},
        commit_timestamp="2000-01-01T00:00:00+00:00",
    )


def _replay_reordered(canvas: Canvas, older: cards.Card) -> None:
    """Deliver notifications out of order, after closure was received.

    The card as it was before its last change, a session that is open again, then the
    closure once more: none of them may change what closure reconciled.
    """
    canvas._on_change(_notification(canvas, "cards", asdict(older)))
    canvas._on_change(_notification(canvas, "workspaces", {"status": "open"}))
    canvas._on_change(_notification(canvas, "workspaces", {"status": "closed"}))


def _assert_unchanged(user: User, canvas: Canvas, before: tuple, persisted: list) -> None:
    """The view shows the reconciled snapshot, which is the persisted one, read-only."""
    assert _shown(canvas) == before
    assert canvas._cards == {card.id: card for card in persisted}
    _assert_no_overlay(user, canvas)
    _assert_read_only(user, canvas)


async def _close_beside_held_operation(session_id: str, card: cards.Card, canvas: Canvas) -> list:
    """Another participant adds a card then closes while the operation's response is paused."""
    other = cards.insert_card(session_id, card.quadrant_id, 1, "Other", "Bob")
    await _until(lambda: other.id in canvas._cards)
    await _close_elsewhere(session_id, canvas)
    assert canvas._requests  # the operation's response is still paused
    return cards.list_cards(session_id)


async def _settle(canvas: Canvas, held) -> None:
    """Release the paused response and let everything it triggers finish."""
    held.release.set()
    await _until(lambda: not canvas._requests)
    await asyncio.sleep(0.3)


def _assert_complete_paginated(canvas: Canvas, persisted: list, pages: list) -> None:
    """The reconciled snapshot holds both persisted cards, loaded over several pages."""
    assert len(persisted) == 2
    assert len(pages) >= 3
    assert canvas._cards == {c.id: c for c in persisted}


def _assert_pre_closure_state(canvas: Canvas, card: cards.Card, persisted: list, pages: list):
    """The persisted snapshot is complete, paginated and holds the committed move."""
    (moved,) = [c for c in persisted if c.id == card.id]
    assert (moved.quadrant_id, moved.index) != (card.quadrant_id, card.index)
    _assert_complete_paginated(canvas, persisted, pages)


def _assert_not_saved(user: User) -> None:
    """The participant is told the pending change was not saved."""
    assert _notice(user).visible
    assert _notice(user).text == _NOT_SAVED


def _assert_move_kept(user: User, canvas: Canvas, card: cards.Card) -> None:
    """The moved card shows once, in its committed column, with no notice."""
    first, second = (_texts(column) for column in canvas._columns[:2])
    assert (first.count("Mover"), second.count("Mover")) == (0, 1)
    assert canvas._cards[card.id].index == 3
    assert not _notice(user).visible


async def _pre_closure_commit(user: User, monkeypatch: pytest.MonkeyPatch, pages: list) -> None:
    """A move committed before closure, its response paused until after reconciliation."""
    async with _held_operation(user, monkeypatch, "move", after=True) as (
        session_id,
        card,
        canvas,
        held,
    ):
        persisted = await _close_beside_held_operation(session_id, card, canvas)
        _assert_pre_closure_state(canvas, card, persisted, pages)
        before = _shown(canvas)

        _replay_reordered(canvas, card)  # would restore the older position
        _assert_unchanged(user, canvas, before, persisted)
        await _settle(canvas, held)

        _assert_unchanged(user, canvas, before, persisted)
        _assert_move_kept(user, canvas, card)


async def _rejected_pending_operation(
    user: User, monkeypatch: pytest.MonkeyPatch, pages: list
) -> None:
    """An edit still uncommitted at closure: the database rejects it once released."""
    async with _held_operation(user, monkeypatch, "edit") as (session_id, card, canvas, held):
        persisted = await _close_beside_held_operation(session_id, card, canvas)
        assert persisted[0] == card
        _assert_complete_paginated(canvas, persisted, pages)
        _assert_not_saved(user)
        before = _shown(canvas)

        _replay_reordered(canvas, card)
        await _settle(canvas, held)

        assert cards.list_cards(session_id) == persisted  # the edit never committed
        _assert_unchanged(user, canvas, before, persisted)
        _assert_not_saved(user)


async def _closure_during_initial_load(
    user: User, monkeypatch: pytest.MonkeyPatch, pages: list
) -> None:
    """Closure arrives while the first load, an older partial result, is paused."""
    session_id, persisted, canvas, hold, opening = await _open_with_paused_partial_load(
        user, monkeypatch
    )
    async with _cleanup(session_id, hold, opening):
        await _close_elsewhere(session_id, canvas)
        assert canvas._reconciled and canvas._loading is False
        assert canvas._cards == {card.id: card for card in persisted}
        assert len(pages) >= 3
        before = _shown(canvas)

        _replay_reordered(canvas, persisted[0])
        await _release_older_load(hold, opening)  # the older partial load comes last

        assert _shown(canvas) == before
        _assert_complete_closed(user, canvas, persisted)
        _assert_no_overlay(user, canvas)


async def test_closure_dominates_stale_async_results(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    pages = _paginate(monkeypatch)
    for situation in (
        _pre_closure_commit,
        _rejected_pending_operation,
        _closure_during_initial_load,
    ):
        pages.clear()
        await situation(user, monkeypatch, pages)
    caplog.clear()  # the rejected edit and the replayed notifications are logged by design
