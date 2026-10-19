import asyncio
import os
from datetime import datetime

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import _load_quadrant_ids
from quadra.sessions import create_session

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401

_ROW = "select content, creator_display_name, created_at, updated_at from public.cards where id = %s"


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _persisted(card_id: str) -> tuple:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        return connection.execute(_ROW, (card_id,)).fetchone()


def _texts(user: User) -> list[str]:
    """Text of every label and badge of the page, in document order."""
    return [
        element.text
        for element in user.current_layout.descendants()
        if isinstance(element, ui.label | ui.badge)
    ]


def _markers(user: User) -> set[str]:
    return {marker for element in user.current_layout.descendants() for marker in element._markers}


async def _wait_until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


async def _open_with_card(user: User, session_id: str) -> cards.Card:
    """Alice's card 'Draft' in the first quadrant, shown on the page opened by the user."""
    quadrant_id = _load_quadrant_ids(session_id)[0]
    card = cards.insert_card(session_id, quadrant_id, 0, "Draft", "Alice")
    await user.open(f"/sessions/{session_id}")
    await user.should_see(marker=f"edit-{card.id}", retries=100)
    return card


def _type_into_editor(user: User, card_id: str, text: str) -> None:
    (editor,) = user.find(marker=f"edit-content-{card_id}").elements
    editor.value = text


async def _bob_revises(user: User, card: cards.Card) -> list[str]:
    """Bob opens the editor of Alice's card, submits 'Revised'; the page texts afterwards."""
    user.find(marker="display-name").type("Bob")
    user.find(marker=f"edit-{card.id}").click()
    await user.should_see(marker=f"save-{card.id}")
    assert user.find(marker=f"edit-content-{card.id}").elements.pop().value == "Draft"
    _type_into_editor(user, card.id, "Revised")
    user.find(marker=f"save-{card.id}").click()
    await _wait_until(lambda: "Revised" in _texts(user))
    await _wait_until(lambda: f"save-{card.id}" not in _markers(user))
    return _texts(user)


def _assert_persisted_authorship_kept(card: cards.Card) -> None:
    content, creator, created_at, updated_at = _persisted(card.id)
    assert (content, creator) == ("Revised", "Alice")
    assert created_at == datetime.fromisoformat(card.created_at)
    assert updated_at > datetime.fromisoformat(card.updated_at)


def _assert_shown_authorship_kept(card: cards.Card, texts: list[str]) -> None:
    created_at = _persisted(card.id)[2]
    start = texts.index("Revised")
    assert texts[start + 1] == "Alice"
    shown = datetime.strptime(texts[start + 2], "%Y-%m-%d %H:%M:%S %Z")
    assert shown.date() == created_at.date()
    assert "Draft" not in texts
    assert "Bob" not in texts


async def test_edited_content_is_persisted_and_shown_without_replacing_authorship(user: User):
    session_id = create_session("Edit Card").session_id
    try:
        card = await _open_with_card(user, session_id)
        texts = await _bob_revises(user, card)
    finally:
        await _release_all(session_id)

    _assert_persisted_authorship_kept(card)
    _assert_shown_authorship_kept(card, texts)


async def test_failed_update_keeps_the_editor_and_the_persisted_card(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Failing Edit").session_id

    def failing(*args, **kwargs):
        raise cards.CardPersistenceError("boom")

    monkeypatch.setattr(cards, "update_card", failing)
    try:
        card = await _open_with_card(user, session_id)
        user.find(marker=f"edit-{card.id}").click()
        await user.should_see(marker=f"save-{card.id}")
        _type_into_editor(user, card.id, "Lost")
        user.find(marker=f"save-{card.id}").click()
        await user.should_see("Card could not be updated", retries=100)
        editor = user.find(marker=f"edit-content-{card.id}").elements.pop()
    finally:
        await _release_all(session_id)

    assert editor.value == "Lost"
    assert _persisted(card.id)[0] == "Draft"
    caplog.clear()


async def test_blank_content_is_not_submitted(user: User, monkeypatch: pytest.MonkeyPatch):
    session_id = create_session("Blank Edit").session_id

    def forbidden(*args, **kwargs):
        raise AssertionError("update_card must not be called")

    monkeypatch.setattr(cards, "update_card", forbidden)
    try:
        card = await _open_with_card(user, session_id)
        user.find(marker=f"edit-{card.id}").click()
        await user.should_see(marker=f"save-{card.id}")
        _type_into_editor(user, card.id, "   ")
        user.find(marker=f"save-{card.id}").click()
        await user.should_see("Enter some text", retries=100)
    finally:
        await _release_all(session_id)

    assert _persisted(card.id)[0] == "Draft"


async def test_cancel_closes_the_editor_without_changing_the_card(user: User):
    session_id = create_session("Cancel Edit").session_id
    try:
        card = await _open_with_card(user, session_id)
        user.find(marker=f"edit-{card.id}").click()
        await user.should_see(marker=f"cancel-{card.id}")
        _type_into_editor(user, card.id, "Abandoned")
        user.find(marker=f"cancel-{card.id}").click()
        await user.should_see(marker=f"edit-{card.id}")
        texts = _texts(user)
    finally:
        await _release_all(session_id)

    assert "Draft" in texts and "Abandoned" not in texts
    assert _persisted(card.id)[0] == "Draft"
