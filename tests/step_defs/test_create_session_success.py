import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_bdd import given, scenario, then, when

from quadra import api, sessions

ROOT = Path(__file__).resolve().parents[2]


class FakeSupabase:
    """In-memory stand-in for the Supabase client; records every insert."""

    def __init__(self):
        self.rows: dict[str, list[dict]] = {}

    def table(self, name):
        return _FakeTable(self, name)


class _FakeTable:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def insert(self, rows, **_):
        self.client.rows.setdefault(self.name, []).extend(
            rows if isinstance(rows, list) else [rows]
        )
        return self

    def execute(self):
        return None


@scenario("../features/create_session_success.feature", "create session success")
def test_create_session_success():
    pass


@pytest.fixture
def supabase(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(sessions, "get_client", lambda: fake)
    return fake


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def context():
    return {}


@given("the system is initialized with schema 0001_workspace.sql")
def _schema():
    sql = (ROOT / "supabase" / "migrations" / "0001_workspace.sql").read_text()
    for table in ("workspaces", "canvases", "quadrants"):
        assert f"create table if not exists public.{table}" in sql


@when("a POST request is sent to /sessions with feature_name 'Test Feature'")
def _post(http, supabase, context):
    context["response"] = http.post("/sessions", json={"feature_name": "Test Feature"})


@then("the response is 201")
def _status(context):
    assert context["response"].status_code == 201


@then("a session record exists in Supabase with status 'open'")
def _record(context, supabase):
    session_id = context["response"].json()["session_id"]
    workspaces = supabase.rows["workspaces"]
    assert [(w["id"], w["status"]) for w in workspaces] == [(session_id, "open")]
    assert len(supabase.rows["canvases"]) == 1
    assert [q["name"] for q in supabase.rows["quadrants"]] == [
        "IS",
        "IS NOT",
        "DOES",
        "DOES NOT",
    ]


@then("the response body contains a valid session_id and link")
def _body(context):
    body = context["response"].json()
    assert uuid.UUID(body["session_id"]).version == 4
    assert body["link"] == f"/sessions/{body['session_id']}"
    assert body["canvas"]["quadrants"] == ["IS", "IS NOT", "DOES", "DOES NOT"]


@then("tests/api/test_sessions.py::test_create_session_success passes")
def _api_test_passes():
    exit_code = pytest.main(
        [
            str(ROOT / "tests" / "api" / "test_sessions.py::test_create_session_success"),
            "-q",
            "-p",
            "no:cacheprovider",
        ]
    )
    assert exit_code == 0
