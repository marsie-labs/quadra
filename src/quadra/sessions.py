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


@dataclass(frozen=True)
class FinalizedCard:
    id: str
    content: str
    author_display_name: str
    index: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FinalizedQuadrant:
    id: str
    name: str
    position: int
    cards: tuple[FinalizedCard, ...]


@dataclass(frozen=True)
class FinalizedSession:
    session_id: str
    status: str
    quadrants: tuple[FinalizedQuadrant, ...]


class SessionStillOpenError(Exception):
    """The session exists but is not closed, so it has no finalized data yet."""


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


def get_session(session_id: str, client: Client | None = None) -> Session | None:
    """Load a session with its quadrants in canvas order; None when it does not exist."""
    client = client or get_client()
    rows = (
        client.table("workspaces")
        .select("id, feature_name, status, canvases(quadrants(name, position))")
        .eq("id", session_id)
        .execute()
        .data
    )
    return _to_session(rows[0]) if rows else None


def get_session_status(session_id: str, client: Client | None = None) -> str | None:
    """Lifecycle state ("open" or "closed") of a session; None when it does not exist."""
    client = client or get_client()
    rows = (
        client.table("workspaces").select("status").eq("id", session_id).execute().data
    )
    return rows[0]["status"] if rows else None


def close_session(session_id: str, client: Client | None = None) -> str | None:
    """Irreversibly close a session; return "closed" (also when it already was).

    None means the session does not exist. The database owns the transition.
    """
    client = client or get_client()
    return client.rpc("close_session", {"p_session_id": session_id}).execute().data


def get_finalized_session_data(
    session_id: str, client: Client | None = None
) -> FinalizedSession | None:
    """Every non-deleted card of a closed session, grouped by its four quadrants.

    None means the session does not exist; SessionStillOpenError that it is open.
    Quadrants come in canvas order, cards in the deterministic list_cards order.
    """
    from quadra import cards  # cards imports get_client from this module

    from quadra.cards import list_cards  # cards imports get_client from this module

    client = client or get_client()
    status = get_session_status(session_id, client)
    if status is None:
        return None
    if status != "closed":
        raise SessionStillOpenError(session_id)

    quadrants = _load_quadrants(session_id, client)
    return FinalizedSession(
        session_id=session_id,
        status=status,
        quadrants=_group_cards(quadrants, list_cards(session_id, client)),
    )


def _group_cards(quadrants: list[dict], stored_cards) -> tuple[FinalizedQuadrant, ...]:
    """Place cards (already in list order) into their quadrants, keeping empty ones."""
    by_quadrant: dict[str, list[FinalizedCard]] = {q["id"]: [] for q in quadrants}
    for card in stored_cards:
        by_quadrant[card.quadrant_id].append(_to_finalized_card(card))
    return tuple(
        FinalizedQuadrant(
            id=q["id"],
            name=q["name"],
            position=q["position"],
            cards=tuple(by_quadrant[q["id"]]),
        )
        for q in quadrants
    )


def _load_quadrants(session_id: str, client: Client) -> list[dict]:
    """The session's quadrants (id, name, position) in canvas order."""
    canvases = (
        client.table("canvases")
        .select("quadrants(id, name, position)")
        .eq("workspace_id", session_id)
        .execute()
        .data
    )
    return sorted(
        (q for canvas in canvases for q in canvas["quadrants"]),
        key=lambda quadrant: quadrant["position"],
    )


def _to_finalized_card(card) -> FinalizedCard:
    """Map a stored card to its finalized, API-facing shape."""
    return FinalizedCard(
        id=card.id,
        content=card.content,
        author_display_name=card.creator_display_name,
        index=card.index,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def _to_session(workspace: dict) -> Session:
    """Map a workspace row with nested canvases/quadrants to a Session."""
    quadrants = sorted(
        (q for canvas in workspace["canvases"] for q in canvas["quadrants"]),
        key=lambda quadrant: quadrant["position"],
    )
    return Session(
        session_id=workspace["id"],
        feature_name=workspace["feature_name"],
        status=workspace["status"],
        quadrants=tuple(q["name"] for q in quadrants),
    )
