"""Database lifecycle boundary of session closure (supabase/migrations/0005_session_closure.sql).

Closure and every card write serialize on the session row's lock, and the database alone
rejects writes to a closed session, whichever path submits them.
"""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from postgrest.exceptions import APIError
from nicegui import ui
from nicegui.testing import User
from psycopg.rows import dict_row

from quadra import app as quadra_app
from quadra import cards
from quadra.sessions import create_session, get_session_status

# Shared harness of the canvas closure tests.
from test_canvas_close import _release_all, _wait_until_registered

MIGRATION = Path(__file__).resolve().parents[2] / "supabase/migrations/0005_session_closure.sql"
CLOSED = "Session is closed"
LOCK_WAIT = 15  # seconds a statement may stay blocked on the session row before failing

_INSERT = (
    'insert into public.cards (id, session_id, quadrant_id, "index", content, '
    "creator_display_name) values (%s, %s, %s, %s, %s, %s)"
)
_EDIT = "update public.cards set content = %s where id = %s"
_MOVE = 'update public.cards set quadrant_id = %s, "index" = %s where id = %s'
_SOFT_DELETE = "update public.cards set deleted_at = now() where id = %s"


@dataclass(frozen=True)
class Workspace:
    session_id: str
    quadrants: list[str]
    card_ids: list[str]


def _connect() -> psycopg.Connection:
    """A transaction-scoped connection; prepared statements are off for the pooler."""
    return psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None)


_reader: psycopg.Connection | None = None


@pytest.fixture(autouse=True)
def reader():
    """One owner connection for reading state back: connecting is slow against Supabase."""
    global _reader
    _reader = psycopg.connect(
        os.environ["SUPABASE_DB_URL"], autocommit=True, prepare_threshold=None, row_factory=dict_row
    )
    yield
    _reader.close()
    _reader = None


def _read(query: str, params: tuple = ()) -> list[dict]:
    assert _reader is not None
    return _reader.execute(query, params).fetchall()


def _as_application_role(connection: psycopg.Connection) -> psycopg.Connection:
    """Run the connection's current transaction as the role the application uses."""
    connection.execute("set local role anon")
    return connection


def _new_workspace() -> Workspace:
    session_id = create_session("Lifecycle boundary").session_id
    quadrants = [str(row["id"]) for row in _quadrants(session_id)]
    card_ids = [
        cards.insert_card(session_id, quadrants[0], index, f"Card {index}", "Alice").id
        for index in range(2)
    ]
    return Workspace(session_id, quadrants, card_ids)


def _quadrants(session_id: str) -> list[dict]:
    return _read(
        "select q.id, q.name, q.position from public.quadrants q "
        "join public.canvases c on c.id = q.canvas_id where c.workspace_id = %s "
        "order by q.position",
        (session_id,),
    )


def _mutations(workspace: Workspace) -> dict[str, tuple[str, tuple]]:
    """Each supported card mutation of the workspace as a statement and its parameters."""
    card = workspace.card_ids[0]
    return {
        "insert": (
            _INSERT,
            (str(uuid.uuid4()), workspace.session_id, workspace.quadrants[0], 9, "New", "Bob"),
        ),
        "edit": (_EDIT, ("Edited", card)),
        "move": (_MOVE, (workspace.quadrants[1], 7, card)),
        "soft_delete": (_SOFT_DELETE, (card,)),
    }


def _snapshot(session_id: str) -> list[dict]:
    """Every card row of the session, soft-deleted ones included."""
    return _read("select * from public.cards where session_id = %s order by id", (session_id,))


def _status(session_id: str) -> str | None:
    rows = _read("select status from public.workspaces where id = %s", (session_id,))
    return rows[0]["status"] if rows else None


def _close(session_id: str) -> str | None:
    """Close the session through the RPC, as the application role."""
    with _connect() as connection:
        row = _as_application_role(connection).execute(
            "select public.close_session(%s)", (session_id,)
        ).fetchone()
    assert row is not None
    return row[0]


def _attempt(
    connection: psycopg.Connection, statement: str, params: tuple
) -> psycopg.Error | None:
    """Run the statement and commit; the database error that rejected it, if any."""
    try:
        connection.execute(statement, params)
        connection.commit()
    except psycopg.Error as error:
        connection.rollback()
        return error
    return None


def _assert_closed_rejection(error: psycopg.Error | None) -> None:
    assert error is not None, "the closed session accepted the write"
    assert error.sqlstate == "P0001"
    assert error.diag.message_primary == CLOSED


def _backend_pid(connection: psycopg.Connection) -> int:
    """The server process of the connection's transaction (not the pooler's)."""
    row = connection.execute("select pg_backend_pid()").fetchone()
    assert row is not None
    return row[0]


def _wait_until_blocked(pid: int) -> None:
    """Block until the backend waits for a lock held by another transaction."""
    deadline = time.monotonic() + LOCK_WAIT
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as monitor:
        while time.monotonic() < deadline:
            if monitor.execute(
                "select 1 from pg_locks where pid = %s and not granted", (pid,)
            ).fetchone():
                return
            time.sleep(0.02)
    raise AssertionError("the statement never waited for the session lock")


def _transaction() -> psycopg.Connection:
    """An open transaction running as the application role."""
    return _as_application_role(_connect())


def _state(session_id: str) -> tuple[list[dict], list[dict], str | None]:
    return _snapshot(session_id), _quadrants(session_id), _status(session_id)


def _migration_leaves_state_unchanged() -> None:
    session_id = _new_workspace().session_id
    before = _state(session_id)
    assert (len(before[0]), len(before[1]), before[2]) == (2, 4, "open")

    assert _reader is not None
    _reader.execute(MIGRATION.read_text())

    assert _state(session_id) == before

    # A session inserted without naming a status defaults to open.
    inserted = str(uuid.uuid4())
    _read(
        "insert into public.workspaces (id, feature_name) values (%s, 'Default status') "
        "returning id",
        (inserted,),
    )
    assert _status(inserted) == "open"


def _migration_initializes_legacy_rows_and_constrains_status() -> None:
    """A row from before the migration with any other status becomes open; closed is kept."""
    assert _reader is not None
    closed = _new_workspace().session_id
    _close(closed)
    legacy = str(uuid.uuid4())
    # Recreate the pre-migration schema: no status constraint, a free-form status.
    _reader.execute("alter table public.workspaces drop constraint if exists workspaces_status_check")
    _reader.execute(
        "insert into public.workspaces (id, feature_name, status) values (%s, 'Legacy', 'pending')",
        (legacy,),
    )
    _reader.execute(MIGRATION.read_text())
    assert (_status(legacy), _status(closed)) == ("open", "closed")

    # From now on only open and closed are accepted.
    with pytest.raises(psycopg.errors.CheckViolation):
        _reader.execute(
            "insert into public.workspaces (id, feature_name, status) "
            "values (%s, 'Invalid', 'archived')",
            (str(uuid.uuid4()),),
        )


def _closure_is_idempotent(workspace: Workspace) -> None:
    session_id = workspace.session_id
    unknown = _close(str(uuid.uuid4()))  # unknown session: nothing to close
    first = _close(session_id)
    closed_snapshot = _snapshot(session_id)
    second = _close(session_id)  # already closed is success
    assert (unknown, first, second, _status(session_id)) == (None, "closed", "closed", "closed")
    assert _snapshot(session_id) == closed_snapshot


def _application_role_changes_no_row(connection: psycopg.Connection, statement: str, params: tuple):
    """The application role is given no policy to change sessions: no row changes."""
    _as_application_role(connection)
    try:
        assert connection.execute(statement, params).rowcount == 0
    except psycopg.errors.InsufficientPrivilege:
        pass
    connection.rollback()


def _lifecycle_trigger_rejects(connection: psycopg.Connection, statement: str, params: tuple):
    """Were the application role allowed to change sessions (a grant and policies that exist
    only inside this transaction), the lifecycle trigger still rejects the operation."""
    connection.execute("grant update, delete on public.workspaces to anon")
    for operation in ("update", "delete"):
        connection.execute(
            f"create policy temporary_{operation} on public.workspaces "
            f"for {operation} to anon using (true)"
        )
    _as_application_role(connection)
    try:
        with pytest.raises(psycopg.Error) as raised:
            connection.execute(statement, params)
        assert raised.value.sqlstate == "P0001"
    finally:
        connection.rollback()  # never keep the temporary grant and policies


def _owner_is_rejected(connection: psycopg.Connection, statement: str, params: tuple):
    """The table's owner is rejected by the lifecycle trigger too."""
    error = _attempt(connection, statement, params)
    assert error is not None
    assert error.sqlstate == "P0001"


def _closed_session_stays_closed(workspace: Workspace) -> None:
    session_id = workspace.session_id
    closed_state = _state(session_id)
    statements = (
        "update public.workspaces set status = 'open' where id = %s",
        "delete from public.workspaces where id = %s",
    )
    with _connect() as connection:
        for statement in statements:
            _application_role_changes_no_row(connection, statement, (session_id,))
            _lifecycle_trigger_rejects(connection, statement, (session_id,))
            _owner_is_rejected(connection, statement, (session_id,))
    assert _state(session_id) == closed_state


def _direct_writes_are_rejected(workspace: Workspace) -> None:
    """Every mutation, as the application role and as the table owner."""
    before = _snapshot(workspace.session_id)
    with _connect() as connection:
        for name, (statement, params) in _mutations(workspace).items():
            for as_application_role in (True, False):
                if as_application_role:
                    _as_application_role(connection)
                _assert_closed_rejection(_attempt(connection, statement, params))
                assert _snapshot(workspace.session_id) == before, f"{name} changed it"


def _reassignment_and_removal_are_rejected(workspace: Workspace) -> None:
    """Removing a card outright and reassigning it to or from another session."""
    session_id = workspace.session_id
    before = _snapshot(session_id)
    other = _new_workspace()
    other_before = _snapshot(other.session_id)
    reassign = "update public.cards set session_id = %s, quadrant_id = %s where id = %s"
    attempts = (
        (reassign, (other.session_id, other.quadrants[0], workspace.card_ids[0])),
        (reassign, (session_id, workspace.quadrants[0], other.card_ids[0])),
        ("delete from public.cards where id = %s", (workspace.card_ids[0],)),
    )
    with _connect() as connection:
        for statement, params in attempts:
            _assert_closed_rejection(_attempt(connection, statement, params))
    assert _snapshot(session_id) == before
    assert _snapshot(other.session_id) == other_before


def _card_service_surfaces_the_rejection(workspace: Workspace) -> None:
    session_id = workspace.session_id
    before = _snapshot(session_id)
    card = workspace.card_ids[0]
    service_calls = {
        "insert": lambda: cards.insert_card(session_id, workspace.quadrants[0], 9, "New", "Bob"),
        "edit": lambda: cards.update_card(session_id, card, "Edited"),
        "move": lambda: cards.relocate_card(session_id, card, workspace.quadrants[1], 7),
        "soft_delete": lambda: cards.soft_delete_card(session_id, card),
    }
    for name, call in service_calls.items():
        with pytest.raises(cards.CardError) as raised:
            call()
        cause = raised.value.__cause__
        assert isinstance(cause, APIError), name
        assert (cause.code, cause.message) == ("P0001", CLOSED), name
        assert _snapshot(session_id) == before, f"{name} changed a closed session"


def _closure_wins_the_lock(name: str) -> None:
    """The mutation started first but waits for the lock closure holds: it is rejected."""
    workspace = _new_workspace()
    statement, params = _mutations(workspace)[name]
    closer = _transaction()
    mutator = _transaction()
    try:
        closer.execute("select public.close_session(%s)", (workspace.session_id,))
        mutator_pid = _backend_pid(mutator)
        with ThreadPoolExecutor(max_workers=1) as pool:
            outcome = pool.submit(_attempt, mutator, statement, params)
            _wait_until_blocked(mutator_pid)
            closer.commit()
            at_closure = _snapshot(workspace.session_id)
            error = outcome.result(timeout=LOCK_WAIT)
    finally:
        closer.close()
        mutator.close()
    _assert_closed_rejection(error)
    assert _status(workspace.session_id) == "closed"
    assert _snapshot(workspace.session_id) == at_closure


def _close_while_mutation_holds_the_lock(workspace: Workspace, statement: str, params: tuple):
    """Closure waits for the uncommitted mutation, then closes once it has committed."""
    mutator = _transaction()
    closer = _transaction()
    try:
        mutator.execute(statement, params)  # holds the session lock until it commits
        closer_pid = _backend_pid(closer)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(
                lambda: closer.execute(
                    "select public.close_session(%s)", (workspace.session_id,)
                ).fetchone()
            )
            _wait_until_blocked(closer_pid)
            assert _status(workspace.session_id) == "open"
            mutator.commit()
            assert closing.result(timeout=LOCK_WAIT) == ("closed",)
            closer.commit()
    finally:
        mutator.close()
        closer.close()


def _mutation_wins_the_lock(name: str) -> None:
    """The mutation holds the lock before closure: closure waits and the mutation is kept."""
    workspace = _new_workspace()
    statement, params = _mutations(workspace)[name]
    before = _snapshot(workspace.session_id)
    _close_while_mutation_holds_the_lock(workspace, statement, params)
    assert _status(workspace.session_id) == "closed"
    at_closure = _snapshot(workspace.session_id)
    assert at_closure != before  # the mutation committed before closure survives
    # Nothing commits once closure has: a second attempt is rejected.
    statement, params = _mutations(workspace)[name]
    with _transaction() as connection:
        _assert_closed_rejection(_attempt(connection, statement, params))
    assert _snapshot(workspace.session_id) == at_closure


def test_database_lifecycle_boundary():
    _migration_leaves_state_unchanged()
    _migration_initializes_legacy_rows_and_constrains_status()

    workspace = _new_workspace()
    _closure_is_idempotent(workspace)
    _closed_session_stays_closed(workspace)
    _direct_writes_are_rejected(workspace)
    _reassignment_and_removal_are_rejected(workspace)
    _card_service_surfaces_the_rejection(workspace)

    for name in ("insert", "edit", "move", "soft_delete"):
        _closure_wins_the_lock(name)
        _mutation_wins_the_lock(name)


@pytest.fixture
def session_page(user: User):
    """Serve the production session page again after the simulation reset NiceGUI."""

    @ui.page("/sessions/{session_id}")
    async def page(session_id: str) -> None:
        await quadra_app.session_page(session_id)


async def test_anonymous_close_and_finalized_retrieval(client, user: User, session_page):
    """No credentials or roles anywhere: the canvas closes a session, the API serves it."""
    session_id = create_session("Anonymous lifecycle").session_id
    data_url = f"/sessions/{session_id}/data"
    try:
        await user.open(f"/sessions/{session_id}")
        await user.should_see(marker="close-session")
        await _wait_until_registered(session_id)

        _assert_still_open(client.get(data_url))

        user.find(marker="close-session").click()
        await user.should_see(marker="session-closed", retries=100)
        assert get_session_status(session_id) == "closed"

        _assert_finalized(client.get(data_url), session_id)
        assert client.get(f"/sessions/{uuid.uuid4()}/data").status_code == 404
    finally:
        await _release_all(session_id)


def _assert_still_open(response) -> None:
    assert response.status_code == 409
    assert response.json() == {"detail": "Session is still open"}


def _assert_finalized(response, session_id: str) -> None:
    assert response.status_code == 200
    body = response.json()
    assert (body["session_id"], body["status"]) == (session_id, "closed")
    assert len(body["quadrants"]) == 4
