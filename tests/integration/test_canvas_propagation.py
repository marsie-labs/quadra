import asyncio
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import psycopg
import pytest
from nicegui import Client, core, ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards
from quadra.canvas import Canvas, _load_quadrant_ids
from quadra.realtime import SessionChange
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


def _shown(user: User) -> list[list[str]]:
    """The labels of each quadrant on the user's page, in quadrant order.

    Read from the page the simulated user is looking at, not from the canvas object.
    """
    grid = next(e for e in user.current_layout.descendants() if isinstance(e, ui.grid))
    return [_labels(quadrant) for quadrant in grid.default_slot.children]


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
    """Wall-clock reading when `condition` first holds; in memory only, never a read."""
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.001)
    return time.time()


def _clock_offset(samples: int = 5) -> float:
    """Seconds the database clock is ahead of this machine's clock.

    Taken from the tightest of a few round trips, so that the database's commit
    timestamps can be compared with this machine's readings (a common time reference).
    """
    tightest: tuple[float, float] | None = None
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        for _ in range(samples):
            before = time.time()
            row = connection.execute("select clock_timestamp()").fetchone()
            after = time.time()
            assert row is not None
            candidate = (after - before, row[0].timestamp() - (before + after) / 2)
            tightest = min(tightest, candidate) if tightest else candidate
    assert tightest is not None
    return tightest[1]


def _commit_time(change: SessionChange, offset: float) -> float:
    """The database commit of a pushed change, on this machine's clock."""
    return datetime.fromisoformat(change.commit_timestamp).timestamp() - offset


def _record_updates(
    monkeypatch: pytest.MonkeyPatch, client_id: str
) -> list[tuple[float, dict]]:
    """Record each NiceGUI WebSocket `update` sent to one client's room.

    An entry is (wall-clock reading when the socket emit completed, update payload):
    the moment the update left the server for that browser.
    """
    sent: list[tuple[float, dict]] = []
    original_emit = core.sio.emit

    async def recording_emit(event, data=None, *args, room=None, **kwargs):
        result = await original_emit(event, data, *args, room=room, **kwargs)
        if event == "update" and room == client_id:
            sent.append((time.time(), data))
        return result

    monkeypatch.setattr(core.sio, "emit", recording_emit)
    return sent


class _Timed:
    """Wraps one card-service call: lets only it through the polling guard and times it.

    After the real call returns, `hold` (when given) keeps the initiating client
    waiting, so a push can be seen before the call resolves.
    """

    def __init__(self, real, gate: dict[str, bool], hold: threading.Event | None = None) -> None:
        self._real = real
        self._gate = gate
        self._hold = hold
        self.returned = threading.Event()

    def __call__(self, *args, **kwargs):
        self._gate["open"] = True
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
    event: str  # the Realtime event B's canvas receives for it
    act: Callable[[User, cards.Card | None], None]
    rendered: Callable[[list[list[str]]], bool]
    settled: Callable[[list[list[str]]], bool]
    pushed: Callable[[dict, set[int]], bool]  # B's WebSocket update carries the change


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


def _carries(text: str) -> Callable[[dict, set[int]], bool]:
    """The update sends an element showing `text`."""
    return lambda data, gone: text in json.dumps(data, default=str)


def _removes(data: dict, gone: set[int]) -> bool:
    """The update deletes an element B showed before the operation."""
    return any(data.get(element_id, 0) is None for element_id in gone)


OPERATIONS = {
    "insert": _Operation(
        "insert_card",
        False,
        "INSERT",
        _insert,
        _once("Inserted", 0),
        _once("Inserted", 0),
        _carries("Inserted"),
    ),
    "edit": _Operation(
        "update_card",
        True,
        "UPDATE",
        _edit,
        lambda shown: "Revised" in shown[0],
        lambda shown: _once("Revised", 0)(shown) and not any("Draft" in c for c in shown),
        _carries("Revised"),
    ),
    "soft-delete": _Operation(
        "soft_delete_card",
        True,
        "UPDATE",
        None,  # confirmed through a dialog, see _perform
        lambda shown: not any("Obsolete" in column for column in shown),
        lambda shown: not any("Obsolete" in column for column in shown),
        _removes,
    ),
    "relocate": _Operation(
        "relocate_card",
        True,
        "UPDATE",
        _relocate,
        lambda shown: "Mover" in shown[1],
        _once("Mover", 1),
        _carries("Mover"),
    ),
}

EXISTING = {
    "update_card": "Draft",
    "soft_delete_card": "Obsolete",
    "relocate_card": "Mover",
}


def _record_canvases(monkeypatch: pytest.MonkeyPatch) -> list[Canvas]:
    """Record each canvas the page builds, with the card changes its view receives."""
    canvases: list[Canvas] = []

    class _Recorded(Canvas):
        def __init__(self, session) -> None:
            super().__init__(session)
            self.received: list[SessionChange] = []
            canvases.append(self)

        def _on_change(self, change: SessionChange) -> None:
            if change.table == "cards":
                self.received.append(change)
            super()._on_change(change)

    monkeypatch.setattr(quadra_app, "Canvas", _Recorded)
    return canvases


async def _open_ready(user: User, session_id: str) -> Client:
    """Open the session page; return its client once cards are loaded and Realtime is ready."""
    client: Client = await user.open(f"/sessions/{session_id}")
    await user.should_see("DOES NOT", retries=100)
    await _until(lambda: all(b.enabled for b in user.find(marker="submit-0").elements))
    return client


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


@dataclass(frozen=True)
class _Delivery:
    """When one operation reached B, all as wall-clock readings of this machine."""

    commit: float  # the database commit, from the change's commit timestamp
    page: float  # B's page first showing the change
    socket: float  # the NiceGUI WebSocket update with it leaving for B


async def _open_both(
    user: User,
    user_b: User,
    session_id: str,
    canvases: list[Canvas],
    card: cards.Card | None,
) -> tuple[Canvas, Canvas, Client]:
    await _open_ready(user, session_id)
    client_b = await _open_ready(user_b, session_id)
    if card:
        await user_b.should_see(marker=f"edit-{card.id}", retries=100)
    canvas_a, canvas_b = canvases
    return canvas_a, canvas_b, client_b


def _shown_ids(user: User, text: str) -> set[int]:
    """Ids of the elements of the user's page showing `text`."""
    return {
        e.id
        for e in user.current_layout.descendants()
        if isinstance(e, ui.label) and e.text == text
    }


async def _socket_delivery(
    operation: _Operation, updates: list[tuple[float, dict]], gone: set[int]
) -> float:
    """Wall-clock reading when the update carrying the operation left for B."""

    def first_push() -> float | None:
        return next((at for at, data in updates if operation.pushed(data, gone)), None)

    await _until(lambda: first_push() is not None)
    socket = first_push()
    assert socket is not None
    return socket


async def _settle(operation: _Operation, user: User, timed: _Timed) -> None:
    """Wait until A's own call resolved and A shows the confirmed state, then for duplicates."""
    await _until(timed.returned.is_set)
    await _until(lambda: operation.settled(_shown(user)) and not _pending_marked(user))
    await asyncio.sleep(0.3)  # a late duplicate would have appeared by now


async def _propagate(
    name: str,
    users: tuple[User, User],
    canvases: tuple[Canvas, Canvas],
    client_b: Client,
    card: cards.Card | None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_Delivery, list]:
    """A performs the operation; return when it reached B and the polling seen."""
    user, user_b = users
    canvas_a, canvas_b = canvases
    operation = OPERATIONS[name]
    offset = await asyncio.to_thread(_clock_offset)
    gone = _shown_ids(user_b, EXISTING.get(operation.service_call, ""))
    # Arm the instrumentation: from here on, any read or timer is polling.
    polling, gate = _forbid_polling(monkeypatch)
    timed = _Timed(getattr(cards, operation.service_call), gate)
    monkeypatch.setattr(cards, operation.service_call, timed)
    updates = _record_updates(monkeypatch, str(client_b.id))
    canvas_b.received.clear()

    observed = asyncio.create_task(_observe(lambda: operation.rendered(_shown(user_b))))
    await _perform(name, user, card)
    page = await asyncio.wait_for(observed, timeout=10)
    socket = await _socket_delivery(operation, updates, gone)
    await _settle(operation, user, timed)

    # Exactly one committed change reached B's view, and it is the operation's own.
    assert [change.event for change in canvas_b.received] == [operation.event]
    commit = _commit_time(canvas_b.received[0], offset)
    return _Delivery(commit, page, socket), polling


def _assert_settled(operation: _Operation, users: tuple[User, User], canvas_a: Canvas) -> None:
    for user in users:
        shown = _shown(user)
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
        canvas_a, canvas_b, client_b = await _open_both(user, user_b, session_id, canvases, card)
        delivery, polling = await _propagate(
            name, (user, user_b), (canvas_a, canvas_b), client_b, card, monkeypatch
        )
        # From the database commit to B's page and to B's WebSocket update.
        assert delivery.page - delivery.commit < LATENCY_LIMIT
        assert delivery.socket - delivery.commit < LATENCY_LIMIT
        _assert_settled(OPERATIONS[name], (user, user_b), canvas_a)
        assert polling == []
    finally:
        await _release_all(session_id)

    assert _persisted(session_id) == EXPECTED[name]


async def _await_push_replaces_pending(user: User, canvas: Canvas) -> None:
    """The insert is committed but unresolved: its push must replace the pending card."""
    await _until(lambda: bool(canvas._cards))
    await _until(lambda: not _pending_marked(user))
    assert _once("Inserted", 0)(_shown(user))
    assert not canvas._pending


async def _assert_reconciled_once(user: User, canvas: Canvas) -> None:
    await _until(lambda: not canvas._pending)
    await asyncio.sleep(0.3)
    assert _once("Inserted", 0)(_shown(user))
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
