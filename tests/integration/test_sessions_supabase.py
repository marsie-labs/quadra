import os
import time
import uuid
from pathlib import Path

import psycopg

from quadra.sessions import QUADRANTS, get_client

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


def _post_session(client):
    """POST /sessions; the elapsed time includes building the Supabase client."""
    started = time.perf_counter()
    response = client.post("/sessions", json={"feature_name": "Integration Feature"})
    return response, (time.perf_counter() - started) * 1000


def test_create_session_within_latency_limit(client):
    response, elapsed_ms = _post_session(client)

    assert response.status_code == 201
    assert elapsed_ms < 2000
    assert uuid.UUID(response.json()["session_id"]).version == 4


def _persisted_state(session_id):
    """Read back what Supabase holds for the session: (statuses, quadrant names)."""
    supabase = get_client()
    canvas_ids = _canvas_ids(supabase, session_id)
    assert len(canvas_ids) == 1
    return (
        _workspace_statuses(supabase, session_id),
        _quadrant_names(supabase, canvas_ids[0]),
    )


def test_create_session_persists_workspace_and_quadrants(client):
    response, _ = _post_session(client)
    body = response.json()

    assert response.status_code == 201
    assert (body["status"], body["link"]) == ("open", f"/sessions/{body['session_id']}")
    assert _persisted_state(body["session_id"]) == (["open"], sorted(QUADRANTS))
    assert body["canvas"]["quadrants"] == list(QUADRANTS)
