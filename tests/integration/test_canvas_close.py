import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import realtime
from quadra.sessions import create_session, get_session_status

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


async def _wait_until_registered(session_id: str) -> None:
    """Wait for the view's subscription, so releasing it later never contends for the lock."""
    async with asyncio.timeout(15):
        while not realtime.is_ready(session_id):
            await asyncio.sleep(0.05)


async def test_close_control_stores_closed_and_shows_it_without_credentials(user: User):
    session_id = create_session("Close Me").session_id
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see(marker="close-session")
        await _wait_until_registered(session_id)
        await user.should_not_see(marker="session-closed")
        assert get_session_status(session_id) == "open"

        user.find(marker="close-session").click()

        await user.should_see(marker="session-closed", retries=100)
        assert get_session_status(session_id) == "closed"
    finally:
        await _release_all(session_id)


async def test_empty_session_renders_and_unknown_session_is_not_found(user: User):
    session_id = create_session("Still Renders").session_id
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see("Still Renders")
        await user.should_see(marker="close-session")
        await _wait_until_registered(session_id)
        await user.open("/sessions/00000000-0000-4000-8000-000000000000")
        await user.should_see("Session not found")
        await user.should_not_see(marker="close-session")
    finally:
        await _release_all(session_id)
