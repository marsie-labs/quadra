"""Collaborative canvas: renders a session's quadrants and their cards.

A view registers with the realtime bridge first and loads the session's active cards
only once the subscription is ready, so a commit can never fall between the two.
Notifications arriving while the snapshot loads are buffered and applied on top of it;
cards are keyed by id, so a change seen both ways is shown exactly once.

Card content and names are rendered with ui.label, i.e. as escaped plain text.
"""

import asyncio
import logging
from dataclasses import asdict, fields
from datetime import datetime

from nicegui import Client, ui

from quadra import cards, realtime
from quadra.cards import Card
from quadra.realtime import SessionChange
from quadra.sessions import Session, get_client

logger = logging.getLogger(__name__)

_CARD_FIELDS = frozenset(field.name for field in fields(Card))


def _load_quadrant_ids(session_id: str) -> list[str]:
    """Ids of the session's quadrants in canvas order."""
    rows = (
        get_client()
        .table("quadrants")
        .select("id, position, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .execute()
        .data
    )
    return [row["id"] for row in sorted(rows, key=lambda row: row["position"])]


def _instant(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _merged(current: Card | None, record: dict) -> Card | None:
    """The card a change record yields on top of `current`; None if incomplete or stale."""
    values = asdict(current) if current else {}
    values |= {name: record[name] for name in _CARD_FIELDS & record.keys()}
    if set(values) != set(_CARD_FIELDS):
        logger.warning("Ignoring incomplete card change %s", record)
        return None
    card = Card(**values)
    return None if _is_stale(card, current) else card


def _is_stale(card: Card, current: Card | None) -> bool:
    """True for a notification of a change older than what is already shown."""
    return current is not None and _instant(card.updated_at) < _instant(current.updated_at)


class Canvas:
    """The quadrant grid of one session, built in the current NiceGUI slot."""

    def __init__(self, session: Session) -> None:
        self._session_id = session.session_id
        self._client: Client = ui.context.client
        self._columns: list[ui.column] = []
        with ui.grid(columns=2).classes("w-full"):
            for name in session.quadrants:
                with ui.card().classes("min-h-48"):
                    ui.label(name).classes("text-h6")
                    self._columns.append(ui.column())
        self._quadrant_ids: list[str] = []
        self._cards: dict[str, Card] = {}
        self._buffered: list[SessionChange] = []  # changes seen while the snapshot loads
        self._loading = True

    async def start(self) -> None:
        """Register for pushes, then load the active cards and show them."""
        await self._client.connected()
        snapshot = await self._register_and_load()
        if snapshot is None:
            return
        self._show(snapshot)

    def _show(self, snapshot: list[Card]) -> None:
        """Take the snapshot, replay the changes buffered meanwhile, and render."""
        self._cards = {card.id: card for card in snapshot}
        self._loading = False
        for change in self._buffered:
            self._apply(change)
        self._buffered = []
        with self._client:
            for quadrant_id in self._quadrant_ids:
                self._render(quadrant_id)

    async def _register_and_load(self) -> list[Card] | None:
        """Subscribe, then load the snapshot; None (after showing an error) on failure."""
        try:
            await realtime.register_view(
                self._session_id, str(self._client.id), self._on_change
            )
            self._client.on_disconnect(self._stop)
            snapshot, self._quadrant_ids = await self._load()
        except Exception:
            logger.exception("Cards of session %s could not be loaded", self._session_id)
            with self._client:
                ui.label("Cards could not be loaded").classes("text-negative")
            return None
        return snapshot

    async def _load(self) -> tuple[list[Card], list[str]]:
        snapshot = await asyncio.to_thread(cards.list_cards, self._session_id)
        quadrant_ids = await asyncio.to_thread(_load_quadrant_ids, self._session_id)
        return snapshot, quadrant_ids

    async def _stop(self) -> None:
        await realtime.unregister_view(self._session_id, str(self._client.id))

    def _on_change(self, change: SessionChange) -> None:
        if change.table != "cards":
            return
        if self._loading:
            self._buffered.append(change)
            return
        with self._client:
            for quadrant_id in self._apply(change):
                self._render(quadrant_id)

    def _apply(self, change: SessionChange) -> set[str]:
        """Fold one card change into the cards; return the quadrants it affects."""
        current = self._cards.get(change.record.get("id", ""))
        card = _merged(current, change.record)
        if card is None:
            return set()
        if card.deleted_at is None:
            self._cards[card.id] = card
        else:
            self._cards.pop(card.id, None)
        return {card.quadrant_id} | ({current.quadrant_id} if current else set())

    def _render(self, quadrant_id: str) -> None:
        """Rebuild one quadrant's cards by index, then card id."""
        if quadrant_id not in self._quadrant_ids:
            return
        column = self._columns[self._quadrant_ids.index(quadrant_id)]
        column.clear()
        in_quadrant = sorted(
            (card for card in self._cards.values() if card.quadrant_id == quadrant_id),
            key=lambda card: (card.index, card.id),
        )
        with column:
            for card in in_quadrant:
                with ui.card().tight().classes("p-2"):
                    ui.label(card.content)
                    ui.label(card.creator_display_name).classes("text-caption")
