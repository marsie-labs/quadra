import asyncio
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.sessions import create_session

# Shared harness of the Realtime tests.
from test_realtime import LATENCY_LIMIT, _release_all  # noqa: F401
from test_realtime_cards import _forbid_polling


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _labels(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.label)]


def _shown(canvas: Canvas) -> list[list[str]]:
    """The labels of each quadrant's column of the canvas, in quadrant order."""
    return [_labels(column) for column in canvas._columns]


def _pending_marked(user: User) -> list:
    return [
        e
        for e in user.current_layout.descendants()
        if any(marker.startswith("pending-") for marker in e._markers)
    ]


async def _until(condition: Callable[[], bool], timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


async def _observe(condition: Callable[[], bool]) -> float:
    """Clock reading when `condition` first holds; in memory only, never a read."""
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.001)
    return time.perf_counter()


class _Timed:
    """Wraps one card-service call: lets only it through the polling guard and times it.

    `started` is read just before the request is sent, so the interval to B's render
    includes the commit itself. After the real call returns, `hold` (when given) keeps the
    initiating client waiting, so a push can be seen before the call resolves.
    """

    def __init__(self, real, gate: dict[str, bool], hold: threading.Event | None = None) -> None:
        self._real = real
        self._gate = gate
        self._hold = hold
        self.started: float | None = None
        self.returned = threading.Event()

    def __call__(self, *args, **kwargs):
        self._gate["open"] = True
        self.started = time.perf_counter()
        try:
            result = self._real(*args, **kwargs)
        finally:
            self._gate["open"] = False
        self.returned.set()
        if self._hold is not None:
            assert self._hold.wait(timeout=30), "the call was never released"
        return result


@dataclass(frozen=True)
class _Operation:
    """One card operation: what A does through the page, and what B must come to render."""

    service_call: str
    existing: bool
    act: Callable[[User, cards.Card | None], None]
    rendered: Callable[[list[list[str]]], bool]
    settled: Callable[[list[list[str]]], bool]


def _insert(user: User, card: cards.Card | None) -> None:
    user.find(marker="display-name").type("Alice")
    user.find(marker="content-0").type("Inserted")
    user.find(marker="submit-0").click()


def _edit(user: User, card: cards.Card | None) -> None:
    user.find(marker=f"edit-{card.id}").click()
    (editor,) = user.find(marker=f"edit-content-{card.id}").elements
    editor.value = "Revised"
    user.find(marker=f"save-{card.id}").click()


async def _confirm_delete(user: User, card: cards.Card) -> None:
    user.find(marker=f"delete-{card.id}").click()
    await user.should_see(marker=f"confirm-delete-{card.id}")
    user.find(marker=f"confirm-delete-{card.id}").click()


def _relocate(user: User, card: cards.Card | None) -> None:
    (target,) = user.find(marker=f"move-quadrant-{card.id}").elements
    (at,) = user.find(marker=f"move-index-{card.id}").elements
    target.value, at.value = 1, 3
    user.find(marker=f"move-{card.id}").click()


def _once(text: str, quadrant: int) -> Callable[[list[list[str]]], bool]:
    """The text is shown exactly once, in `quadrant`, and nowhere else."""
    return lambda shown: [column.count(text) for column in shown] == [
        int(index == quadrant) for index in range(len(shown))
    ]


OPERATIONS = {
    "insert": _Operation("insert_card", False, _insert, _once("Inserted", 0), _once("Inserted", 0)),
    "edit": _Operation(
        "update_card",
        True,
        _edit,
        lambda shown: "Revised" in shown[0],
        lambda shown: _once("Revised", 0)(shown) and not any("Draft" in c for c in shown),
    ),
    "soft-delete": _Operation(
        "soft_delete_card",
        True,
        None,  # confirmed through a dialog, see _perform
        lambda shown: not any("Obsolete" in column for column in shown),
        lambda shown: not any("Obsolete" in column for column in shown),
    ),
    "relocate": _Operation(
        "relocate_card",
        True,
        _relocate,
        lambda shown: "Mover" in shown[1],
        _once("Mover", 1),
    ),
}

EXISTING = {
    "update_card": "Draft",
    "soft_delete_card": "Obsolete",
    "relocate_card": "Mover",
}


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            canvases.append(self)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


async def _open_ready(user: User, session_id: str) -> None:
    """Open the session page; return once its cards are loaded and Realtime is ready."""
    await user.open(f"/sessions/{session_id}")
    await user.should_see("DOES NOT", retries=100)
    await _until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))


def _persisted(session_id: str) -> list[tuple]:
    """(content, quadrant position, index) of the session's active cards, from the database."""
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        return connection.execute(
            'select c.content, q.position, c."index" from public.cards c '
            "join public.quadrants q on q.id = c.quadrant_id "
            "where c.session_id = %s and c.deleted_at is null",
            (session_id,),
        ).fetchall()


async def _perform(name: str, user: User, card: cards.Card | None) -> None:
    if name == "soft-delete":
        await _confirm_delete(user, card)
    else:
        OPERATIONS[name].act(user, card)


def _no_timer(*args, **kwargs):
    pytest.fail("client polling: ui.timer is not allowed")


EXPECTED = {
    "insert": [("Inserted", 0, 0)],
    "edit": [("Revised", 0, 0)],
    "soft-delete": [],
    "relocate": [("Mover", 1, 3)],
}


def _seed_card(operation: _Operation, session_id: str) -> cards.Card | None:
    """The card an operation acts on, when it acts on an existing one."""
    if not operation.existing:
        return None
    quadrant_id = _load_quadrant_ids(session_id)[0]
    return cards.insert_card(session_id, quadrant_id, 0, EXISTING[operation.service_call], "Alice")


async def _open_both(
    user: User,
    user_b: User,
    session_id: str,
    canvases: list[Canvas],
    card: cards.Card | None,
) -> tuple[Canvas, Canvas]:
    await _open_ready(user, session_id)
    await _open_ready(user_b, session_id)
    if card:
        await user_b.should_see(marker=f"edit-{card.id}", retries=100)
    canvas_a, canvas_b = canvases
    return canvas_a, canvas_b


async def _propagate(
    name: str,
    user: User,
    canvas_a: Canvas,
    canvas_b: Canvas,
    card: cards.Card | None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[float, list]:
    """A performs the operation; return the commit-to-B-render time and the polling seen."""
    operation = OPERATIONS[name]
    # Arm the instrumentation: from here on, any read or timer is polling.
    polling, gate = _forbid_polling(monkeypatch)
    timed = _Timed(getattr(cards, operation.service_call), gate)
    monkeypatch.setattr(cards, operation.service_call, timed)

    observed = asyncio.create_task(_observe(lambda: operation.rendered(_shown(canvas_b))))
    await _perform(name, user, card)
    rendered_at = await asyncio.wait_for(observed, timeout=10)
    await _until(timed.returned.is_set)
    await _until(lambda: operation.settled(_shown(canvas_a)) and not _pending_marked(user))
    await asyncio.sleep(0.3)  # a late duplicate would have appeared by now
    return rendered_at - timed.started, polling


def _assert_settled(operation: _Operation, canvas_a: Canvas, canvas_b: Canvas) -> None:
    for shown in (_shown(canvas_a), _shown(canvas_b)):
        assert operation.settled(shown)
        assert "Pending" not in [text for column in shown for text in column]
    assert not canvas_a._pending and not canvas_a._moving


@pytest.mark.parametrize("name", OPERATIONS)
async def test_card_operation_reaches_the_other_client_without_duplicates(
    name: str, user: User, create_user, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Propagation").session_id
    card = _seed_card(OPERATIONS[name], session_id)
    canvases = _record_canvases(monkeypatch)
    monkeypatch.setattr(ui, "timer", _no_timer)
    user_b = create_user()
    try:
        canvas_a, canvas_b = await _open_both(user, user_b, session_id, canvases, card)
        elapsed, polling = await _propagate(name, user, canvas_a, canvas_b, card, monkeypatch)
        assert elapsed < LATENCY_LIMIT
        _assert_settled(OPERATIONS[name], canvas_a, canvas_b)
        assert polling == []
    finally:
        await _release_all(session_id)

    assert _persisted(session_id) == EXPECTED[name]


async def _await_push_replaces_pending(user: User, canvas: Canvas) -> None:
    """The insert is committed but unresolved: its push must replace the pending card."""
    await _until(lambda: bool(canvas._cards))
    await _until(lambda: not _pending_marked(user))
    assert _once("Inserted", 0)(_shown(canvas))
    assert canvas._pending


async def _assert_reconciled_once(user: User, canvas: Canvas) -> None:
    await _until(lambda: not canvas._pending)
    await asyncio.sleep(0.3)
    assert _once("Inserted", 0)(_shown(canvas))
    assert "Pending" not in _labels(user.current_layout)
    assert len(canvas._cards) == 1


async def test_confirmed_insert_is_reconciled_when_its_push_arrives_first(
    user: User, create_user, monkeypatch: pytest.MonkeyPatch
):
    """The push of A's own insert reaches A before its insert call resolves."""
    session_id = create_session("Push First").session_id
    canvases = _record_canvases(monkeypatch)
    release = threading.Event()
    timed = _Timed(cards.insert_card, {"open": False}, hold=release)
    monkeypatch.setattr(cards, "insert_card", timed)
    try:
        await _open_ready(user, session_id)
        (canvas,) = canvases
        _insert(user, None)
        await asyncio.wait_for(asyncio.to_thread(timed.returned.wait, 10), timeout=15)
        await _await_push_replaces_pending(user, canvas)
        release.set()
        await _assert_reconciled_once(user, canvas)
    finally:
        release.set()
        await _release_all(session_id)
