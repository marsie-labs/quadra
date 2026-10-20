import asyncio

import pytest
from nicegui import Client, ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import canvas as canvas_module
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.realtime import SessionChange
from quadra.sessions import create_session, get_session_status

# Shared harness of the Realtime and propagation tests.
from test_canvas_propagation import (
    _clock_offset,
    _commit_time,
    _no_timer,
    _observe,
    _open_ready,
    _record_updates,
    _until,
)
from test_realtime import LATENCY_LIMIT, _release_all  # noqa: F401

_MUTATION_MARKS = ("edit-", "delete-", "move-", "save-", "confirm-delete-")


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    """Record each canvas the page builds, with the session changes its view receives."""
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            self.session_changes: list[SessionChange] = []
            canvases.append(self)

        def _on_change(self, change: SessionChange) -> None:
            if change.table == "workspaces":
                self.session_changes.append(change)
            super()._on_change(change)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


def _closed_label(user: User) -> ui.label:
    return next(
        e for e in user.current_layout.descendants() if "session-closed" in e._markers
    )


def _closed_label_shown(user: User) -> bool:
    return _closed_label(user).visible


def _marks(user: User) -> list[str]:
    return [mark for element in user.current_layout.descendants() for mark in element._markers]


def _submit_buttons(user: User) -> list[ui.button]:
    return [
        element
        for element in user.current_layout.descendants()
        if isinstance(element, ui.button) and any(m.startswith("submit-") for m in element._markers)
    ]


def _forbid_status_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """B must learn of the closure from the push: a status read would be polling."""
    reads: list[str] = []

    def read(session_id: str) -> str:
        reads.append(session_id)
        return "open"

    monkeypatch.setattr(canvas_module, "get_session_status", read)
    return reads


def _forbid_mutations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any card mutation request a canvas still sends."""
    requested: list[str] = []
    for name in ("insert_card", "update_card", "relocate_card", "soft_delete_card"):
        monkeypatch.setattr(cards, name, lambda *a, _name=name, **k: requested.append(_name))
    return requested


async def _try_every_mutation(canvas: Canvas, card_id: str) -> None:
    """Drive each mutation entry point of B's canvas as a participant could."""
    with canvas._client:
        composer = ui.input(value="late card")
        dialog = ui.dialog()
    canvas._display_name.value = "Bob"
    canvas._drafts[card_id] = "late edit"
    await canvas._submit(0, composer)
    await canvas._save_edit(card_id)
    await canvas._move(card_id, 1, 0)
    await canvas._confirm_delete(card_id, dialog)


async def _close_from_a_and_observe_b(
    user: User, user_b: User, client_b: Client, canvas_b: Canvas, monkeypatch: pytest.MonkeyPatch
) -> tuple[float, float, float, list[str]]:
    """A closes S; return when B's page and socket showed it, the commit time, and status reads."""
    offset = await asyncio.to_thread(_clock_offset)
    closed_label = _closed_label(user_b)
    updates = _record_updates(monkeypatch, str(client_b.id))
    status_reads = _forbid_status_reads(monkeypatch)
    observed = asyncio.create_task(_observe(lambda: _closed_label_shown(user_b)))

    user.find(marker="close-session").click()  # A closes S through its page

    page = await asyncio.wait_for(observed, timeout=10)
    await _until(lambda: any(closed_label.id in data for _, data in updates))
    socket = next(at for at, data in updates if closed_label.id in data)
    await _until(lambda: _closure_push(canvas_b) is not None)
    commit = _commit_time(_closure_push(canvas_b), offset)
    return page, socket, commit, status_reads


async def _assert_read_only(
    user_b: User, canvas_b: Canvas, card_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B has no controls, composers off, and no mutation request goes out."""
    await _until(lambda: f"edit-{card_id}" not in _marks(user_b))
    assert not [mark for mark in _marks(user_b) if mark.startswith(_MUTATION_MARKS)]
    assert not any(button.enabled for button in _submit_buttons(user_b))
    await user_b.should_not_see(marker="close-session")
    requested = _forbid_mutations(monkeypatch)
    await _try_every_mutation(canvas_b, card_id)
    assert requested == []


def _assert_closed_by_push(
    canvas_b: Canvas, timings: tuple[float, float, float, list[str]]
) -> None:
    """B shows S closed less than 2000ms after the closure commit, pushed, not read."""
    page, socket, commit, status_reads = timings
    assert page - commit < LATENCY_LIMIT
    assert socket - commit < LATENCY_LIMIT
    assert canvas_b._closed and not canvas_b._editable
    assert status_reads == []


async def test_closure_reaches_another_connected_view_read_only_without_polling(
    user: User, create_user, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Closure Push").session_id
    card = cards.insert_card(session_id, _load_quadrant_ids(session_id)[0], 0, "Shared", "Alice")
    canvases = _record_canvases(monkeypatch)
    monkeypatch.setattr(ui, "timer", _no_timer)
    user_b = create_user()
    try:
        await _open_ready(user, session_id)
        client_b: Client = await _open_ready(user_b, session_id)
        await user_b.should_see(marker=f"edit-{card.id}", retries=100)
        _, canvas_b = canvases
        assert not _closed_label_shown(user_b)
        assert all(button.enabled for button in _submit_buttons(user_b))

        timings = await _close_from_a_and_observe_b(user, user_b, client_b, canvas_b, monkeypatch)
        _assert_closed_by_push(canvas_b, timings)

        await _assert_read_only(user_b, canvas_b, card.id, monkeypatch)
    finally:
        await _release_all(session_id)

    assert get_session_status(session_id) == "closed"


def _closure_push(canvas: Canvas) -> SessionChange | None:
    """The session change B's view received that closed the session, if any."""
    return next((c for c in canvas.session_changes if c.record.get("status") == "closed"), None)
