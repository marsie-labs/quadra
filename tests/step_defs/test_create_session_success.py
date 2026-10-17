import os
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_bdd import given, scenario, then, when

from quadra import api
from quadra.sessions import QUADRANTS, get_client

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "0001_workspace.sql"


@scenario("../features/create_session_success.feature", "create session success")
def test_create_session_success():
    pass


@pytest.fixture
def context():
    load_dotenv(ROOT / ".env")
    return {}


@given("the system is initialized with schema 0001_workspace.sql")
def _schema(context):
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as connection:
        connection.execute(MIGRATION.read_text())
        tables = {
            row[0]
            for row in connection.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'public'"
            )
        }
    assert {"workspaces", "canvases", "quadrants"} <= tables


@when("a POST request is sent to /sessions with feature_name 'Test Feature'")
def _post(context):
    app = FastAPI()
    app.include_router(api.router)
    context["response"] = TestClient(app).post(
        "/sessions", json={"feature_name": "Test Feature"}
    )


@then("the response is 201")
def _status(context):
    assert context["response"].status_code == 201


@then("a session record exists in Supabase with status 'open'")
def _record(context):
    session_id = context["response"].json()["session_id"]
    supabase = get_client()

    workspaces = (
        supabase.table("workspaces").select("status").eq("id", session_id).execute()
    )
    assert [row["status"] for row in workspaces.data] == ["open"]

    canvases = (
        supabase.table("canvases").select("id").eq("workspace_id", session_id).execute()
    )
    assert len(canvases.data) == 1
    quadrants = (
        supabase.table("quadrants")
        .select("name")
        .eq("canvas_id", canvases.data[0]["id"])
        .execute()
    )
    assert sorted(row["name"] for row in quadrants.data) == sorted(QUADRANTS)


@then("the response body contains a valid session_id and link")
def _body(context):
    body = context["response"].json()
    assert uuid.UUID(body["session_id"]).version == 4
    assert body["status"] == "open"
    assert body["link"] == f"/sessions/{body['session_id']}"
    assert body["canvas"]["quadrants"] == list(QUADRANTS)


@then("tests/api/test_sessions.py::test_create_session_success passes")
def _api_test_passes():
    exit_code = pytest.main(
        [
            str(ROOT / "tests/api/test_sessions.py::test_create_session_success"),
            "-q",
            "-p",
            "no:cacheprovider",
        ]
    )
    assert exit_code == pytest.ExitCode.OK
