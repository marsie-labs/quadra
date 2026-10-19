import asyncio
import threading

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import canvas, cards
from quadra.canvas import _load_quadrant_ids as _quadrant_ids
from quadra.sessions import close_session, create_session, get_session_status

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _failing_close(session_id: str, *args, **kwargs):
    raise RuntimeError("closure request failed before committing")


def _marked(user: User, marker: str) -> list:
    return [e for e in user.current_layout.descendants() if marker in e._markers]


def _label_texts(user: User) -> list[str]:
    return [e.text for e in user.current_layout.descendants() if isinstance(e, ui.label)]


async def _until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


async def _open_editable(user: User, session_id: str) -> None:
    await user.open(f"/sessions/{session_id}")
    await user.should_see("DOES NOT", retries=100)
    await _until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))


async def test_failed_closure_is_reported_and_open_session_stays_editable(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Close Fails").session_id
    try:
        await _open_editable(user, session_id)
        monkeypatch.setattr(canvas, "close_session", _failing_close)

        user.find(marker="close-session").click()

        await user.should_see(marker="closure-failed", retries=100)
        assert "could not be closed" in caplog.text  # logged, so expected here
        caplog.clear()
        await user.should_not_see(marker="session-closed")
        assert get_session_status(session_id) == "open"

        await _until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))
        user.find(marker="display-name").type("Alice")
        user.find(marker="content-0").type("Still editable")
        user.find(marker="submit-0").click()
        await user.should_see("Still editable", retries=100)
        await _until(lambda: [c.content for c in cards.list_cards(session_id)] == ["Still editable"])
    finally:
        await _release_all(session_id)


async def test_failed_closure_keeps_a_closed_session_closed_and_read_only(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Closed Elsewhere").session_id
    try:
        await _open_editable(user, session_id)
        monkeypatch.setattr(canvas, "close_session", _failing_close)
        assert close_session(session_id) == "closed"  # committed by another participant

        user.find(marker="close-session").click()

        await user.should_see(marker="session-closed", retries=100)
        assert "could not be closed" in caplog.text  # logged, so expected here
        caplog.clear()
        await user.should_not_see(marker="closure-failed")
        assert not any(b.enabled for b in user.find(marker="submit-0").elements)
        assert get_session_status(session_id) == "closed"
    finally:
        await _release_all(session_id)


def _hold_inserts(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Make inserts wait until the returned event is set; they then reach the database."""
    real_insert, release = cards.insert_card, threading.Event()

    def held_insert(*args, **kwargs):
        assert release.wait(timeout=30), "insert was never released"
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(cards, "insert_card", held_insert)
    return release


async def _submit_pending(user: User, text: str) -> None:
    user.find(marker="display-name").type("Alice")
    user.find(marker="content-0").type(text)
    user.find(marker="submit-0").click()
    await _until(lambda: text in _label_texts(user))


def _assert_closed_with_only(user: User, session_id: str, kept) -> None:
    """The late rejection was not reported, and only `kept` is shown, read-only, and stored."""
    assert not user.notify.contains("Card could not be added")
    assert _label_texts(user).count("Kept") == 1
    assert "Pending only" not in _label_texts(user)
    _assert_read_only_and_stored(user, session_id, kept)


def _assert_read_only_and_stored(user: User, session_id: str, kept) -> None:
    assert not any(b.enabled for b in user.find(marker="submit-0").elements)
    assert [c.id for c in cards.list_cards(session_id)] == [kept.id]


async def test_closure_drops_a_pending_insert_and_ignores_its_late_failure(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Closed While Pending").session_id
    kept = cards.insert_card(session_id, _quadrant_ids(session_id)[0], 0, "Kept", "Bob")
    release = threading.Event()
    try:
        await _open_editable(user, session_id)
        await user.should_see("Kept", retries=100)
        release = _hold_inserts(monkeypatch)
        await _submit_pending(user, "Pending only")

        assert close_session(session_id) == "closed"  # committed while the insert is held

        await user.should_see(marker="session-closed", retries=100)
        await _until(lambda: "Pending only" not in _label_texts(user))
        release.set()
        await _until(lambda: "could not be added" in caplog.text)  # the late rejection
        caplog.clear()
        await asyncio.sleep(0.5)
        _assert_closed_with_only(user, session_id, kept)
    finally:
        release.set()
        await _release_all(session_id)
