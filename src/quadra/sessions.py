"""Session management service: provisions workspaces in Supabase."""

import os
import uuid
from dataclasses import dataclass

from postgrest.types import ReturnMethod
from supabase import Client, create_client

QUADRANTS: tuple[str, ...] = ("IS", "IS NOT", "DOES", "DOES NOT")


@dataclass(frozen=True)
class Session:
    session_id: str
    feature_name: str
    status: str
    quadrants: tuple[str, ...]


def get_client() -> Client:
    """Build a Supabase client from SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY."""
    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_PUBLISHABLE_KEY"]
    )


def create_session(feature_name: str, client: Client | None = None) -> Session:
    """Persist a workspace, its canvas and the four quadrants; return the session."""
    client = client or get_client()
    session_id = str(uuid.uuid4())
    canvas_id = str(uuid.uuid4())
    minimal = ReturnMethod.minimal

    client.table("workspaces").insert(
        {"id": session_id, "feature_name": feature_name, "status": "open"},
        returning=minimal,
    ).execute()
    client.table("canvases").insert(
        {"id": canvas_id, "workspace_id": session_id}, returning=minimal
    ).execute()
    client.table("quadrants").insert(
        [
            {
                "id": str(uuid.uuid4()),
                "canvas_id": canvas_id,
                "name": name,
                "position": position,
            }
            for position, name in enumerate(QUADRANTS)
        ],
        returning=minimal,
    ).execute()

    return Session(
        session_id=session_id,
        feature_name=feature_name,
        status="open",
        quadrants=QUADRANTS,
    )
