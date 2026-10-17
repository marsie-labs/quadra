import re

import pytest
from fastapi.testclient import TestClient

from quadra.app import app
from quadra.sessions import QUADRANTS, create_session


@pytest.fixture
def page_client():
    """The real app (API and NiceGUI page) with its lifespan running."""
    with TestClient(app) as client:
        yield client


def _rendered_texts(html: str) -> list[str]:
    """Label texts NiceGUI embeds in the served page."""
    return re.findall(r'"text":"([^"]*)"', html)


def test_session_page_renders_canvas_with_four_quadrants(page_client):
    session = create_session("Page Feature")

    response = page_client.get(f"/sessions/{session.session_id}")

    assert response.status_code == 200
    texts = _rendered_texts(response.text)
    assert texts[0] == "Page Feature"
    assert [text for text in texts if text in QUADRANTS] == list(QUADRANTS)


def test_session_page_displays_not_found_for_missing_session(page_client):
    response = page_client.get("/sessions/00000000-0000-4000-8000-000000000000")

    assert response.status_code == 200
    texts = _rendered_texts(response.text)
    assert texts == ["Session not found"]
    assert not [text for text in texts if text in QUADRANTS]


def test_post_sessions_is_unchanged_with_the_page_mounted(page_client):
    response = page_client.post("/sessions", json={"feature_name": "Still Works"})

    assert response.status_code == 201
    assert response.json()["link"] == f"/sessions/{response.json()['session_id']}"
