import re
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenario, then, when

from quadra.app import app
from quadra.sessions import QUADRANTS, create_session, get_session

ROOT = Path(__file__).resolve().parents[2]


@scenario(
    "../features/access_existing_session_link.feature", "access existing session link"
)
def test_access_existing_session_link():
    pass


@pytest.fixture
def context():
    load_dotenv(ROOT / ".env")
    return {}


# Session ids are random UUIDv4 values, so the id written in the scenario is
# illustrative: the steps create a real session and use the id Supabase now holds.


@given(parsers.parse("a valid session with id '{example_id}' exists in Supabase"))
def _session_exists(context, example_id):
    context["session"] = create_session("Scenario Feature")
    assert get_session(context["session"].session_id) is not None


@when(parsers.parse("a GET request is made to /sessions/{example_id}"))
def _get(context, example_id):
    with TestClient(app) as client:
        context["response"] = client.get(f"/sessions/{context['session'].session_id}")


@then(
    parsers.parse(
        "the /sessions/{example_id} page renders the canvas with quadrants IS, IS NOT, DOES, DOES NOT"
    )
)
def _renders_canvas(context, example_id):
    response = context["response"]
    assert response.status_code == 200
    texts = re.findall(r'"text":"([^"]*)"', response.text)
    assert texts[0] == "Scenario Feature"
    assert [text for text in texts if text in QUADRANTS] == list(QUADRANTS)
