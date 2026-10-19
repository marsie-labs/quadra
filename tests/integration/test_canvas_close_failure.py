import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import canvas, cards
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
