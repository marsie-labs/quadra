import asyncio
import os
import time
from collections.abc import Callable
from datetime import datetime

import psycopg
import pytest
from nicegui import ui
from nicegui.testing import User

from quadra import app as quadra_app
from quadra import cards, realtime
from quadra.realtime import SessionChange
from quadra.canvas import _load_quadrant_ids
from quadra.sessions import create_session

# Shared harness of the Realtime and canvas tests.
from test_canvas_propagation import _open_ready, _shown, _until
from test_realtime import LATENCY_LIMIT, _release_all

_UPDATE = "update public.cards set content = %s where id = %s"
_DELETE = "update public.cards set deleted_at = now() where id = %s"
_ROW = "select updated_at, deleted_at from public.cards where id = %s"


@pytest.fixture(autouse=True)
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


def _row(card_id: str) -> tuple[datetime, datetime | None]:
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as connection:
        row = connection.execute(_ROW, (card_id,)).fetchone()
    assert row is not None
    return row


def _t2_commits_while_t1_holds(
    card_id: str, t2: tuple[str, tuple]
) -> tuple[psycopg.Connection, tuple[datetime, datetime | None]]:
    """T1 starts first and holds; T2 changes the card and commits. Return T1 and T2's row."""
    t1 = psycopg.connect(os.environ["SUPABASE_DB_URL"])
    t1.execute("select now()")  # T1's transaction starts here, before T2's does
    time.sleep(0.05)
    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as t2_connection:
        t2_connection.execute(*t2)
    return t1, _row(card_id)


def _t1_commits(t1: psycopg.Connection, statement: tuple[str, tuple]) -> float:
    """T1 runs its statement and commits; the clock reading taken just before the commit."""
    t1.execute(*statement)
    committing = time.perf_counter()
    t1.commit()
    t1.close()
    return committing


async def _commit_and_wait(
    t1: psycopg.Connection, statement: tuple[str, tuple], shown: Callable[[], bool]
) -> float:
    """T1 commits; seconds from the commit until the connected canvas shows its state.

    The clock starts in the commit worker, before the commit call returns, so any delay
    before the coroutine resumes counts towards the interval rather than being excluded.
    """
    committing = await asyncio.to_thread(_t1_commits, t1, statement)
    await _until(shown, timeout=10)
    return time.perf_counter() - committing


def _newer_deletion(pushed: list[SessionChange], after: datetime) -> bool:
    """Whether a pushed card change carries a deletion version later than `after`."""
    return any(
        change.table == "cards"
        and change.record.get("deleted_at")
        and datetime.fromisoformat(change.record["deleted_at"]) > after
        for change in pushed
    )


def _texts(user: User) -> list[str]:
    return [text for column in _shown(user) for text in column]


async def test_update_committed_last_is_the_later_version_and_reaches_the_canvas(user: User):
    session_id = create_session("Commit Order Update").session_id
    card = cards.insert_card(session_id, _load_quadrant_ids(session_id)[0], 0, "Draft", "Alice")
    try:
        await _open_ready(user, session_id)
        await user.should_see(marker=f"edit-{card.id}", retries=100)

        t1, t2_row = await asyncio.to_thread(
            _t2_commits_while_t1_holds, card.id, (_UPDATE, ("From T2", card.id))
        )
        await _until(lambda: "From T2" in _texts(user))
        elapsed = await _commit_and_wait(
            t1, (_UPDATE, ("From T1", card.id)), lambda: "From T1" in _texts(user)
        )
    finally:
        await _release_all(session_id)

    assert _row(card.id)[0] > t2_row[0]
    assert elapsed < LATENCY_LIMIT
    assert "From T2" not in _texts(user)


async def test_soft_delete_committed_last_is_the_later_version_and_reaches_the_canvas(user: User):
    session_id = create_session("Commit Order Delete").session_id
    card = cards.insert_card(session_id, _load_quadrant_ids(session_id)[0], 0, "Draft", "Alice")
    try:
        await _open_ready(user, session_id)
        await user.should_see(marker=f"edit-{card.id}", retries=100)

        t1, t2_row = await asyncio.to_thread(
            _t2_commits_while_t1_holds, card.id, (_UPDATE, ("From T2", card.id))
        )
        await _until(lambda: "From T2" in _texts(user))
        elapsed = await _commit_and_wait(
            t1, (_DELETE, (card.id,)), lambda: "From T2" not in _texts(user)
        )
    finally:
        await _release_all(session_id)

    updated_at, deleted_at = _row(card.id)
    assert deleted_at is not None
    assert deleted_at > t2_row[0]
    assert updated_at > t2_row[0]
    assert elapsed < LATENCY_LIMIT


async def test_soft_delete_committed_last_has_the_later_deleted_at(user: User):
    session_id = create_session("Commit Order Double Delete").session_id
    card = cards.insert_card(session_id, _load_quadrant_ids(session_id)[0], 0, "Draft", "Alice")
    try:
        await _open_ready(user, session_id)
        await user.should_see(marker=f"edit-{card.id}", retries=100)

        t1, t2_row = await asyncio.to_thread(
            _t2_commits_while_t1_holds, card.id, (_DELETE, (card.id,))
        )
        await _until(lambda: "Draft" not in _texts(user))
        assert t2_row[1] is not None
        t2_deleted_at = t2_row[1]
        # T2's delete already emptied the canvas, so the page alone cannot show T1's
        # delete arriving: observe T1's own notification through a second view.
        pushed: list[SessionChange] = []
        await realtime.register_view(session_id, "probe", pushed.append)
        elapsed = await _commit_and_wait(
            t1, (_DELETE, (card.id,)), lambda: _newer_deletion(pushed, t2_deleted_at)
        )
        assert "Draft" not in _texts(user)
    finally:
        await _release_all(session_id)

    deleted_at = _row(card.id)[1]
    assert deleted_at is not None and deleted_at > t2_deleted_at
    assert elapsed < LATENCY_LIMIT
