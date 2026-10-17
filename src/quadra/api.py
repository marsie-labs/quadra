"""HTTP API: POST /sessions."""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, StringConstraints

from quadra.sessions import create_session

router = APIRouter()


class SessionCreateRequest(BaseModel):
    feature_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CanvasResponse(BaseModel):
    quadrants: list[str]


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    link: str
    canvas: CanvasResponse


@router.post("/sessions", status_code=201, response_model=SessionCreateResponse)
def post_session(request: SessionCreateRequest) -> SessionCreateResponse:
    session = create_session(request.feature_name)
    return SessionCreateResponse(
        session_id=session.session_id,
        status=session.status,
        link=f"/sessions/{session.session_id}",
        canvas=CanvasResponse(quadrants=list(session.quadrants)),
    )
