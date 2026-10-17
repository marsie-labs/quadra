import asyncio
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.sessions import create_session

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401

_ROWS = (
    "select content, creator_display_name, deleted_at, created_at, updated_at, "
    "q.name, c.session_id from public.cards c join public.quadrants q on q.id = c.quadrant_id "
    "where c.session_id = %s"
)


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _persisted(session_id: str) -> list[tuple]:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        return connection.execute(_ROWS, (session_id,)).fetchall()


def _texts(user: User) -> list[str]:
    """Text of every label and badge of the page, in document order."""
    return [
        element.text
        for element in user.current_layout.descendants()
        if isinstance(element, ui.label | ui.badge)
    ]


async def _open_ready(user: User, session_id: str) -> None:
    """Open the page and wait until the composers accept submissions."""
    await user.open(f"/sessions/{session_id}")
    await user.should_see("DOES NOT", retries=100)
    submit = user.find(marker="submit-0")
    async with asyncio.timeout(10):
        while not all(button.enabled for button in submit.elements):
            await asyncio.sleep(0.05)


async def _wait_for_cards(user: User, count: int) -> None:
    """Wait until `count` confirmed cards (each with its timestamp badge) are shown."""
    async with asyncio.timeout(10):
        while sum(isinstance(e, ui.badge) for e in user.current_layout.descendants()) < count:
            await asyncio.sleep(0.05)


async def _wait_until_cleared(composer) -> None:
    """Wait for the composer to empty; the push may beat the insert's own confirmation."""
    async with asyncio.timeout(10):
        while composer.value:
            await asyncio.sleep(0.05)


async def test_submitted_card_is_persisted_and_shown_with_author_and_timestamp(user: User):
    session_id = create_session("Insert Card").session_id
    try:
        await _open_ready(user, session_id)
        user.find(marker="display-name").type("Alice")
        user.find(marker="content-0").type("Compare options")
        user.find(marker="submit-0").click()
        await _wait_for_cards(user, 1)
        texts = _texts(user)
        composer = user.find(marker="content-0").elements.pop()
        await _wait_until_cleared(composer)
    finally:
        await _release_all(session_id)

    created_at = _assert_persisted_alice_card(session_id)
    start = texts.index("IS")
    assert texts[start + 1 : start + 3] == ["Compare options", "Alice"]
    assert texts.count("Compare options") == 1
    shown = texts[start + 3]
    assert datetime.strptime(shown, "%Y-%m-%d %H:%M:%S %Z").date() == created_at.date()
    assert composer.value == ""


def _assert_persisted_alice_card(session_id: str) -> datetime:
    """Check the one stored card and its server timestamps; return its creation time."""
    ((content, creator, deleted_at, created_at, updated_at, quadrant, owner),) = _persisted(
        session_id
    )
    assert (content, creator, deleted_at, quadrant, str(owner)) == (
        "Compare options",
        "Alice",
        None,
        "IS",
        session_id,
    )
    now = datetime.now(UTC)
    assert now - timedelta(minutes=5) < created_at <= now + timedelta(minutes=5)
    assert updated_at >= created_at
    return created_at


async def test_card_goes_to_the_quadrant_of_its_composer_after_existing_cards(user: User):
    session_id = create_session("Insert Order").session_id
    try:
        await _open_ready(user, session_id)
        user.find(marker="display-name").type("Alice")
        for count, text in enumerate(("first", "second"), start=1):
            user.find(marker="content-2").type(text)
            user.find(marker="submit-2").click()
            await _wait_for_cards(user, count)
    finally:
        await _release_all(session_id)

    rows = sorted(cards.list_cards(session_id), key=lambda card: card.index)
    assert [(card.content, card.index) for card in rows] == [("first", 0), ("second", 1)]
    assert {name for *_, name, _ in _persisted(session_id)} == {"DOES"}


@pytest.mark.parametrize(
    ("name", "content"), [("", "Compare options"), ("   ", "Compare options"), ("Alice", ""), ("Alice", "  ")]
)
async def test_empty_display_name_or_content_is_not_submitted(
    user: User, monkeypatch: pytest.MonkeyPatch, name: str, content: str
):
    session_id = create_session("Empty Insert").session_id

    def forbidden(*args, **kwargs):
        raise AssertionError("insert_card must not be called")

    monkeypatch.setattr(cards, "insert_card", forbidden)
    try:
        await _open_ready(user, session_id)
        if name:
            user.find(marker="display-name").type(name)
        if content:
            user.find(marker="content-0").type(content)
        user.find(marker="submit-0").click()
        await user.should_see("Enter a display name and some text", retries=100)
    finally:
        await _release_all(session_id)

    assert not _persisted(session_id)
    assert not [text for text in _texts(user) if text in (name, content) and text.strip()]


async def test_content_and_name_render_as_escaped_plain_text(user: User):
    session_id = create_session("Escaped Insert").session_id
    content, name = "<b>bold</b>", "<i>Mallory</i>"
    try:
        await _open_ready(user, session_id)
        user.find(marker="display-name").type(name)
        user.find(marker="content-0").type(content)
        user.find(marker="submit-0").click()
        await _wait_for_cards(user, 1)
        texts = _texts(user)
        markup = [e for e in user.current_layout.descendants() if isinstance(e, ui.html | ui.markdown)]
    finally:
        await _release_all(session_id)

    assert texts[texts.index("IS") + 1 : texts.index("IS") + 3] == [content, name]
    assert not markup


async def test_failed_insert_keeps_the_text_and_shows_no_card(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Failing Insert").session_id

    def failing(*args, **kwargs):
        raise cards.CardPersistenceError("boom")

    monkeypatch.setattr(cards, "insert_card", failing)
    try:
        await _open_ready(user, session_id)
        user.find(marker="display-name").type("Alice")
        user.find(marker="content-0").type("Lost")
        user.find(marker="submit-0").click()
        await user.should_see("Card could not be added", retries=100)
        composer = user.find(marker="content-0").elements.pop()
    finally:
        await _release_all(session_id)

    assert composer.value == "Lost"
    assert not _persisted(session_id)
    assert "Alice" not in _texts(user)
    caplog.clear()

