import asyncio
import json
import os
import time
import uuid

import httpx
import psycopg
import pytest
from nicegui import Client, core, ui
from nicegui.testing import User
from realtime._async.channel import AsyncRealtimeChannel
from realtime.message import PostgresChangesMessage

from quadra import realtime, sessions
from quadra.realtime import SessionChange
from quadra.sessions import create_session

LATENCY_LIMIT = 2.0  # seconds, strictly less than


def _commit(sql: str, params: tuple) -> float:
    """Commit one statement; return the clock reading taken just before the commit."""
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        connection.execute(sql, params)
        started = time.perf_counter()
        connection.commit()
    return started


async def _commit_status(session_id: str, status: str | None = None) -> tuple[str, float]:
    """Commit a (unique) status on the session's workspace; return it and the commit time."""
    status = status or f"status-{uuid.uuid4()}"
    started = await asyncio.to_thread(
        _commit, "update public.workspaces set status = %s where id = %s", (status, session_id)
    )
    return status, started


async def _commit_quadrant_position(session_id: str) -> tuple[int, float]:
    position = uuid.uuid4().int % 30000 + 100
    started = await asyncio.to_thread(
        _commit,
        "update public.quadrants set position = %s where id = ("
        "select q.id from public.quadrants q join public.canvases c on c.id = q.canvas_id "
        "where c.workspace_id = %s order by q.name limit 1)",
        (position, session_id),
    )
    return position, started


@pytest.fixture
async def register():
    """register_view that unregisters every view again when the test ends."""
    registered: list[tuple[str, str]] = []

    async def register_view(session_id: str, view_id: str, callback) -> None:
        await realtime.register_view(session_id, view_id, callback)
        registered.append((session_id, view_id))

    yield register_view
    for session_id, view_id in registered:
        await realtime.unregister_view(session_id, view_id)


async def _next_change(received: list[SessionChange], count: int = 1) -> None:
    """Wait, in memory only, until `count` pushed changes were recorded."""
    deadline = time.perf_counter() + 10
    while len(received) < count:
        assert time.perf_counter() < deadline, "no change was pushed"
        await asyncio.sleep(0.005)


def _assert_within_limit(elapsed: float) -> None:
    assert elapsed < LATENCY_LIMIT, f"push took {elapsed:.3f}s"


async def _release_all(session_id: str) -> None:
    for view_id in realtime.view_ids(session_id):
        await realtime.unregister_view(session_id, view_id)


def _forbid_polling(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """From now on any HTTP request or session read is polling; return what it records."""
    polling: list[str] = []

    async def forbid_async_request(self, request, *args, **kwargs):
        polling.append(f"httpx {request.method} {request.url}")
        raise httpx.ConnectError("polling is forbidden")

    def forbid_request(self, request, *args, **kwargs):
        polling.append(f"httpx {request.method} {request.url}")
        raise httpx.ConnectError("polling is forbidden")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbid_async_request)
    monkeypatch.setattr(httpx.Client, "send", forbid_request)
    monkeypatch.setattr(sessions, "get_session", lambda *a, **k: polling.append("get_session"))
    return polling


def _record_notifications(monkeypatch: pytest.MonkeyPatch, path: list[str]) -> None:
    """Record every Supabase Realtime change notification the channel receives."""
    original_handle = AsyncRealtimeChannel._handle_message

    def recording_handle(channel, message):
        if isinstance(message, PostgresChangesMessage):
            path.append("supabase realtime notification")
        return original_handle(channel, message)

    monkeypatch.setattr(AsyncRealtimeChannel, "_handle_message", recording_handle)


def _record_websocket_push(
    monkeypatch: pytest.MonkeyPatch, client_id: str, status: str, path: list[str]
) -> tuple[asyncio.Event, list[float]]:
    """Record the NiceGUI WebSocket update carrying `status` to one client's room."""
    pushed = asyncio.Event()
    pushed_at: list[float] = []
    original_emit = core.sio.emit

    async def recording_emit(event, data=None, *args, room=None, **kwargs):
        result = await original_emit(event, data, *args, room=room, **kwargs)
        if event == "update" and room == client_id and status in json.dumps(data, default=str):
            path.append("nicegui websocket update")
            pushed_at.append(time.perf_counter())
            pushed.set()
        return result

    monkeypatch.setattr(core.sio, "emit", recording_emit)
    return pushed, pushed_at


def _mount_watch_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that renders each pushed change into its own DOM; ui.timer polling fails."""

    def no_timer(*args, **kwargs):
        pytest.fail("client polling: ui.timer is not allowed")

    monkeypatch.setattr(ui, "timer", no_timer)

    @ui.page("/watch/{session_id}")
    async def watch(session_id: str):
        client = ui.context.client
        ui.label("event log")

        def render(change: SessionChange) -> None:
            with client:  # the view's own DOM, updated over NiceGUI's WebSocket
                ui.label(f"{change.table} {change.event} {change.record['status']}")

        await realtime.register_view(session_id, str(client.id), render)
        client.on_disconnect(lambda: realtime.unregister_view(session_id, str(client.id)))


async def test_push_delivery_without_polling(
    user: User, create_user, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Push Delivery").session_id

    _mount_watch_page(monkeypatch)
    user_b = create_user()
    try:
        client_a: Client = await user.open(f"/watch/{session_id}")
        client_b: Client = await user_b.open(f"/watch/{session_id}")
        await user_b.should_see("event log")
        assert realtime.is_ready(session_id)
        assert realtime.view_ids(session_id) == {str(client_a.id), str(client_b.id)}

        # Arm the instrumentation: from here on, any read or timer is polling.
        path: list[str] = []
        polling = _forbid_polling(monkeypatch)
        _record_notifications(monkeypatch, path)
        status = f"status-{uuid.uuid4()}"
        pushed, pushed_at = _record_websocket_push(monkeypatch, client_b.id, status, path)

        started = (await _commit_status(session_id, status))[1]
        await asyncio.wait_for(pushed.wait(), timeout=10)

        _assert_within_limit(pushed_at[0] - started)
        await user_b.should_see(f"workspaces UPDATE {status}")
        assert path == ["supabase realtime notification", "nicegui websocket update"]
        assert polling == []
    finally:
        await _release_all(session_id)


def _assert_update(received: list[SessionChange], table: str, session_id: str, column: str, value):
    assert len(received) == 1
    change = received[0]
    assert (change.table, change.event, change.session_id) == (table, "UPDATE", session_id)
    assert change.record[column] == value


COMMITS = {
    "workspaces": (_commit_status, "status"),
    "quadrants": (_commit_quadrant_position, "position"),
}


@pytest.mark.parametrize("source", COMMITS)
async def test_session_and_quadrant_changes_reach_every_view(register, source):
    session_id = create_session("Sources").session_id
    first: list[SessionChange] = []
    second: list[SessionChange] = []
    await register(session_id, "first", first.append)
    await register(session_id, "second", second.append)
    assert realtime.is_ready(session_id)

    commit, column = COMMITS[source]
    value, _ = await commit(session_id)
    await _next_change(first)
    await _next_change(second)

    _assert_update(first, source, session_id, column, value)
    _assert_update(second, source, session_id, column, value)


async def test_changes_are_dispatched_only_to_views_of_their_session(register):
    session_one = create_session("Session One").session_id
    session_two = create_session("Session Two").session_id
    seen_one: list[SessionChange] = []
    seen_two: list[SessionChange] = []
    await register(session_one, "a", seen_one.append)
    await register(session_two, "b", seen_two.append)

    status_one, _ = await _commit_status(session_one)
    await _next_change(seen_one)
    status_two, _ = await _commit_status(session_two)
    await _next_change(seen_two)  # arrives after session one's change on the same socket

    assert [c.record["status"] for c in seen_one] == [status_one]
    assert [c.record["status"] for c in seen_two] == [status_two]
    assert {c.session_id for c in seen_two} == {session_two}


async def test_earlier_views_keep_the_subscription_alive(register):
    session_id = create_session("Shared").session_id
    received: list[SessionChange] = []
    await register(session_id, "first", lambda change: None)
    await register(session_id, "second", received.append)

    assert await realtime.unregister_view(session_id, "first") is False
    assert realtime.is_ready(session_id)
    assert realtime.view_ids(session_id) == {"second"}
    await _commit_status(session_id)
    await _next_change(received)  # the shared subscription still delivers


async def test_last_view_releases_the_subscription(register):
    session_id = create_session("Release").session_id
    await register(session_id, "only", lambda change: None)

    assert await realtime.unregister_view(session_id, "only") is True
    assert not realtime.is_ready(session_id)
    assert realtime.view_ids(session_id) == frozenset()
    assert await realtime.unregister_view(session_id, "only") is False
