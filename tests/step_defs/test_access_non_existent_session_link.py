import re
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenario, then, when

from quadra.app import app
from quadra.sessions import QUADRANTS, get_session

ROOT = Path(__file__).resolve().parents[2]


@scenario(
    "../features/access_non_existent_session_link.feature",
    "access non-existent session link",
)
def test_access_non_existent_session_link():
    pass


@pytest.fixture
def context():
    load_dotenv(ROOT / ".env")
    return {}


@given(parsers.parse("no session with id '{session_id}' exists in Supabase"))
def _no_session(context, session_id):
    assert get_session(session_id) is None


@when(parsers.parse("a GET request is made to /sessions/{session_id}"))
def _get(context, session_id):
    with TestClient(app) as client:
        context["response"] = client.get(f"/sessions/{session_id}")


@then(parsers.parse("the /sessions/{session_id} page displays 'Session not found'"))
def _displays_not_found(context, session_id):
    response = context["response"]
    assert response.status_code == 200
    texts = re.findall(r'"text":"([^"]*)"', response.text)
    assert "Session not found" in texts
    assert not [text for text in texts if text in QUADRANTS]
