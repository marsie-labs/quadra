"""HTTP API: POST /sessions and GET /sessions/{session_id}/data."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from quadra.sessions import (
    SessionStillOpenError,
    create_session,
    get_finalized_session_data,
)

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


class FinalizedCardResponse(BaseModel):
    id: str
    content: str
    author_display_name: str
    index: int
    created_at: str
    updated_at: str


class FinalizedQuadrantResponse(BaseModel):
    id: str
    name: str
    position: int
    cards: list[FinalizedCardResponse]


class FinalizedSessionResponse(BaseModel):
    session_id: str
    status: str
    quadrants: list[FinalizedQuadrantResponse]


@router.get("/sessions/{session_id}/data", response_model=FinalizedSessionResponse)
def get_session_data(session_id: UUID) -> FinalizedSessionResponse:
    try:
        data = get_finalized_session_data(str(session_id))
    except SessionStillOpenError:
        raise HTTPException(status_code=409, detail="Session is still open") from None
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return FinalizedSessionResponse.model_validate(asdict(data))
