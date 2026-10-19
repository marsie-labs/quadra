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
import uuid
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime

from nicegui import Client, ui

from quadra import cards, realtime
from quadra.cards import Card
from quadra.realtime import SessionChange
from quadra.sessions import Session, get_client

logger = logging.getLogger(__name__)

_CARD_FIELDS = frozenset(field.name for field in fields(Card))


@dataclass(frozen=True)
class _PendingCard:
    """A card whose insert request is unresolved; it lives only in this view's memory."""

    id: str
    quadrant_id: str
    content: str
    creator_display_name: str


@dataclass(frozen=True)
class _PendingMove:
    """A card whose relocation request is unresolved; it lives only in this view's memory.

    It remembers where the card came from, so a rejected move can put it back.
    """

    card_id: str
    quadrant_id: str
    index: int
    origin_quadrant_id: str
    origin_index: int


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


def _merged(
    current: Card | None, record: dict, deleted: datetime | None = None
) -> Card | None:
    """The card a change record yields on top of `current`; None if incomplete or stale.

    `deleted` is the retained deletion version of the card, if this view saw it deleted.
    """
    values = asdict(current) if current else {}
    values |= {name: record[name] for name in _CARD_FIELDS & record.keys()}
    if set(values) != set(_CARD_FIELDS):
        logger.warning("Ignoring incomplete card change %s", record)
        return None
    card = Card(**values)
    return None if _is_stale(card, current, deleted) else card


def _deletion_version(card: Card) -> datetime:
    """The instant a deleted card's deletion became current."""
    return max(_instant(card.updated_at), _instant(card.deleted_at or card.updated_at))


def _is_stale(card: Card, current: Card | None, deleted: datetime | None = None) -> bool:
    """True for a notification of a change older than what is already shown or deleted."""
    version = _instant(card.updated_at)
    if current is not None and version < _instant(current.updated_at):
        return True
    return deleted is not None and version < deleted


class Canvas:
    """The quadrant grid of one session, built in the current NiceGUI slot."""

    def __init__(self, session: Session) -> None:
        self._session_id = session.session_id
        self._quadrant_names = dict(enumerate(session.quadrants))
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
        # card id -> version of its deletion seen by this view, so that a delayed older
        # response cannot restore it; transient, Supabase stays authoritative.
        self._deleted: dict[str, datetime] = {}
        self._pending: dict[str, _PendingCard] = {}  # card id -> insert awaiting confirmation
        self._moving: dict[str, _PendingMove] = {}  # card id -> move awaiting confirmation
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
        """Insert the composed card at the end of the quadrant.

        The card shows as pending at once, under the id the insert will use, and is
        replaced by the confirmed card.
        """
        name, content = self._entered(composer)
        if not self._submittable(name, content):
            ui.notify("Enter a display name and some text", type="warning")
            return
        quadrant_id = self._quadrant_ids[position]
        pending = _PendingCard(str(uuid.uuid4()), quadrant_id, content, name)
        index = self._next_index(quadrant_id)
        self._pending[pending.id] = pending
        self._render(quadrant_id)
        card = await self._insert(pending, index)
        self._pending.pop(pending.id, None)
        if card is None:
            self._render(quadrant_id)
            return
        composer.value = ""
        # The push of this same insert may have arrived already; cards are keyed by id.
        self._show_confirmed(card, quadrant_id)

    async def _insert(self, pending: _PendingCard, index: int) -> Card | None:
        """The confirmed card, or None (after showing an error) if it could not be added."""
        try:
            return await asyncio.to_thread(
                cards.insert_card,
                self._session_id,
                pending.quadrant_id,
                index,
                pending.content,
                pending.creator_display_name,
                card_id=pending.id,
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
        self._show_confirmed(card)

    async def _move(self, card_id: str, position: int | None, index: float | None) -> None:
        """Move the card to quadrant `position` at `index` and show the confirmed placement.

        The card shows as pending in the target quadrant at once, while its original
        quadrant and index are remembered. Only the placement is sent: authorship and
        creation time stay the card's own.
        """
        move = self._start_move(card_id, position, index)
        if move is None:
            return
        card = await self._relocate(card_id, move.quadrant_id, move.index)
        shown = self._clear_move(move)
        if card is None:
            self._render_all(*shown, *self._current_quadrants(card_id))
            return
        self._show_confirmed(card, *shown)

    def _start_move(
        self, card_id: str, position: int | None, index: float | None
    ) -> _PendingMove | None:
        """Show the card as pending in its target; None if there is nothing to move."""
        if position not in self._quadrant_names or index is None:
            ui.notify("Choose a quadrant and an index", type="warning")
            return None
        current = self._cards.get(card_id)
        if current is None or card_id in self._moving:
            return None
        move = _PendingMove(
            card_id, self._quadrant_ids[position], int(index), current.quadrant_id, current.index
        )
        self._moving[card_id] = move
        self._render_all(move.origin_quadrant_id, move.quadrant_id)
        return move

    def _render_all(self, *quadrant_ids: str) -> None:
        for quadrant_id in quadrant_ids:
            self._render(quadrant_id)

    def _clear_move(self, move: _PendingMove) -> set[str]:
        """Drop `move` if it is still the card's optimistic move; the quadrants it showed."""
        if self._moving.get(move.card_id) is move:
            del self._moving[move.card_id]
        return {move.quadrant_id, move.origin_quadrant_id}

    def _clear_optimistic(self, card_id: str, keep_insert: bool = False) -> set[str]:
        """Drop the card's optimistic insert and move; the quadrants they showed it in.

        `keep_insert` leaves the insert for its own response to resolve.
        """
        shown: set[str] = set()
        pending = None if keep_insert else self._pending.pop(card_id, None)
        if pending is not None:
            shown.add(pending.quadrant_id)
        move = self._moving.pop(card_id, None)
        if move is not None:
            shown |= {move.quadrant_id, move.origin_quadrant_id}
        return shown

    def _current_quadrants(self, card_id: str) -> set[str]:
        """The quadrant the authoritative cards place the card in; none if it is absent."""
        card = self._cards.get(card_id)
        return {card.quadrant_id} if card else set()

    def _show_confirmed(self, card: Card, *shown: str) -> None:
        """Show the authoritative state of a card the database confirmed.

        `shown` are the quadrants its cleared optimistic entries showed it in. A response
        older than what the view holds is discarded by the fold, but the card's current
        quadrant, `shown` and the response's own are rendered all the same.
        """
        # The push of this same change may have arrived already; stale ones are ignored.
        affected = self._fold(asdict(card)) | set(shown) | {card.quadrant_id}
        for quadrant_id in affected | self._current_quadrants(card.id):
            self._render(quadrant_id)

    async def _relocate(self, card_id: str, quadrant_id: str, index: int) -> Card | None:
        """The confirmed card, or None (after showing an error) if it could not be moved."""
        try:
            return await asyncio.to_thread(
                cards.relocate_card, self._session_id, card_id, quadrant_id, index
            )
        except cards.CardError:
            logger.exception("Card %s could not be moved", card_id)
            ui.notify("Card could not be moved", type="negative")
            return None

    def _mover(self, card: Card) -> None:
        """Controls to move `card`, marked move-quadrant-ID, move-index-ID and move-ID."""
        quadrant = ui.select(self._quadrant_names, label="Move to")
        quadrant.mark(f"move-quadrant-{card.id}")
        index = ui.number("At index", value=card.index, precision=0, min=0)
        index.mark(f"move-index-{card.id}")
        ui.button(
            "Move", on_click=lambda: self._move(card.id, quadrant.value, index.value)
        ).mark(f"move-{card.id}")

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
        self._show_confirmed(card, *self._clear_optimistic(card.id))

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
        card = _merged(current, record, self._deleted.get(record.get("id", "")))
        if card is None:
            return set()
        # Authoritative state replaces every optimistic entry of the card (insert, move,
        # edit), and each quadrant that showed the card is rerendered by the caller.
        affected = {card.quadrant_id} | self._shown_in(card.id, current)
        # A replay of what is already held (a response after its own push) is no news,
        # and must not clear what the participant started since. The first sight of a
        # card is the push of its own insert: that insert's response resolves its
        # pending entry (it is no longer shown once the card is in the cards), while
        # any newer change clears it.
        if current is None or _instant(card.updated_at) > _instant(current.updated_at):
            self._clear_optimistic(card.id, keep_insert=current is None and not card.deleted_at)
            self._drafts.pop(card.id, None)
        self._store(card)
        return affected

    def _store(self, card: Card) -> None:
        """Keep an active card in the cards; drop a deleted one, retaining its version."""
        if card.deleted_at is None:
            self._cards[card.id] = card
            return
        self._cards.pop(card.id, None)
        # Never older than a version retained before: older records are stale.
        self._deleted[card.id] = _deletion_version(card)

    def _shown_in(self, card_id: str, current: Card | None) -> set[str]:
        """The quadrants showing the card now: its own, its pending insert, its move."""
        shown = {current.quadrant_id} if current else set()
        pending = self._pending.get(card_id)
        if pending is not None:
            shown.add(pending.quadrant_id)
        move = self._moving.get(card_id)
        if move is not None:
            shown |= {move.quadrant_id, move.origin_quadrant_id}
        return shown

    def _render(self, quadrant_id: str) -> None:
        """Rebuild one quadrant's cards by index, then card id."""
        if quadrant_id not in self._quadrant_ids:
            return
        column = self._columns[self._quadrant_ids.index(quadrant_id)]
        column.clear()
        with column:
            for card in sorted(self._placed(quadrant_id), key=lambda card: (card.index, card.id)):
                self._card_view(card, pending=card.id in self._moving)
            for pending in self._unconfirmed(quadrant_id):
                self._pending_view(pending)

    def _placed(self, quadrant_id: str) -> list[Card]:
        """The cards shown in the quadrant: those in it, with moving ones at their target."""
        return self._resting(quadrant_id) + self._arriving(quadrant_id)

    def _resting(self, quadrant_id: str) -> list[Card]:
        return [
            card
            for card in self._cards.values()
            if card.id not in self._moving and card.quadrant_id == quadrant_id
        ]

    def _arriving(self, quadrant_id: str) -> list[Card]:
        """Moving cards targeting the quadrant, placed by the index they were asked for."""
        return [
            replace(self._cards[move.card_id], quadrant_id=quadrant_id, index=move.index)
            for move in self._moving.values()
            if move.quadrant_id == quadrant_id and move.card_id in self._cards
        ]

    def _unconfirmed(self, quadrant_id: str) -> list[_PendingCard]:
        """Pending cards of the quadrant that are neither confirmed nor deleted.

        A push may have confirmed one before its insert returned, or deleted it: the
        retained deletion version, not mere absence from the cards, rules it out.
        """
        return [
            pending
            for pending in self._pending.values()
            if pending.quadrant_id == quadrant_id
            and pending.id not in self._cards
            and pending.id not in self._deleted
        ]

    def _pending_view(self, pending: _PendingCard) -> None:
        """An unconfirmed card, dimmed and labelled, marked pending-ID."""
        with ui.card().tight().classes("p-2 opacity-50").mark(f"pending-{pending.id}"):
            ui.label(pending.content)
            ui.label(pending.creator_display_name).classes("text-caption")
            ui.label("Pending").classes("text-caption text-orange")

    def _card_view(self, card: Card, pending: bool = False) -> None:
        """One card: its content or open editor, its creator and its creation time.

        A card whose move is unresolved is dimmed and labelled, marked pending-ID, and
        offers no controls.
        """
        with ui.card().tight().classes("p-2 opacity-50" if pending else "p-2") as view:
            if pending:
                view.mark(f"pending-{card.id}")
                ui.label(card.content)
            elif card.id in self._drafts:
                self._editor(card)
            else:
                ui.label(card.content)
                ui.button("Edit", on_click=lambda: self._open_editor(card)).mark(f"edit-{card.id}")
                ui.button("Delete", on_click=lambda: self._ask_delete(card)).mark(
                    f"delete-{card.id}"
                )
                self._mover(card)
            ui.label(card.creator_display_name).classes("text-caption")
            created = _instant(card.created_at).strftime("%Y-%m-%d %H:%M:%S %Z")
            ui.badge(created, color="grey-7")
            if pending:
                ui.label("Pending").classes("text-caption text-orange")
