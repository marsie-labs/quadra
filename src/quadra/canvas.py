"""Collaborative canvas: renders a session's quadrants and their cards.

A view registers with the realtime bridge first and loads the session's active cards
only once the subscription is ready, so a commit can never fall between the two.
Notifications arriving while the snapshot loads are buffered and applied on top of it;
cards are keyed by id, so a change seen both ways is shown exactly once.

Card content and names are rendered with ui.label, i.e. as escaped plain text. The
display name a participant types is kept per connected view: it is not an identity.
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
        self._submit_buttons: list[ui.button] = []
        self._display_name = ui.input("Your display name").mark("display-name")
        with ui.grid(columns=2).classes("w-full"):
            for position, name in enumerate(session.quadrants):
                with ui.card().classes("min-h-48"):
                    ui.label(name).classes("text-h6")
                    self._columns.append(ui.column())
                    self._compose(position)
        self._quadrant_ids: list[str] = []
        self._cards: dict[str, Card] = {}
        self._drafts: dict[str, str] = {}  # card id -> text in its open editor
        self._buffered: list[SessionChange] = []  # changes seen while the snapshot loads
        self._loading = True
        self._registration: asyncio.Future[None] | None = None
        self._stopped = False  # the client disconnected

    def _compose(self, position: int) -> None:
        """The card composer of quadrant `position`, marked content-N and submit-N.

        Submitting stays off until the cards are loaded.
        """
        content = ui.input("New card").mark(f"content-{position}")
        button = ui.button("Add card", on_click=lambda: self._submit(position, content))
        button.mark(f"submit-{position}").disable()
        self._submit_buttons.append(button)
        content.on("keydown.enter", lambda: self._submit(position, content))

    async def _submit(self, position: int, composer: ui.input) -> None:
        """Insert the composed card at the end of the quadrant and show it once confirmed."""
        name, content = self._entered(composer)
        if not self._submittable(name, content):
            ui.notify("Enter a display name and some text", type="warning")
            return
        card = await self._insert(self._quadrant_ids[position], content, name)
        if card is None:
            return
        composer.value = ""
        # The push of this same insert may have arrived already; cards are keyed by id.
        for affected in self._fold(asdict(card)):
            self._render(affected)

    async def _insert(self, quadrant_id: str, content: str, name: str) -> Card | None:
        """The confirmed card, or None (after showing an error) if it could not be added."""
        try:
            return await asyncio.to_thread(
                cards.insert_card,
                self._session_id,
                quadrant_id,
                self._next_index(quadrant_id),
                content,
                name,
            )
        except cards.CardError:
            logger.exception("Card could not be added to session %s", self._session_id)
            ui.notify("Card could not be added", type="negative")
            return None

    async def _save_edit(self, card_id: str) -> None:
        """Replace the card's content with its draft and show the confirmed card.

        Only the content is sent: authorship and creation time stay the card's own.
        """
        content = self._drafts.get(card_id, "")
        if not content.strip():
            ui.notify("Enter some text", type="warning")
            return
        try:
            card = await asyncio.to_thread(cards.update_card, self._session_id, card_id, content)
        except cards.CardError:
            logger.exception("Card %s could not be updated", card_id)
            ui.notify("Card could not be updated", type="negative")
            return
        self._drafts.pop(card_id, None)
        # The push of this same update may have arrived already; stale ones are ignored.
        affected = self._fold(asdict(card)) or {card.quadrant_id}
        for quadrant_id in affected:
            self._render(quadrant_id)

    def _ask_delete(self, card: Card) -> None:
        """Open a confirmation dialog, marked confirm-delete-ID and cancel-delete-ID.

        The dialog belongs to the page, not to the card, so a re-render of the card's
        quadrant does not discard it. Its text names no card content.
        """
        with self._client, ui.dialog() as dialog, ui.card():
            ui.label("Delete this card?")
            confirm = ui.button("Delete", on_click=lambda: self._confirm_delete(card.id, dialog))
            confirm.mark(f"confirm-delete-{card.id}")
            ui.button("Cancel", on_click=lambda: self._dismiss(dialog)).mark(
                f"cancel-delete-{card.id}"
            )
        dialog.open()

    @staticmethod
    def _dismiss(dialog: ui.dialog) -> None:
        dialog.close()
        dialog.delete()

    async def _confirm_delete(self, card_id: str, dialog: ui.dialog) -> None:
        """Soft-delete the card; every connected canvas, this one included, drops it.

        The row stays in the database. The removal reaches the other canvases through
        the Realtime push of the committed update.
        """
        self._dismiss(dialog)
        try:
            card = await asyncio.to_thread(cards.soft_delete_card, self._session_id, card_id)
        except cards.CardError:
            logger.exception("Card %s could not be deleted", card_id)
            ui.notify("Card could not be deleted", type="negative")
            return
        # The push of this same deletion may have arrived already; the fold is idempotent.
        for quadrant_id in self._fold(asdict(card)):
            self._render(quadrant_id)

    def _open_editor(self, card: Card) -> None:
        self._drafts[card.id] = card.content
        self._render(card.quadrant_id)

    def _close_editor(self, card: Card) -> None:
        self._drafts.pop(card.id, None)
        self._render(card.quadrant_id)

    def _editor(self, card: Card) -> None:
        """The open editor of `card`, marked edit-content-ID and save-ID."""
        draft = ui.input("Edit card", value=self._drafts[card.id])
        draft.mark(f"edit-content-{card.id}")
        draft.on_value_change(lambda event: self._drafts.__setitem__(card.id, event.value or ""))
        draft.on("keydown.enter", lambda: self._save_edit(card.id))
        ui.button("Save", on_click=lambda: self._save_edit(card.id)).mark(f"save-{card.id}")
        ui.button("Cancel", on_click=lambda: self._close_editor(card)).mark(f"cancel-{card.id}")

    def _entered(self, composer: ui.input) -> tuple[str, str]:
        """The trimmed display name and the raw text currently typed."""
        return str(self._display_name.value or "").strip(), str(composer.value or "")

    def _submittable(self, name: str, content: str) -> bool:
        """True once the cards are loaded and neither the name nor the text is blank."""
        return not self._loading and bool(name) and bool(content.strip())

    def _next_index(self, quadrant_id: str) -> int:
        """The index after the last shown card of the quadrant."""
        indexes = (card.index for card in self._cards.values() if card.quadrant_id == quadrant_id)
        return 1 + max(indexes, default=-1)

    async def start(self) -> None:
        """Register for pushes, then load the active cards and show them."""
        await self._client.connected()
        snapshot = await self._register_and_load()
        if snapshot is None or self._stopped:
            return
        self._show(snapshot)

    def _show(self, snapshot: list[Card]) -> None:
        """Take the snapshot, replay the changes buffered meanwhile, and render."""
        self._cards = {card.id: card for card in snapshot}
        self._loading = False
        for change in self._buffered:
            self._fold(change.record)
        self._buffered = []
        with self._client:
            for button in self._submit_buttons:
                button.enable()
            for quadrant_id in self._quadrant_ids:
                self._render(quadrant_id)

    async def _register_and_load(self) -> list[Card] | None:
        """Subscribe, then load the snapshot; None (after showing an error) on failure."""
        # Cleanup comes first: a disconnect while the subscription is becoming ready
        # must still release it once registration settles.
        self._client.on_disconnect(self._stop)
        self._registration = asyncio.ensure_future(
            realtime.register_view(self._session_id, str(self._client.id), self._on_change)
        )
        try:
            await asyncio.shield(self._registration)
            if self._stopped:
                return None
            snapshot, self._quadrant_ids = await self._load()
        except Exception:
            logger.exception("Cards of session %s could not be loaded", self._session_id)
            if self._stopped:
                return None
            with self._client:
                ui.label("Cards could not be loaded").classes("text-negative")
            return None
        return snapshot

    async def _load(self) -> tuple[list[Card], list[str]]:
        snapshot = await asyncio.to_thread(cards.list_cards, self._session_id)
        quadrant_ids = await asyncio.to_thread(_load_quadrant_ids, self._session_id)
        return snapshot, quadrant_ids

    async def _stop(self) -> None:
        """On disconnect: wait for any registration in flight, then release the view."""
        self._stopped = True
        if self._registration is not None:
            await asyncio.wait([self._registration])  # settles, never raises
        await realtime.unregister_view(self._session_id, str(self._client.id))

    def _on_change(self, change: SessionChange) -> None:
        if change.table != "cards":
            return
        if self._loading:
            self._buffered.append(change)
            return
        with self._client:
            for quadrant_id in self._fold(change.record):
                self._render(quadrant_id)

    def _fold(self, record: dict) -> set[str]:
        """Fold one card record into the cards; return the quadrants it affects."""
        current = self._cards.get(record.get("id", ""))
        card = _merged(current, record)
        if card is None:
            return set()
        if card.deleted_at is None:
            self._cards[card.id] = card
        else:
            self._cards.pop(card.id, None)
            self._drafts.pop(card.id, None)
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
                self._card_view(card)

    def _card_view(self, card: Card) -> None:
        """One card: its content or open editor, its creator and its creation time."""
        with ui.card().tight().classes("p-2"):
            if card.id in self._drafts:
                self._editor(card)
            else:
                ui.label(card.content)
                ui.button("Edit", on_click=lambda: self._open_editor(card)).mark(f"edit-{card.id}")
                ui.button("Delete", on_click=lambda: self._ask_delete(card)).mark(
                    f"delete-{card.id}"
                )
            ui.label(card.creator_display_name).classes("text-caption")
            created = _instant(card.created_at).strftime("%Y-%m-%d %H:%M:%S %Z")
            ui.badge(created, color="grey-7")
