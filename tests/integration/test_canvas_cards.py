import asyncio
import os
import threading
import uuid

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards, realtime
from quadra.canvas import Canvas
from quadra.sessions import QUADRANTS, create_session

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI.

    A page defined here, delegating to the production one, so that quadra.app stays
    imported for the conftest's restore (the simulation drops every page's module).
    """

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _quadrant_id(session_id: str, position: int = 0) -> str:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        (row,) = connection.execute(
            "select q.id from public.quadrants q join public.canvases c on c.id = q.canvas_id "
            "where c.workspace_id = %s and q.position = %s",
            (session_id, position),
        ).fetchall()
    return str(row[0])


def _add_card(session_id: str, index: int, content: str, name: str = "Alice", position: int = 0) -> str:
    quadrant_id = _quadrant_id(session_id, position)
    return cards.insert_card(session_id, quadrant_id, index, content, name).id


def _texts(user: User) -> list[str]:
    """Every label of the page, in document order."""
    return [
        element.text
        for element in user.current_layout.descendants()
        if isinstance(element, ui.label)
    ]


def _cards_of_quadrant(texts: list[str], quadrant: str) -> list[str]:
    """Texts between a quadrant's title and the next quadrant title."""
    start = texts.index(quadrant)
    end = min((texts.index(name) for name in QUADRANTS if texts.index(name) > start), default=len(texts))
    return texts[start + 1 : end]


async def _wait_until_ready(session_id: str) -> None:
    """Wait for the view's subscription, then for its (empty) snapshot to be shown."""
    async with asyncio.timeout(10):
        while not realtime.is_ready(session_id):
            await asyncio.sleep(0.01)
    await asyncio.sleep(0.5)


async def _open(user: User, session_id: str) -> None:
    await user.open(f"/sessions/{session_id}")


async def test_cards_render_in_index_order_without_deleted_or_foreign_cards(user: User):
    session_id = create_session("Ordered Cards").session_id
    other_id = create_session("Other Cards").session_id
    _add_card(session_id, 2, "card-A")
    _add_card(session_id, 1, "card-B")
    deleted = _add_card(session_id, 0, "card-C")
    cards.soft_delete_card(session_id, deleted)
    _add_card(other_id, 0, "card-D")
    try:
        await _open(user, session_id)
        await user.should_see("card-B", retries=100)
        first = _texts(user)
        await _open(user, session_id)  # reload
        await user.should_see("card-B", retries=100)
        second = _texts(user)
    finally:
        await _release_all(session_id)

    assert first == second
    assert _cards_of_quadrant(first, "IS") == ["card-B", "Alice", "card-A", "Alice"]
    assert "card-C" not in first and "card-D" not in first
    assert [text for text in first if text in QUADRANTS] == list(QUADRANTS)


async def test_equal_indexes_are_ordered_by_card_id(user: User):
    session_id = create_session("Tied Cards").session_id
    ids = {_add_card(session_id, 0, f"tie-{n}"): f"tie-{n}" for n in range(3)}
    try:
        await _open(user, session_id)
        await user.should_see("tie-0", retries=100)
        texts = _cards_of_quadrant(_texts(user), "IS")
    finally:
        await _release_all(session_id)

    assert texts[::2] == [ids[card_id] for card_id in sorted(ids)]


async def test_session_without_cards_renders_its_quadrants(user: User):
    session_id = create_session("Empty Canvas").session_id
    try:
        await _open(user, session_id)
        await user.should_see("Empty Canvas", retries=100)
        await user.should_see("DOES NOT", retries=100)
        await _wait_until_ready(session_id)
        texts = _texts(user)
    finally:
        await _release_all(session_id)

    assert [text for text in texts if text in QUADRANTS] == list(QUADRANTS)
    assert "Cards could not be loaded" not in texts


async def test_unknown_session_still_shows_not_found(user: User):
    await _open(user, str(uuid.uuid4()))

    await user.should_see("Session not found", retries=100)
    assert not [text for text in _texts(user) if text in QUADRANTS]


async def test_notification_between_subscription_and_snapshot_appears_once(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Racing Card").session_id
    received = threading.Event()
    original_on_change = Canvas._on_change
    original_list_cards = cards.list_cards

    def spy(self, change):
        original_on_change(self, change)
        received.set()

    def list_cards_after_a_commit(*args, **kwargs):
        # Runs after the view is registered and ready, before the snapshot is read.
        _add_card(session_id, 0, "raced-card")
        assert received.wait(10), "the commit was not pushed"
        return original_list_cards(*args, **kwargs)

    monkeypatch.setattr(Canvas, "_on_change", spy)
    monkeypatch.setattr(cards, "list_cards", list_cards_after_a_commit)
    try:
        await _open(user, session_id)
        await user.should_see("raced-card", retries=100)
        await asyncio.sleep(0.3)
        texts = _texts(user)
    finally:
        await _release_all(session_id)

    assert texts.count("raced-card") == 1


async def test_card_committed_during_loading_only_in_notification_is_kept(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    """The snapshot predates the commit, so only the buffered notification has the card."""
    session_id = create_session("Late Card").session_id
    received = threading.Event()
    original_on_change = Canvas._on_change
    original_list_cards = cards.list_cards

    def spy(self, change):
        original_on_change(self, change)
        received.set()

    def list_cards_before_a_commit(*args, **kwargs):
        snapshot = original_list_cards(*args, **kwargs)
        _add_card(session_id, 0, "late-card")
        assert received.wait(10), "the commit was not pushed"
        return snapshot

    monkeypatch.setattr(Canvas, "_on_change", spy)
    monkeypatch.setattr(cards, "list_cards", list_cards_before_a_commit)
    try:
        await _open(user, session_id)
        await user.should_see("late-card", retries=100)
        texts = _texts(user)
    finally:
        await _release_all(session_id)

    assert texts.count("late-card") == 1


async def test_later_notifications_add_edit_relocate_and_remove_cards(user: User):
    session_id = create_session("Live Cards").session_id
    try:
        await _open(user, session_id)
        await user.should_see("DOES NOT", retries=100)
        await _wait_until_ready(session_id)

        card_id = await asyncio.to_thread(_add_card, session_id, 0, "first")
        await user.should_see("first", retries=100)
        await asyncio.to_thread(cards.update_card, session_id, card_id, "second")
        await user.should_see("second", retries=100)
        await user.should_not_see("first", retries=100)
        await asyncio.to_thread(
            cards.relocate_card, session_id, card_id, _quadrant_id(session_id, 1), 0
        )
        await asyncio.sleep(1)
        texts = _texts(user)
        assert _cards_of_quadrant(texts, "IS") == []
        assert _cards_of_quadrant(texts, "IS NOT") == ["second", "Alice"]
        await asyncio.to_thread(cards.soft_delete_card, session_id, card_id)
        await user.should_not_see("second", retries=100)
        assert sum(text == "second" for text in _texts(user)) == 0
    finally:
        await _release_all(session_id)


async def test_content_and_names_render_as_escaped_plain_text(user: User):
    session_id = create_session("Escaped Cards").session_id
    content = "<b>bold</b><script>alert(1)</script>"
    name = "<i>Mallory</i>"
    _add_card(session_id, 0, content, name)
    try:
        await _open(user, session_id)
        await user.should_see(content, retries=100)
        texts = _texts(user)
        markup = [
            element
            for element in user.current_layout.descendants()
            if isinstance(element, ui.html | ui.markdown)
        ]
    finally:
        await _release_all(session_id)

    assert _cards_of_quadrant(texts, "IS") == [content, name]
    assert not markup


async def test_card_loading_failure_keeps_the_canvas(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Failing Load").session_id

    def failing_list_cards(*args, **kwargs):
        raise cards.CardPersistenceError("boom")

    monkeypatch.setattr(cards, "list_cards", failing_list_cards)
    try:
        await _open(user, session_id)
        await user.should_see("Cards could not be loaded", retries=100)
        texts = _texts(user)
    finally:
        await _release_all(session_id)

    assert [text for text in texts if text in QUADRANTS] == list(QUADRANTS)
    assert "could not be loaded" in caplog.text  # logged, so expected here
    caplog.clear()
