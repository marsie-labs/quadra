import os
import time
import uuid
from pathlib import Path

import psycopg

from quadra.sessions import QUADRANTS, create_session, get_client

MIGRATION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "0001_workspace.sql"


def test_migration_applies_twice_and_creates_tables():
    sql = MIGRATION.read_text()
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as connection:
        connection.execute(sql)
        connection.execute(sql)
        tables = {
            row[0]
            for row in connection.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'public'"
            )
        }
    assert {"workspaces", "canvases", "quadrants"} <= tables


def _canvas_ids(client, session_id):
    canvases = (
        client.table("canvases").select("id").eq("workspace_id", session_id).execute()
    )
    return [row["id"] for row in canvases.data]


def _quadrant_names(client, canvas_id):
    quadrants = client.table("quadrants").select("name").eq("canvas_id", canvas_id).execute()
    return sorted(row["name"] for row in quadrants.data)


def _workspace_statuses(client, session_id):
    workspace = client.table("workspaces").select("status").eq("id", session_id).execute()
    return [row["status"] for row in workspace.data]


def _timed_create(client):
    started = time.perf_counter()
    session = create_session("Integration Feature", client)
    return session, (time.perf_counter() - started) * 1000


def test_create_session_within_latency_limit():
    session, elapsed_ms = _timed_create(get_client())

    assert elapsed_ms < 2000
    assert uuid.UUID(session.session_id).version == 4


def test_create_session_persists_workspace_and_quadrants():
    client = get_client()
    session, _ = _timed_create(client)

    assert _workspace_statuses(client, session.session_id) == ["open"]
    canvas_ids = _canvas_ids(client, session.session_id)
    assert len(canvas_ids) == 1
    assert _quadrant_names(client, canvas_ids[0]) == sorted(QUADRANTS)
