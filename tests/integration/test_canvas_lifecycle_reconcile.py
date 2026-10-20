import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards, realtime
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.sessions import close_session, create_session

# Shared harness of the closed-session canvas tests.
from test_canvas_closed_load import (
    _assert_cards_rendered_once_in_order,
    _assert_no_deleted_and_exact_count,
    _assert_quadrants_once,
    _closed_session_with_deleted_cards,
)
from test_canvas_closure_pending import _assert_no_overlay, _assert_read_only, _texts, _until
from test_realtime import _release_all  # noqa: F401


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _closed_label(canvas: Canvas) -> bool:
    return canvas._closed_label.visible and not canvas._close_button.visible


def _card_texts(user: User) -> list[str]:
    return [text for text in _texts(user.current_layout) if text.startswith("is-")]


def _shown_texts(canvas: Canvas) -> list[str]:
    return [text for column in canvas._columns for text in _texts(column)]


def _reconciled_closed(canvas: Canvas) -> bool:
    return canvas._reconciliation is not None and canvas._reconciliation.done()


def _assert_closed_and_read_only(user: User, canvas: Canvas, card) -> None:
    assert canvas._closed and canvas._reconciled
    assert canvas._cards == {card.id: card}
    assert _closed_label(canvas)
    _assert_read_only(user, canvas)


async def test_closure_before_registration_is_found_by_the_lifecycle_read(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    """S is open when the page reads it, and closes before the Realtime registration."""
    canvases: list[Canvas] = []
    real_register = realtime.register_view
    session_id = create_session("Closed Before Registration").session_id
    quadrant_ids = _load_quadrant_ids(session_id)
    card = cards.insert_card(session_id, quadrant_ids[0], 0, "Before", "Alice")

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            assert session.status == "open"  # the page's own lifecycle read
            super().__init__(session)
            canvases.append(self)

    async def close_then_register(*args, **kwargs):
        assert await asyncio.to_thread(close_session, session_id) == "closed"
        return await real_register(*args, **kwargs)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    monkeypatch.setattr(realtime, "register_view", close_then_register)
    try:
        await user.open(f"/sessions/{session_id}")
        (canvas,) = canvases
        await _until(lambda: _reconciled_closed(canvas))
        assert realtime.view_ids(session_id)  # registered, after the closure
        assert not canvas._loading
        assert _shown_texts(canvas).count("Before") == 1
        assert not canvas._reload_failure.visible
        _assert_no_overlay(user, canvas)
        _assert_closed_and_read_only(user, canvas, card)
    finally:
        await _release_all(session_id)


def _slow_read(
    canvases: list[Canvas], started: asyncio.Event, release: asyncio.Event
) -> type[Canvas]:
    """A Canvas whose first lifecycle read answers 'open' only once released."""

    class _SlowRead(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

        async def _read_status(self):
            status = await super()._read_status()
            assert status == "open"  # read before the closure
            started.set()
            await release.wait()
            return status

    return _SlowRead


def _assert_latched(user: User, canvas: Canvas, card, generation: int) -> None:
    assert not canvas._editable  # never reverted by the late 'open'
    assert canvas._generation == generation  # and closure was not applied twice
    assert _shown_texts(canvas).count("Stable") == 1
    _assert_closed_and_read_only(user, canvas, card)


async def test_closure_pushed_during_the_lifecycle_read_is_not_lost(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    """The post-registration read answers 'open' late; the pushed closure stays latched."""
    canvases: list[Canvas] = []
    read_started, read_release = asyncio.Event(), asyncio.Event()
    session_id = create_session("Closed During Read").session_id
    quadrant_ids = _load_quadrant_ids(session_id)
    card = cards.insert_card(session_id, quadrant_ids[0], 0, "Stable", "Alice")

    monkeypatch.setattr(
        quadra_app, "Canvas", _slow_read(canvases, read_started, read_release)
    )
    opening = asyncio.ensure_future(user.open(f"/sessions/{session_id}"))
    try:
        await asyncio.wait_for(read_started.wait(), timeout=15)
        (canvas,) = canvases
        assert realtime.view_ids(session_id)
        assert not canvas._closed
        await asyncio.to_thread(close_session, session_id)
        await _until(lambda: canvas._closed)
        await _until(lambda: _reconciled_closed(canvas))
        generation = canvas._generation

        read_release.set()
        await asyncio.wait_for(opening, timeout=15)
        await asyncio.sleep(0.3)

        _assert_latched(user, canvas, card, generation)
    finally:
        read_release.set()
        await asyncio.gather(opening, return_exceptions=True)
        await _release_all(session_id)


def _failing_first_load(canvases: list[Canvas], calls: list[None]) -> type[Canvas]:
    """A Canvas whose first card load fails and whose later loads are the real ones."""

    class _FailingFirstLoad(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

        async def _load(self):
            calls.append(None)
            if len(calls) == 1:
                raise RuntimeError("load failed")
            return await super()._load()

    return _FailingFirstLoad


def _assert_closed_read_only(user: User, canvas: Canvas) -> None:
    assert _closed_label(canvas)
    _assert_read_only(user, canvas)


def _assert_load_failed(user: User, canvas: Canvas, calls: list[None]) -> None:
    assert len(calls) == 1
    assert not canvas._reconciled
    assert canvas._cards == {}
    assert not _card_texts(user)
    _assert_closed_read_only(user, canvas)


def _assert_retry_rendered_all(user: User, canvas: Canvas, calls: list[None]) -> None:
    texts = _texts(user.current_layout)
    assert len(calls) == 2
    assert not canvas._reload_failure.visible
    _assert_cards_rendered_once_in_order(texts)
    _assert_quadrants_once(texts)
    _assert_no_deleted_and_exact_count(texts)
    assert _closed_label(canvas)
    _assert_no_overlay(user, canvas)
    _assert_read_only(user, canvas)


async def test_failed_load_of_a_closed_session_offers_a_retry_that_shows_every_card_once(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    canvases: list[Canvas] = []
    calls: list[None] = []

    monkeypatch.setattr(quadra_app, "Canvas", _failing_first_load(canvases, calls))
    session_id = _closed_session_with_deleted_cards()
    try:
        await user.open(f"/sessions/{session_id}")
        (canvas,) = canvases
        await _until(lambda: canvas._reload_failure.visible)
        await user.should_see("Closed session cards could not be reloaded")
        await user.should_see(marker="retry-reload")
        _assert_load_failed(user, canvas, calls)

        user.find(marker="retry-reload").click()
        await _until(lambda: canvas._reconciled)

        _assert_retry_rendered_all(user, canvas, calls)
    finally:
        await _release_all(session_id)
    caplog.clear()  # the failed load is logged on purpose
