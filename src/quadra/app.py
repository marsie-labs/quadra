"""NiceGUI application mounted on the same FastAPI app as the HTTP API."""

from fastapi import FastAPI
from nicegui import ui

from quadra import api
from quadra.sessions import get_session

app = FastAPI()
app.include_router(api.router)


@ui.page("/sessions/{session_id}")
def session_page(session_id: str) -> None:
    session = get_session(session_id)
    if session is None:
        ui.label("Session not found").classes("text-h4")
        return
    ui.label(session.feature_name).classes("text-h4")
    with ui.grid(columns=2).classes("w-full"):
        for name in session.quadrants:
            with ui.card().classes("min-h-48"):
                ui.label(name).classes("text-h6")


ui.run_with(app)
