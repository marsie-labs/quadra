import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quadra import api
from quadra.sessions import QUADRANTS, Session

FAKE_SESSION_ID = str(uuid.uuid4())


@pytest.fixture
def calls(monkeypatch):
    recorded: list[str] = []

    def fake_create_session(feature_name: str) -> Session:
        recorded.append(feature_name)
        return Session(
            session_id=FAKE_SESSION_ID,
            feature_name=feature_name,
            status="open",
            quadrants=QUADRANTS,
        )

    monkeypatch.setattr(api, "create_session", fake_create_session)
    return recorded


@pytest.fixture
def client(calls):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_create_session_success(client, calls):
    response = client.post("/sessions", json={"feature_name": "Test Feature"})

    assert response.status_code == 201
    assert response.json() == {
        "session_id": FAKE_SESSION_ID,
        "status": "open",
        "link": f"/sessions/{FAKE_SESSION_ID}",
        "canvas": {"quadrants": ["IS", "IS NOT", "DOES", "DOES NOT"]},
    }
    assert uuid.UUID(response.json()["session_id"]).version == 4
    assert calls == ["Test Feature"]


@pytest.mark.parametrize("body", [{"feature_name": ""}, {"feature_name": "   "}, {}])
def test_create_session_validation(client, calls, body):
    response = client.post("/sessions", json=body)

    assert response.status_code == 422
    assert calls == []
