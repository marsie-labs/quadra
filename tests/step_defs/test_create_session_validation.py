from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest.types import CountMethod
from pytest_bdd import given, scenario, then, when

from quadra import api
from quadra.sessions import get_client

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("workspaces", "canvases", "quadrants")


@scenario(
    "../features/create_session_validation.feature",
    "create session validation fails on empty input",
)
def test_create_session_validation():
    pass


@pytest.fixture
def context():
    load_dotenv(ROOT / ".env")
    return {}


def _record_counts():
    supabase = get_client()
    return {
        table: supabase.table(table).select("id", count=CountMethod.exact).execute().count
        for table in TABLES
    }


@given("the API route exists")
def _route(context):
    app = FastAPI()
    app.include_router(api.router)
    assert "/sessions" in {getattr(route, "path", None) for route in api.router.routes}
    context["client"] = TestClient(app)
    context["before"] = _record_counts()


@when("a POST request is sent to /sessions with feature_name ''")
def _post(context):
    context["response"] = context["client"].post("/sessions", json={"feature_name": ""})


@then("the response is 422")
def _status(context):
    assert context["response"].status_code == 422


@then("no session record is created in Supabase")
def _nothing_written(context):
    assert _record_counts() == context["before"]


@then("tests/api/test_sessions.py::test_create_session_validation passes")
def _api_test_passes():
    exit_code = pytest.main(
        [
            str(ROOT / "tests/api/test_sessions.py::test_create_session_validation"),
            "-q",
            "-p",
            "no:cacheprovider",
        ]
    )
    assert exit_code == pytest.ExitCode.OK
