import asyncio
import threading
from contextlib import asynccontextmanager

import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.sessions import close_session, create_session, get_session_status

# Shared harness of the Realtime tests.
from test_realtime import _release_all  # noqa: F401

_MUTATION_MARKS = ("edit-", "delete-", "move-", "save-", "confirm-delete-")
_NOT_SAVED = "Your pending change was not saved because the session closed"


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


class _Held:
    """Holds one card operation unresolved until released.

    By default the real operation runs after the release, so it has not committed while
    held; with `after` it runs first and only its response is held.
    """

    def __init__(self, real, after: bool = False) -> None:
        self._real = real
        self._after = after
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        result = self._real(*args, **kwargs) if self._after else None
        self.entered.set()
        assert self.release.wait(timeout=30), "barrier was never released"
        return result if self._after else self._real(*args, **kwargs)


async def _until(condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.05)


def _marks(user: User) -> list[str]:
    return [mark for element in user.current_layout.descendants() for mark in element._markers]


def _notice(user: User) -> ui.label:
    """The 'not saved' label; User.find skips hidden elements, so look it up directly."""
    return next(e for e in user.current_layout.descendants() if "change-not-saved" in e._markers)


def _texts(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.label)]


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


async def _request_insert(user: User, card: cards.Card) -> None:
    user.find(marker="display-name").type("Bob")
    user.find(marker="content-0").type("Uncommitted")
    user.find(marker="submit-0").click()


async def _request_edit(user: User, card: cards.Card) -> None:
    user.find(marker=f"edit-{card.id}").click()
    await user.should_see(marker=f"save-{card.id}")
    (editor,) = user.find(marker=f"edit-content-{card.id}").elements
    editor.value = "Uncommitted"
    user.find(marker=f"save-{card.id}").click()


async def _request_same_edit(user: User, card: cards.Card) -> None:
    user.find(marker=f"edit-{card.id}").click()
    await user.should_see(marker=f"save-{card.id}")
    user.find(marker=f"save-{card.id}").click()  # the editor holds the persisted content


async def _request_move(user: User, card: cards.Card) -> None:
    (target,) = user.find(marker=f"move-quadrant-{card.id}").elements
    (at,) = user.find(marker=f"move-index-{card.id}").elements
    target.value, at.value = 1, 3
    user.find(marker=f"move-{card.id}").click()


async def _request_delete(user: User, card: cards.Card) -> None:
    user.find(marker=f"delete-{card.id}").click()
    await user.should_see(marker=f"confirm-delete-{card.id}")
    user.find(marker=f"confirm-delete-{card.id}").click()


_OPERATIONS = {
    "insert": ("insert_card", _request_insert),
    "edit": ("update_card", _request_edit),
    "move": ("relocate_card", _request_move),
    "delete": ("soft_delete_card", _request_delete),
}


def _assert_authoritative_closed_cards(user: User, canvas: Canvas, card: cards.Card) -> None:
    """The cards shown are exactly the persisted ones: no uncommitted change appears."""
    (persisted,) = cards.list_cards(canvas._session_id)
    assert persisted == card
    first, second = (_texts(column) for column in canvas._columns[:2])
    assert (first.count("Mover"), second.count("Mover")) == (1, 0)
    _assert_no_overlay(user, canvas)


def _assert_no_overlay(user: User, canvas: Canvas) -> None:
    assert "Uncommitted" not in _texts(user.current_layout)
    assert "Pending" not in _texts(user.current_layout)
    assert not canvas._pending and not canvas._moving and not canvas._drafts
    assert not [mark for mark in _marks(user) if mark.startswith("pending-")]


def _assert_read_only(user: User, canvas: Canvas) -> None:
    assert canvas._closed and not canvas._editable
    assert not [mark for mark in _marks(user) if mark.startswith(_MUTATION_MARKS)]
    assert not any(
        e.enabled
        for e in user.current_layout.descendants()
        if isinstance(e, ui.button) and any(m.startswith("submit-") for m in e._markers)
    )


@asynccontextmanager
async def _held_operation(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    request=None,
    after: bool = False,
):
    """Open the session with `operation` requested and held unresolved.

    It is uncommitted while held, unless `after`: then only its response is held.
    `request` replaces the way the operation is asked for.

    Yields the session id, the persisted card, the canvas and the barrier; on exit the
    barrier is released and the Realtime views are released.
    """
    function, default_request = _OPERATIONS[operation]
    request = request or default_request
    session_id = create_session("Closure Pending").session_id
    q1 = _load_quadrant_ids(session_id)[0]
    card = cards.insert_card(session_id, q1, 0, "Mover", "Alice")
    held = _Held(getattr(cards, function), after)
    monkeypatch.setattr(cards, function, held)
    canvases = _record_canvases(monkeypatch)
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see(marker=f"move-{card.id}", retries=100)
        (canvas,) = canvases
        await _until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))
        await request(user, card)
        await asyncio.wait_for(asyncio.to_thread(held.entered.wait, 10), timeout=15)
        yield session_id, card, canvas, held
    finally:
        held.release.set()
        await _release_all(session_id)


async def _close_elsewhere(session_id: str, canvas: Canvas) -> None:
    """S closes while the operation is unresolved; wait for the reconciliation to end."""
    await asyncio.to_thread(close_session, session_id)
    await _until(lambda: canvas._closed)
    await _until(lambda: canvas._reconciliation is not None and canvas._reconciliation.done())


def _assert_discarded(user: User, canvas: Canvas, card: cards.Card, generation: int) -> None:
    """Closure was reconciled while the operation's response is delayed: the cards
    show the change did not commit, so it is reported without waiting for the response."""
    assert canvas._generation > generation
    assert canvas._requests  # the response is still delayed
    assert _notice(user).visible and _notice(user).text == _NOT_SAVED
    _assert_authoritative_closed_cards(user, canvas, card)
    _assert_read_only(user, canvas)


@pytest.mark.parametrize("operation", list(_OPERATIONS))
async def test_closure_discards_the_pending_operation_and_says_it_was_not_saved(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, operation: str
):
    async with _held_operation(user, monkeypatch, operation) as (session_id, card, canvas, held):
        generation = canvas._generation
        assert canvas._requests and not canvas._closed
        assert not _notice(user).visible

        await _close_elsewhere(session_id, canvas)
        _assert_discarded(user, canvas, card, generation)

        # The delayed response then arrives: the database rejected it; still reported.
        held.release.set()
        await _until(lambda: not canvas._requests)
        assert _notice(user).visible and _notice(user).text == _NOT_SAVED
        _assert_authoritative_closed_cards(user, canvas, card)
        _assert_read_only(user, canvas)

    caplog.clear()  # the rejected mutation of a closed session is logged by design
    assert get_session_status(session_id) == "closed"


@pytest.mark.parametrize("operation", list(_OPERATIONS))
async def test_not_saved_survives_a_failed_first_reload(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, operation: str
):
    """The pending evidence outlives the operation's response until a reload succeeds."""
    async with _held_operation(user, monkeypatch, operation) as (session_id, card, canvas, held):
        real_load, calls = canvas._load, []

        async def flaky_load():
            calls.append(None)
            if len(calls) == 1:
                raise RuntimeError("reload failed")
            return await real_load()

        monkeypatch.setattr(canvas, "_load", flaky_load)
        await _close_elsewhere(session_id, canvas)

        # The rejected response arrives before the reload is retried.
        held.release.set()
        await _until(lambda: not canvas._requests)
        assert not _notice(user).visible

        await canvas._retry_reconcile()
        await _until(lambda: canvas._reconciliation.done())
        assert len(calls) == 2
        assert _notice(user).visible and _notice(user).text == _NOT_SAVED
        _assert_authoritative_closed_cards(user, canvas, card)
        _assert_read_only(user, canvas)

    caplog.clear()
    assert get_session_status(session_id) == "closed"


async def test_rejected_edit_to_the_persisted_value_is_still_reported(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """The database's rejection decides, not whether the fields happen to be equal."""
    async with _held_operation(user, monkeypatch, "edit", _request_same_edit) as (
        session_id,
        card,
        canvas,
        held,
    ):
        await _close_elsewhere(session_id, canvas)
        assert _notice(user).visible  # the card is still the version the edit started from

        held.release.set()
        await _until(lambda: not canvas._requests)
        assert _notice(user).visible and _notice(user).text == _NOT_SAVED
        (persisted,) = cards.list_cards(session_id)
        assert persisted == card
        _assert_read_only(user, canvas)

    caplog.clear()


async def _release_unreported(user: User, canvas: Canvas, held: _Held) -> None:
    """Let the delayed response arrive and settle: the change is still not reported."""
    held.release.set()
    await _until(lambda: not canvas._requests)
    await asyncio.sleep(0.5)
    assert not _notice(user).visible


@pytest.mark.parametrize("operation", ["insert", "move", "delete"])
async def test_operation_committed_before_closure_is_not_reported(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, operation: str
):
    """A change that committed before closure is not 'not saved' while its response lags."""
    async with _held_operation(user, monkeypatch, operation, after=True) as (
        session_id,
        card,
        canvas,
        held,
    ):
        await _close_elsewhere(session_id, canvas)
        assert canvas._requests  # the success response is still delayed
        assert not _notice(user).visible
        committed = {"insert": 2, "move": 1, "delete": 0}[operation]
        assert len(cards.list_cards(session_id)) == committed  # it did commit

        await _release_unreported(user, canvas, held)
        _assert_read_only(user, canvas)

    caplog.clear()


async def test_committed_edit_overwritten_before_closure_is_not_reported(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """An edit that committed is not 'not saved' because someone else changed the card."""
    real_update = cards.update_card
    async with _held_operation(user, monkeypatch, "edit", after=True) as (
        session_id,
        card,
        canvas,
        held,
    ):
        # The edit committed; only its response is delayed. Another participant overwrites it.
        overwritten = real_update(session_id, card.id, "Overwritten")
        await _close_elsewhere(session_id, canvas)

        await _release_unreported(user, canvas, held)
        assert cards.list_cards(session_id) == [overwritten]
        assert "Overwritten" in _texts(user.current_layout)
        _assert_read_only(user, canvas)

    caplog.clear()


def _delete_inserted(session_id: str, persisted: cards.Card) -> None:
    """Soft-delete the card the held insert committed, other than `persisted`."""
    (inserted,) = [c for c in cards.list_cards(session_id) if c.id != persisted.id]
    cards.soft_delete_card(session_id, inserted.id)


async def test_insert_committed_then_deleted_before_closure_is_not_reported(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """An insert that committed and was then deleted is absent from the cards, not unsaved."""
    async with _held_operation(user, monkeypatch, "insert", after=True) as (
        session_id,
        card,
        canvas,
        held,
    ):
        _delete_inserted(session_id, card)
        await _close_elsewhere(session_id, canvas)
        assert canvas._requests  # the success response is still delayed
        assert not _notice(user).visible

        await _release_unreported(user, canvas, held)
        assert cards.list_cards(session_id) == [card]
        _assert_read_only(user, canvas)

    caplog.clear()
