"""Card service: session-scoped card persistence in Supabase.

Supabase is authoritative: timestamps, authorship and session scoping are enforced by
the database (see supabase/migrations/0003_cards.sql); this module validates inputs up
front and maps failures to typed errors, leaving persisted state unchanged on rejection.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from postgrest.exceptions import APIError
from supabase import Client

from quadra.sessions import get_client

_COLUMNS = (
    "id, session_id, quadrant_id, index, content, creator_display_name, "
    "created_at, updated_at, deleted_at"
)

# Rows requested per page by list_cards; read at call time.
_PAGE_SIZE = 1000


class CardError(Exception):
    """Base class of card service errors."""


class CardValidationError(CardError):
    """The request is invalid or references a card or quadrant outside the session."""


class CardPersistenceError(CardError):
    """Supabase failed to persist the request."""


@dataclass(frozen=True)
class Card:
    id: str
    session_id: str
    quadrant_id: str
    index: int
    content: str
    creator_display_name: str
    created_at: str
    updated_at: str
    deleted_at: str | None


def list_cards(session_id: str, client: Client | None = None) -> list[Card]:
    """Non-deleted cards of the session by quadrant position, index, then card id."""
    session_id = _uuid(session_id, "session_id")
    client = client or get_client()
    page_size = _PAGE_SIZE
    rows: list[dict] = []
    while True:
        start = len(rows)
        page = _run(
            lambda: (
                client.table("cards")
                .select(f"{_COLUMNS}, quadrants(position)")
                .eq("session_id", session_id)
                .is_("deleted_at", "null")
                .order("id")
                .range(start, start + page_size - 1)
                .execute()
                .data
            )
        )
        rows.extend(page)
        if len(page) < page_size:
            break
    rows.sort(key=lambda row: (row["quadrants"]["position"], row["index"], row["id"]))
    return [_to_card(row) for row in rows]


def insert_card(
    session_id: str,
    quadrant_id: str,
    index: int,
    content: str,
    creator_display_name: str,
    client: Client | None = None,
    card_id: str | None = None,
) -> Card:
    """Insert a card into a quadrant of the session; card_id lets callers pre-assign it."""
    client = client or get_client()
    session_id = _uuid(session_id, "session_id")
    quadrant_id = _uuid(quadrant_id, "quadrant_id")
    card_id = _uuid(card_id, "card_id") if card_id else str(uuid.uuid4())
    _validate_index(index)
    _validate_content(content)
    creator_display_name = (creator_display_name or "").strip()
    if not creator_display_name:
        raise CardValidationError("creator_display_name must not be empty")
    _require_quadrant_in_session(client, session_id, quadrant_id)
    return _first(
        _run(
            lambda: client.table("cards")
            .insert(
                {
                    "id": card_id,
                    "session_id": session_id,
                    "quadrant_id": quadrant_id,
                    "index": index,
                    "content": content,
                    "creator_display_name": creator_display_name,
                }
            )
            .execute()
            .data
        )
    )


def update_card(
    session_id: str, card_id: str, content: str, client: Client | None = None
) -> Card:
    """Replace the content of an active card of the session."""
    _validate_content(content)
    return _mutate(session_id, card_id, {"content": content}, client)


def soft_delete_card(
    session_id: str, card_id: str, client: Client | None = None
) -> Card:
    """Mark an active card of the session deleted; the row is kept."""
    # The database replaces this instant with its own clock.
    return _mutate(
        session_id, card_id, {"deleted_at": datetime.now(UTC).isoformat()}, client
    )


def relocate_card(
    session_id: str,
    card_id: str,
    quadrant_id: str,
    index: int,
    client: Client | None = None,
) -> Card:
    """Move an active card of the session to a quadrant of the same session."""
    client = client or get_client()
    quadrant_id = _uuid(quadrant_id, "quadrant_id")
    _validate_index(index)
    _require_quadrant_in_session(client, _uuid(session_id, "session_id"), quadrant_id)
    return _mutate(
        session_id, card_id, {"quadrant_id": quadrant_id, "index": index}, client
    )


def _mutate(
    session_id: str, card_id: str, changes: dict, client: Client | None
) -> Card:
    """Apply changes to an active card of the session, or raise when there is none."""
    client = client or get_client()
    session_id = _uuid(session_id, "session_id")
    card_id = _uuid(card_id, "card_id")
    rows = _run(
        lambda: client.table("cards")
        .update(changes)
        .eq("id", card_id)
        .eq("session_id", session_id)
        .is_("deleted_at", "null")
        .execute()
        .data
    )
    if not rows:
        raise CardValidationError(f"no active card {card_id} in session {session_id}")
    return _to_card(rows[0])


def _require_quadrant_in_session(
    client: Client, session_id: str, quadrant_id: str
) -> None:
    rows = _run(
        lambda: client.table("quadrants")
        .select("id, canvases!inner(workspace_id)")
        .eq("id", quadrant_id)
        .eq("canvases.workspace_id", session_id)
        .execute()
        .data
    )
    if not rows:
        raise CardValidationError(
            f"quadrant {quadrant_id} does not belong to session {session_id}"
        )


def _run(operation):
    """Run a Supabase call, mapping its failures to CardError subclasses."""
    try:
        return operation()
    except APIError as error:
        # 23xxx are integrity violations: the request itself is invalid.
        if str(error.code or "").startswith("23"):
            raise CardValidationError(error.message) from error
        raise CardPersistenceError(error.message) from error
    except Exception as error:
        raise CardPersistenceError(str(error)) from error


def _first(rows: list[dict]) -> Card:
    if not rows:
        raise CardPersistenceError("Supabase returned no card")
    return _to_card(rows[0])


def _to_card(row: dict) -> Card:
    return Card(**{field: row[field] for field in _COLUMNS.replace(" ", "").split(",")})


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as error:
        raise CardValidationError(f"{name} must be a UUID") from error


def _validate_index(index: int) -> None:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise CardValidationError("index must be a non-negative integer")


def _validate_content(content: str) -> None:
    if not isinstance(content, str):
        raise CardValidationError("content must be plain text")
