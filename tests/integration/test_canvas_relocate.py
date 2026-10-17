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

_ROW = "select quadrant_id, index, creator_display_name, created_at from public.cards where id = %s"
_CARDS = {"Mover", "Before", "Tie", "After"}


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


def _shown_cards(user: User) -> list[str]:
    """The card contents of the page in document order: quadrant by quadrant."""
    return [text for text in _texts(user) if text in _CARDS]


async def _wait_until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


def _populate(session_id: str) -> tuple[cards.Card, dict[str, cards.Card], list[str]]:
    """Alice's card C in Q1 at index 0, and three cards already in Q2 around index 3."""
    q1, q2 = _load_quadrant_ids(session_id)[:2]
    card = cards.insert_card(session_id, q1, 0, "Mover", "Alice")
    others = {
        content: cards.insert_card(session_id, q2, index, content, "Carol")
        for content, index in (("Before", 2), ("Tie", 3), ("After", 5))
    }
    return card, others, [q1, q2]


async def _open_with_cards(user: User, session_id: str) -> tuple[cards.Card, dict, list[str]]:
    card, others, quadrant_ids = _populate(session_id)
    await user.open(f"/sessions/{session_id}")
    await user.should_see(marker=f"move-{card.id}", retries=100)
    return card, others, quadrant_ids


def _choose(user: User, card_id: str, position: int | None, index: int) -> None:
    (quadrant,) = user.find(marker=f"move-quadrant-{card_id}").elements
    (target,) = user.find(marker=f"move-index-{card_id}").elements
    quadrant.value = position
    target.value = index


def _expected_q2(card: cards.Card, others: dict[str, cards.Card]) -> list[str]:
    placed = {"Mover": (3, card.id)} | {name: (c.index, c.id) for name, c in others.items()}
    return sorted(placed, key=placed.__getitem__)


async def _move_and_reload(user: User, other: User, session_id: str) -> dict:
    """Move C to Q2 at index 3, then load the session afresh; what was observed."""
    card, others, (_, q2) = await _open_with_cards(user, session_id)
    _choose(user, card.id, 1, 3)
    user.find(marker=f"move-{card.id}").click()
    expected = _expected_q2(card, others)
    await _wait_until(lambda: _shown_cards(user) == expected)
    shown = _texts(user)

    await other.open(f"/sessions/{session_id}")  # a fresh page load
    await other.should_see(marker=f"move-{card.id}", retries=100)
    return {
        "card": card,
        "others": others,
        "q2": q2,
        "expected": expected,
        "shown": shown,
        "fresh": _shown_cards(other),
        "active": [active.id for active in cards.list_cards(session_id)],
    }


def _assert_creation_shown(shown: list[str], created_at: datetime) -> None:
    """The creator and creation time follow the moved card's content on the page."""
    start = shown.index("Mover")
    assert shown[start + 1] == "Alice"
    assert datetime.strptime(shown[start + 2], "%Y-%m-%d %H:%M:%S %Z").date() == created_at.date()


async def test_moved_card_is_persisted_and_shown_without_replacing_authorship(
    user: User, create_user
):
    other = create_user()
    session_id = create_session("Relocate Card").session_id
    try:
        seen = await _move_and_reload(user, other, session_id)
    finally:
        await _release_all(session_id)

    card, active = seen["card"], seen["active"]
    quadrant_id, index, creator, created_at = _persisted(card.id)
    assert (str(quadrant_id), index, creator) == (seen["q2"], 3, "Alice")
    assert created_at == datetime.fromisoformat(card.created_at)
    assert seen["fresh"] == seen["expected"]
    assert active.index(card.id) > active.index(seen["others"]["Before"].id)
    _assert_creation_shown(seen["shown"], created_at)


async def test_move_reaches_other_connected_canvases(user: User, create_user):
    other = create_user()
    session_id = create_session("Relocate Push").session_id
    try:
        card, others, _ = await _open_with_cards(user, session_id)
        await other.open(f"/sessions/{session_id}")
        await other.should_see(marker=f"move-{card.id}", retries=100)
        _choose(user, card.id, 1, 3)
        user.find(marker=f"move-{card.id}").click()
        expected = _expected_q2(card, others)
        await _wait_until(lambda: _shown_cards(other) == expected)
    finally:
        await _release_all(session_id)


async def test_failed_move_keeps_the_card_where_it_was(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    session_id = create_session("Failing Relocate").session_id

    def failing(*args, **kwargs):
        raise cards.CardPersistenceError("boom")

    monkeypatch.setattr(cards, "relocate_card", failing)
    try:
        card, _, (q1, _) = await _open_with_cards(user, session_id)
        _choose(user, card.id, 1, 3)
        user.find(marker=f"move-{card.id}").click()
        await user.should_see("Card could not be moved", retries=100)
        shown = _shown_cards(user)
    finally:
        await _release_all(session_id)

    assert shown[0] == "Mover"
    assert (str(_persisted(card.id)[0]), _persisted(card.id)[1]) == (q1, 0)
    caplog.clear()


async def test_move_without_a_chosen_quadrant_is_not_submitted(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Unchosen Relocate").session_id

    def forbidden(*args, **kwargs):
        raise AssertionError("relocate_card must not be called")

    monkeypatch.setattr(cards, "relocate_card", forbidden)
    try:
        card, _, (q1, _) = await _open_with_cards(user, session_id)
        _choose(user, card.id, None, 3)
        user.find(marker=f"move-{card.id}").click()
        await user.should_see("Choose a quadrant and an index", retries=100)
    finally:
        await _release_all(session_id)

    assert str(_persisted(card.id)[0]) == q1
