import asyncio
import os
import time
import uuid

import httpx
import psycopg
import pytest
from nicegui import Client, ui
from nicegui.testing import User

from quadra import cards, realtime, sessions
from quadra.realtime import SessionChange
from quadra.sessions import create_session

# Shared harness of the session/quadrant Realtime tests, reused so both suites
# measure the same way.
from test_realtime import (  # noqa: F401  (register is a pytest fixture)
    _assert_within_limit,
    _commit_quadrant_position,
    _commit_feature_name,
    _next_change,
    _observe_in_dom,
    _record_notifications,
    _record_websocket_push,
    _release_all,
    register,
)


def _quadrant_id(session_id: str) -> str:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        (row,) = connection.execute(
            "select q.id from public.quadrants q join public.canvases c on c.id = q.canvas_id "
            "where c.workspace_id = %s order by q.position limit 1",
            (session_id,),
        ).fetchall()
    return str(row[0])


async def _insert_card(session_id: str, card_id: str | None = None) -> tuple[str, float]:
    """A inserts a card through card-service; return its id and the clock reading taken
    before the insert is sent, so the measured latency includes the commit itself."""
    quadrant_id = await asyncio.to_thread(_quadrant_id, session_id)
    started = time.perf_counter()
    card = await asyncio.to_thread(
        cards.insert_card,
        session_id,
        quadrant_id,
        0,
        "a card",
        "Alice",
        None,
        card_id,
    )
    return card.id, started


def _forbid_polling(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], dict[str, bool]]:
    """From now on any Supabase HTTP request or session read is polling.

    A's own card insert is the one action under test, not polling: the returned gate
    lets it through while it is open. Everything else, including every async request
    of the bridge itself, is recorded and fails.
    """
    polling: list[str] = []
    gate = {"open": False}
    original_send = httpx.Client.send

    async def forbid_async_request(self, request, *args, **kwargs):
        polling.append(f"httpx {request.method} {request.url}")
        raise httpx.ConnectError("polling is forbidden")

    def guarded_request(self, request, *args, **kwargs):
        if gate["open"]:
            return original_send(self, request, *args, **kwargs)
        polling.append(f"httpx {request.method} {request.url}")
        raise httpx.ConnectError("polling is forbidden")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbid_async_request)
    monkeypatch.setattr(httpx.Client, "send", guarded_request)
    monkeypatch.setattr(sessions, "get_session", lambda *a, **k: polling.append("get_session"))
    monkeypatch.setattr(cards, "list_cards", lambda *a, **k: polling.append("list_cards"))
    return polling, gate


def _mount_card_log_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only view: renders the id of every received card change into a DOM event log.

    The production card UI is not involved; ui.timer polling fails the test.
    """

    def no_timer(*args, **kwargs):
        pytest.fail("client polling: ui.timer is not allowed")

    monkeypatch.setattr(ui, "timer", no_timer)

    @ui.page("/card-log/{session_id}")
    async def card_log(session_id: str):
        client = ui.context.client
        ui.label("card event log")

        def render(change: SessionChange) -> None:
            if change.table != "cards":
                return
            with client:  # the view's own DOM, updated over NiceGUI's WebSocket
                ui.label(f"card {change.event} {change.record['id']}")

        await realtime.register_view(session_id, str(client.id), render)
        client.on_disconnect(lambda: realtime.unregister_view(session_id, str(client.id)))


async def _insert_with_gate(session_id: str, card_id: str, gate: dict[str, bool]) -> float:
    """A's insert is the one request polling detection lets through; return its start."""
    gate["open"] = True
    try:
        inserted_id, started = await _insert_card(session_id, card_id)
    finally:
        gate["open"] = False
    assert inserted_id == card_id
    return started


async def _open_two_views(user: User, user_b: User, session_id: str) -> Client:
    """Open the card log for A and B; return B's client once both are registered."""
    client_a: Client = await user.open(f"/card-log/{session_id}")
    client_b: Client = await user_b.open(f"/card-log/{session_id}")
    await user_b.should_see("card event log")
    assert realtime.is_ready(session_id)
    assert realtime.view_ids(session_id) == {str(client_a.id), str(client_b.id)}
    return client_b


async def test_card_insert_reaches_second_user_without_polling(
    user: User, create_user, monkeypatch: pytest.MonkeyPatch
):
    session_id = create_session("Card Push").session_id
    card_id = str(uuid.uuid4())

    _mount_card_log_page(monkeypatch)
    user_b = create_user()
    try:
        client_b = await _open_two_views(user, user_b, session_id)

        # Arm the instrumentation: from here on, any read or timer is polling.
        path: list[str] = []
        polling, gate = _forbid_polling(monkeypatch)
        _record_notifications(monkeypatch, path)
        delivered = _record_websocket_push(monkeypatch, client_b.id, card_id, path)

        observed = asyncio.create_task(_observe_in_dom(user_b, f"card INSERT {card_id}"))
        started = await _insert_with_gate(session_id, card_id, gate)
        rendered_at = await asyncio.wait_for(observed, timeout=10)
        await _next_change(delivered)

        _assert_within_limit(delivered[0] - started)
        _assert_within_limit(rendered_at - started)
        assert path == ["supabase realtime notification", "nicegui websocket update"]
        assert polling == []
    finally:
        await _release_all(session_id)


async def test_card_update_and_soft_delete_arrive_as_updates(register):
    session_id = create_session("Card Updates").session_id
    received: list[SessionChange] = []
    await register(session_id, "view", received.append)

    card_id, _ = await _insert_card(session_id)
    await _next_change(received, 1)
    await asyncio.to_thread(cards.update_card, session_id, card_id, "edited")
    await _next_change(received, 2)
    await asyncio.to_thread(cards.soft_delete_card, session_id, card_id)
    await _next_change(received, 3)

    assert [(c.table, c.event, c.session_id) for c in received] == [
        ("cards", "INSERT", session_id),
        ("cards", "UPDATE", session_id),
        ("cards", "UPDATE", session_id),
    ]
    assert {c.record["id"] for c in received} == {card_id}
    assert received[1].record["content"] == "edited"
    assert received[2].record["deleted_at"] is not None


async def test_card_changes_are_dispatched_only_to_views_of_their_session(register):
    session_one = create_session("Cards One").session_id
    session_two = create_session("Cards Two").session_id
    seen_one: list[SessionChange] = []
    seen_two: list[SessionChange] = []
    await register(session_one, "a", seen_one.append)
    await register(session_two, "b", seen_two.append)

    card_one, _ = await _insert_card(session_one)
    await _next_change(seen_one)
    card_two, _ = await _insert_card(session_two)
    await _next_change(seen_two)  # arrives after session one's change on the same socket

    assert [c.record["id"] for c in seen_one] == [card_one]
    assert [c.record["id"] for c in seen_two] == [card_two]
    assert {c.session_id for c in seen_one} == {session_one}
    assert {c.session_id for c in seen_two} == {session_two}


def _assert_mixed_changes(
    received: list[SessionChange], session_id: str, expected: dict[str, tuple[str, object]]
) -> None:
    """Each table's change keeps the session identity and the payload field committed."""
    assert {c.session_id for c in received} == {session_id}
    actual = {c.table: (field, c.record[field]) for c in received for field in [expected[c.table][0]]}
    assert actual == expected


async def test_session_and_quadrant_events_still_arrive_beside_card_events(register):
    session_id = create_session("Mixed Sources").session_id
    first: list[SessionChange] = []
    second: list[SessionChange] = []
    await register(session_id, "first", first.append)
    await register(session_id, "second", second.append)

    status, _ = await _commit_feature_name(session_id)
    await _next_change(first, 1)
    position, _ = await _commit_quadrant_position(session_id)
    await _next_change(first, 2)
    card_id, _ = await _insert_card(session_id)
    await _next_change(first, 3)
    await _next_change(second, 3)

    expected = {
        "workspaces": ("feature_name", status),
        "quadrants": ("position", position),
        "cards": ("id", card_id),
    }
    _assert_mixed_changes(first, session_id, expected)
    _assert_mixed_changes(second, session_id, expected)

    assert await realtime.unregister_view(session_id, "first") is False
    assert await realtime.unregister_view(session_id, "second") is True
    assert not realtime.is_ready(session_id)
