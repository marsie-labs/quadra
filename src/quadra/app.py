"""NiceGUI application mounted on the same FastAPI app as the HTTP API."""

from fastapi import FastAPI
from nicegui import ui

from quadra import api
from quadra.canvas import Canvas
from quadra.sessions import get_session

app = FastAPI()
app.include_router(api.router)


@ui.page("/sessions/{session_id}")
async def session_page(session_id: str) -> None:
    session = get_session(session_id)
    if session is None:
        ui.label("Session not found").classes("text-h4")
        return
    ui.label(session.feature_name).classes("text-h4")
    await Canvas(session).start()


ui.run_with(app)
