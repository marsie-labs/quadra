import asyncio
import os

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards, realtime
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.realtime import SessionChange
from quadra.sessions import create_session, get_session_status

# Shared harness of the Realtime and closure push tests.
from test_canvas_closure_push import _closed_label_shown, _marks, _submit_buttons
from test_canvas_propagation import _open_ready, _until
from test_realtime import LATENCY_LIMIT, _release_all


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> dict[str, Canvas]:
    """Record each canvas the page builds by session, with every change its view receives."""
    canvases: dict[str, Canvas] = {}

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            self.changes: list[SessionChange] = []
            canvases[session.session_id] = self

        def _on_change(self, change: SessionChange) -> None:
            self.changes.append(change)
            super()._on_change(change)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


def _snapshot(session_id: str) -> list[dict]:
    """Every persisted card row of the session, soft-deleted ones included."""
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        rows = connection.execute(
            "select row_to_json(c)::jsonb from public.cards c where c.session_id = %s order by c.id",
            (session_id,),
        ).fetchall()
    return [row[0] for row in rows]


def _assert_open_state(canvas: Canvas) -> None:
    """Neither the canvas nor the persisted session was marked closed."""
    assert canvas._editable
    assert not canvas._closed
    assert get_session_status(canvas._session_id) == "open"


def _assert_controls_shown(user: User, card_id: str) -> None:
    """Every control of an editable canvas is still rendered and enabled."""
    assert not _closed_label_shown(user)
    assert f"edit-{card_id}" in _marks(user)
    assert "close-session" in _marks(user)
    assert all(button.enabled for button in _submit_buttons(user))


async def _assert_insert_accepted(canvas: Canvas) -> None:
    """A real card insert through the canvas is persisted."""
    with canvas._client:
        composer = ui.input(value="still editable")
    canvas._display_name.value = "Bob"
    await canvas._submit(0, composer)
    contents = [row["content"] for row in _snapshot(canvas._session_id)]
    assert "still editable" in contents


async def _assert_editable(user: User, canvas: Canvas, card_id: str) -> None:
    """The canvas still exposes every control and accepts a real card insert."""
    _assert_open_state(canvas)
    _assert_controls_shown(user, card_id)
    await _assert_insert_accepted(canvas)


async def _close_through_page(user: User, canvas_s: Canvas) -> None:
    """Close S through its own page and wait for the closure to reach S's view."""
    user.find(marker="close-session").click()
    await _until(lambda: any(c.record.get("status") == "closed" for c in canvas_s.changes))
    assert canvas_s._closed  # the closure was pushed to S's own view
    await asyncio.sleep(LATENCY_LIMIT)  # a misrouted closure would have arrived by now


def _assert_t_untouched(canvas_t: Canvas, views_t: set, before: list[dict]) -> None:
    """S's closure left T's views and persisted cards as they were."""
    assert canvas_t.changes == []  # S's closure event never reached a view of T
    assert realtime.view_ids(canvas_t._session_id) == views_t
    assert _snapshot(canvas_t._session_id) == before  # T's persisted cards are unchanged


def _views_of_t(session_s: str, session_t: str) -> set:
    """T's single connected view, which is not one of S's views."""
    views_t = realtime.view_ids(session_t)
    assert len(views_t) == 1
    assert realtime.view_ids(session_s).isdisjoint(views_t)
    return views_t


async def test_closing_one_session_leaves_another_open_session_untouched(
    user: User, create_user, monkeypatch: pytest.MonkeyPatch
):
    session_s = create_session("Closure Isolation S").session_id
    session_t = create_session("Closure Isolation T").session_id
    card_s = cards.insert_card(session_s, _load_quadrant_ids(session_s)[0], 0, "S card", "Alice")
    card_t = cards.insert_card(session_t, _load_quadrant_ids(session_t)[0], 0, "T card", "Carol")
    canvases = _record_canvases(monkeypatch)
    user_t = create_user()
    try:
        await _open_ready(user, session_s)
        await _open_ready(user_t, session_t)
        await user_t.should_see(marker=f"edit-{card_t.id}", retries=100)
        canvas_s, canvas_t = canvases[session_s], canvases[session_t]
        views_t = _views_of_t(session_s, session_t)
        before = _snapshot(session_t)
        assert [row["id"] for row in before] == [card_t.id]

        await _close_through_page(user, canvas_s)

        assert get_session_status(session_s) == "closed"
        _assert_t_untouched(canvas_t, views_t, before)
        assert [row["id"] for row in _snapshot(session_s)] == [card_s.id]

        await _assert_editable(user_t, canvas_t, card_t.id)
    finally:
        await _release_all(session_s)
        await _release_all(session_t)
