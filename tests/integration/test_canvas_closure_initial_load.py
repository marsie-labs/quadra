import asyncio
import contextlib

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards, realtime
from quadra.canvas import _load_quadrant_ids
from quadra.sessions import create_session

# Shared harness of the closure tests.
from test_canvas_closure_pending import (  # noqa: F401
    _assert_no_overlay,
    _assert_read_only,
    _close_elsewhere,
    _texts,
    _until,
)
from test_canvas_closure_stale import _gated_canvases, _Hold
from test_realtime import _release_all  # noqa: F401


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _closed_label(canvas) -> bool:
    return canvas._closed_label.visible and not canvas._close_button.visible


def _shown_texts(canvas) -> list[str]:
    return [text for column in canvas._columns for text in _texts(column)]


async def _open_with_paused_partial_load(user: User, monkeypatch: pytest.MonkeyPatch):
    """Open S whose first load is held with an older result that omits cards.

    Returns the session id, the complete persisted cards, the canvas, the hold and the
    pending page opening, with Realtime registered.
    """
    hold = _Hold(armed=True)
    canvases = _gated_canvases(monkeypatch, hold)
    session_id = create_session("Closure Initial Load").session_id
    quadrant_ids = _load_quadrant_ids(session_id)
    first = cards.insert_card(session_id, quadrant_ids[0], 0, "Older", "Alice")
    opening = asyncio.ensure_future(user.open(f"/sessions/{session_id}"))
    await asyncio.wait_for(hold.entered.wait(), timeout=15)
    (canvas,) = canvases
    # The held response is older: it only knows the first card.
    hold.result = ([first], list(quadrant_ids))
    second = cards.insert_card(session_id, quadrant_ids[1], 0, "Newer", "Bob")
    assert realtime.view_ids(session_id)  # registered before the load returned
    return session_id, [first, second], canvas, hold, opening


@contextlib.asynccontextmanager
async def _cleanup(session_id: str, hold: _Hold, opening: asyncio.Future):
    """Release the held load and every Realtime view however the test ends."""
    try:
        yield
    finally:
        hold.release.set()
        await asyncio.gather(opening, return_exceptions=True)
        await _release_all(session_id)


def _assert_complete_closed(user: User, canvas, persisted: list) -> None:
    assert canvas._cards == {card.id: card for card in persisted}
    assert {"Older", "Newer"} <= set(_shown_texts(canvas))
    assert not canvas._reload_failure.visible
    _assert_read_only(user, canvas)


def _assert_failed_and_closed(user: User, canvas) -> None:
    """A failed reconciliation: retry offered, nothing incomplete shown as final."""
    assert canvas._reload_failure.visible and not canvas._reconciled
    assert canvas._cards == {} and "Older" not in _shown_texts(canvas)
    assert _closed_label(canvas)
    _assert_read_only(user, canvas)


async def _release_older_load(hold: _Hold, opening: asyncio.Future) -> None:
    hold.release.set()
    await asyncio.wait_for(opening, timeout=15)
    await asyncio.sleep(0.3)


def _fail_first_load(canvas, monkeypatch: pytest.MonkeyPatch) -> list:
    """Make the canvas's first reconciliation load fail; return the call log."""
    real_load, calls = canvas._load, []

    async def flaky_load():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("reload failed")
        return await real_load()

    monkeypatch.setattr(canvas, "_load", flaky_load)
    return calls


async def test_closure_during_the_initial_load_settles_on_the_complete_closed_set(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id, persisted, canvas, hold, opening = await _open_with_paused_partial_load(
        user, monkeypatch
    )
    async with _cleanup(session_id, hold, opening):
        await _close_elsewhere(session_id, canvas)
        assert canvas._reconciled and canvas._loading is False
        assert canvas._cards == {card.id: card for card in persisted}

        await _release_older_load(hold, opening)

        _assert_complete_closed(user, canvas, persisted)
        assert _closed_label(canvas)
        _assert_no_overlay(user, canvas)


async def test_failed_reconciliation_during_the_initial_load_offers_retry(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id, persisted, canvas, hold, opening = await _open_with_paused_partial_load(
        user, monkeypatch
    )
    async with _cleanup(session_id, hold, opening):
        calls = _fail_first_load(canvas, monkeypatch)
        await _close_elsewhere(session_id, canvas)
        assert canvas._reload_failure.visible and not canvas._reconciled

        # Releasing the older partial load must not present it as the final result.
        await _release_older_load(hold, opening)
        _assert_failed_and_closed(user, canvas)

        await canvas._retry_reconcile()
        await _until(lambda: canvas._reconciliation.done() and canvas._reconciled)
        assert len(calls) == 2
        _assert_complete_closed(user, canvas, persisted)
    caplog.clear()
