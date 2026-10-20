import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas
from quadra.sessions import QUADRANTS, close_session, create_session

# Shared harness of the canvas tests.
from test_canvas_cards import _add_card, _cards_of_quadrant, _open, _texts
from test_realtime import _release_all  # noqa: F401

_ACTIVE_PREFIXES = ("is-", "isnot-", "does-", "doesnot-")
_MUTATION_MARKS = ("edit-", "delete-", "move-", "save-", "confirm-delete-")


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _marked(user: User) -> list[str]:
    """Every marker of the page."""
    return [
        mark
        for element in user.current_layout.descendants()
        for mark in element._markers
    ]


def _submit_buttons(user: User) -> list[ui.button]:
    return [
        element
        for element in user.current_layout.descendants()
        if isinstance(element, ui.button) and any(m.startswith("submit-") for m in element._markers)
    ]


# (quadrant position, index, content): more active cards than one page, all quadrants.
_LAYOUT = [
    (0, 1, "is-1"),
    (0, 0, "is-0"),
    (1, 0, "isnot-0"),
    (2, 0, "does-0"),
    (2, 1, "does-1"),
    (3, 0, "doesnot-0"),
]


def _closed_session_with_deleted_cards() -> str:
    session_id = create_session("Closed Load").session_id
    for position, index, content in _LAYOUT:
        _add_card(session_id, index, content, position=position)
    for content in ("gone-1", "gone-2"):
        deleted = _add_card(session_id, 5, content, position=0)
        cards.soft_delete_card(session_id, deleted)
    assert close_session(session_id) == "closed"
    return session_id


def _assert_cards_rendered_once_in_order(texts: list[str]) -> None:
    assert _cards_of_quadrant(texts, "IS") == ["is-0", "Alice", "is-1", "Alice"]
    assert _cards_of_quadrant(texts, "IS NOT") == ["isnot-0", "Alice"]
    assert _cards_of_quadrant(texts, "DOES") == ["does-0", "Alice", "does-1", "Alice"]
    assert _cards_of_quadrant(texts, "DOES NOT") == ["doesnot-0", "Alice"]


def _assert_quadrants_once(texts: list[str]) -> None:
    assert [text for text in texts if text in QUADRANTS] == list(QUADRANTS)


def _assert_no_deleted_and_exact_count(texts: list[str]) -> None:
    assert not [text for text in texts if text.startswith("gone-")]
    rendered = [text for text in texts if text.startswith(_ACTIVE_PREFIXES)]
    assert len(rendered) == len(_LAYOUT)


async def test_closed_session_loads_every_active_card_once_and_read_only(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(cards, "_PAGE_SIZE", 2)
    session_id = _closed_session_with_deleted_cards()
    try:
        await _open(user, session_id)
        await user.should_see("doesnot-0", retries=100)
        await user.should_see(marker="session-closed")
        texts = _texts(user)
        marks = _marked(user)
        buttons = _submit_buttons(user)
        await user.should_not_see(marker="close-session")
    finally:
        await _release_all(session_id)

    _assert_cards_rendered_once_in_order(texts)
    _assert_quadrants_once(texts)
    _assert_no_deleted_and_exact_count(texts)
    assert not [mark for mark in marks if mark.startswith(_MUTATION_MARKS)]
    assert len(buttons) == 4 and not any(button.enabled for button in buttons)


async def test_canvas_loaded_closed_never_becomes_editable_again(
    user: User, monkeypatch: pytest.MonkeyPatch
):
    canvases: list[Canvas] = []
    original_init = Canvas.__init__

    def record(self, session):
        original_init(self, session)
        canvases.append(self)

    monkeypatch.setattr(Canvas, "__init__", record)
    session_id = create_session("Latched").session_id
    _add_card(session_id, 0, "latched-card")
    close_session(session_id)
    try:
        await _open(user, session_id)
        await user.should_see("latched-card", retries=100)
        (canvas,) = canvases

        async def open_status():
            return "open"

        monkeypatch.setattr(canvas, "_read_status", open_status)
        await canvas._recover_from_failed_close()
        buttons = _submit_buttons(user)
    finally:
        await _release_all(session_id)

    assert not canvas._editable
    assert not any(button.enabled for button in buttons)


async def test_open_session_still_offers_every_control(user: User):
    session_id = create_session("Still Open").session_id
    card_id = _add_card(session_id, 0, "open-card")
    try:
        await _open(user, session_id)
        await user.should_see("open-card", retries=100)
        await user.should_see(marker=f"edit-{card_id}", retries=100)
        await user.should_see(marker="close-session")
        await user.should_not_see(marker="session-closed")
        buttons = _submit_buttons(user)
    finally:
        await _release_all(session_id)

    assert len(buttons) == 4 and all(button.enabled for button in buttons)
