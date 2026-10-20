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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime

from nicegui import Client, ui

from quadra import cards, realtime
from quadra.cards import Card
from quadra.realtime import SessionChange
from quadra.sessions import Session, close_session, get_client, get_session_status

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


@dataclass(eq=False)
class _Request:
    """A card mutation request, identified by itself, and what its response said.

    The outcome comes only from the mutation's own response: "committed" if it
    succeeded, "rejected" if the database refused it because the session was closed.
    It stays None while the response is awaited, or if it failed some other way.
    `base` is the card as this view held it when the request started (None for an insert),
    so that a snapshot still showing that very version does not reflect the request.
    `intended` holds the card fields the request asks for (content of an edit, placement
    of a move), so a snapshot is compared with the requested result, not just with `base`.
    """

    kind: str  # "insert", "edit", "move" or "delete"
    card_id: str
    base: Card | None = None
    intended: dict[str, object] = field(default_factory=dict)
    outcome: str | None = None


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
        self._close_button = ui.button("Close session", on_click=self._close)
        self._close_button.mark("close-session")
        self._closed_label = ui.label("Session closed").classes("text-h6 text-negative")
        self._closed_label.mark("session-closed").set_visibility(False)
        self._failure_label = ui.label("Session could not be closed").classes("text-negative")
        self._failure_label.mark("closure-failed").set_visibility(False)
        self._reload_failure = ui.row().mark("reload-failed")
        with self._reload_failure:
            ui.label("Closed session cards could not be reloaded").classes("text-negative")
            ui.button("Retry", on_click=self._retry_reconcile).mark("retry-reload")
        self._reload_failure.set_visibility(False)
        self._unsaved_label = ui.label(
            "Your pending change was not saved because the session closed"
        ).classes("text-negative")
        self._unsaved_label.mark("change-not-saved").set_visibility(False)
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
        # A closed state was received (or the session was loaded closed); it is never undone.
        self._closed = session.status == "closed"
        self._closing = False  # a closure request is unresolved: editing is suspended
        # Responses of requests started before the latest bump are stale: closure bumps it,
        # so no older mutation result or load can change what closure reconciled.
        self._generation = 0
        self._reconciliation: asyncio.Task[None] | None = None
        self._reconciled = False  # the closed cards were loaded and shown
        self._requests: list[_Request] = []  # mutation requests awaiting their response
        # Requests unresolved when closure was first seen; kept until a reconciliation
        # succeeds, so a retried reload still judges them after their responses arrived.
        self._closure_requests: list[_Request] = []
        if self._closed:
            self._show_closed()

    @property
    def _editable(self) -> bool:
        return not (self._closed or self._closing)

    def _is_current(self, generation: int) -> bool:
        """True while no closure has invalidated the requests of `generation`."""
        return generation == self._generation

    def _is_live(self, generation: int) -> bool:
        """True while `generation` is current and the client is still connected."""
        return self._is_current(generation) and not self._stopped

    def _show_reload_failure(self, generation: int) -> None:
        """Offer a retry when the closed session's cards could not be reloaded."""
        if self._is_live(generation):
            with self._client:
                self._reload_failure.set_visibility(True)

    async def _close(self) -> None:
        """Ask the session service to close the session, then show it as closed.

        Any participant may do this: no credentials or role are checked. The control is
        not the enforcement; the database is. Editing is suspended while the request is
        unresolved; if it fails, the lifecycle state is read before editing is restored.
        """
        self._closing = True
        self._close_button.disable()
        self._failure_label.set_visibility(False)
        self._refresh_editing()
        try:
            status = await asyncio.to_thread(close_session, self._session_id)
        except Exception:
            logger.exception("Session %s could not be closed", self._session_id)
            status = None
        if status == "closed":
            self._set_closed()
            return
        await self._recover_from_failed_close()

    async def _recover_from_failed_close(self) -> None:
        """Report the failure, then let the authoritative state decide about editing.

        Editing returns only if that state is open and no closed state was received
        meanwhile; if the state cannot be read or is closed, the canvas stays frozen.
        """
        if not self._closed:
            self._failure_label.set_visibility(True)
        ui.notify("Session could not be closed", type="negative")
        status = await self._read_status()
        if status == "closed":
            self._set_closed()
        elif status == "open" and not self._closed:
            self._closing = False
            self._close_button.enable()
            self._refresh_editing()

    async def _read_status(self) -> str | None:
        """The session's authoritative lifecycle state, or None if it cannot be read."""
        try:
            return await asyncio.to_thread(get_session_status, self._session_id)
        except Exception:
            logger.exception("State of session %s could not be read", self._session_id)
            return None

    def _set_closed(self) -> None:
        """Latch the closed state: read-only for good, whatever happens to a request.

        The first sight of closure also invalidates every older response generation,
        drops the optimistic overlays and reloads the authoritative cards.
        """
        first = not self._closed
        if first:
            self._closure_requests = list(self._requests)
        self._closed = True
        self._closing = False
        self._show_closed()
        if first:
            self._start_reconciliation()
        self._refresh_editing()

    def _show_closed(self) -> None:
        """Offer no way to close again, and say that the session is closed."""
        self._close_button.set_visibility(False)
        self._failure_label.set_visibility(False)
        self._closed_label.set_visibility(True)

    def _start_reconciliation(self) -> None:
        """Invalidate older responses, drop optimistic overlays, reload the cards."""
        self._generation += 1
        self._pending.clear()
        self._moving.clear()
        self._drafts.clear()
        self._reconciled = False
        self._reload_failure.set_visibility(False)
        self._reconciliation = asyncio.ensure_future(
            self._reconcile(self._generation)
        )

    async def _retry_reconcile(self) -> None:
        self._start_reconciliation()
        self._refresh_editing()

    async def _reconcile(self, generation: int) -> None:
        """Replace the cards with the complete authoritative snapshot, unless superseded.

        If the load fails the view stays closed and read-only, with a retry offered; an
        incomplete result is never shown as the finalized one. A request unresolved at
        closure is reported unsaved once the snapshot is shown and does not reflect it,
        or its own response says the database rejected it.
        """
        try:
            snapshot, quadrant_ids = await self._load()
        except Exception:
            logger.exception("Closed session %s could not be reloaded", self._session_id)
            self._show_reload_failure(generation)
            return
        if not self._is_live(generation):
            return
        self._quadrant_ids = quadrant_ids
        self._buffered = []
        self._show(snapshot)
        self._reconciled = True
        self._sync_unsaved()

    def _sync_unsaved(self) -> None:
        """Show the 'not saved' notice while a change pending at closure is not reflected.

        Only after the authoritative cards are shown. The notice is announced once, and
        removed when the delayed success response of the last unreflected change arrives.
        """
        if self._reconciled and not self._stopped:
            self._set_unsaved(any(self._not_saved(r) for r in self._closure_requests))

    def _set_unsaved(self, due: bool) -> None:
        """Show or hide the notice; announce it only when it newly appears."""
        if due == self._unsaved_label.visible:
            return
        with self._client:
            self._unsaved_label.set_visibility(due)
            if due:
                ui.notify(self._unsaved_label.text, type="warning")

    def _not_saved(self, request: _Request) -> bool:
        """True if the database rejected `request`, or the shown cards do not reflect it.

        Called once the authoritative closed cards are shown, so it need not wait for the
        delayed response. A response that says the request committed always clears it.
        """
        if request.outcome is not None:
            return request.outcome == "rejected"
        return self._unreflected(request)

    def _unreflected(self, request: _Request) -> bool:
        """True if the shown authoritative cards do not reflect `request`.

        An edit or move is reflected only if the card changed since the request started
        and now holds the requested result, so another participant's update to the card
        does not stand in for it. Known limit: a change later overwritten or deleted by
        another participant cannot be told apart from an unsaved one; its delayed
        success response removes the notice.
        """
        card = self._cards.get(request.card_id)
        if request.kind == "delete":
            return card is not None
        if card is None:
            return True
        return request.kind != "insert" and self._not_updated(request, card)

    @staticmethod
    def _not_updated(request: _Request, card: Card) -> bool:
        """True if `card` is still the version the request started from, or lacks its result."""
        if request.base is not None and card.updated_at == request.base.updated_at:
            return True
        return any(getattr(card, name) != value for name, value in request.intended.items())

    async def _send(self, request: _Request, function, *args, **kwargs):
        """Run the card mutation `function` for `request` and record how it ended."""
        try:
            result = await asyncio.to_thread(function, self._session_id, *args, **kwargs)
        except cards.CardSessionClosedError:
            request.outcome = "rejected"
            self._sync_unsaved()
            raise
        request.outcome = "committed"
        self._sync_unsaved()
        return result

    @contextmanager
    def _unresolved(self, request: _Request) -> Iterator[None]:
        """Keep `request` on record while its response is awaited."""
        self._requests.append(request)
        try:
            yield
        finally:
            self._requests.remove(request)

    def _refresh_editing(self) -> None:
        """Make the composers and the cards' controls follow whether editing is allowed."""
        for button in self._submit_buttons:
            button.set_enabled(self._editable and not self._loading)
        self._render_all(*self._quadrant_ids)

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
        if not self._editable:
            return
        if not self._submittable(name, content):
            ui.notify("Enter a display name and some text", type="warning")
            return
        generation = self._generation
        quadrant_id = self._quadrant_ids[position]
        pending = _PendingCard(str(uuid.uuid4()), quadrant_id, content, name)
        index = self._next_index(quadrant_id)
        self._pending[pending.id] = pending
        self._render(quadrant_id)
        request = _Request("insert", pending.id)
        with self._unresolved(request):
            card = await self._insert(request, pending, index, generation)
        if not self._is_current(generation):
            return  # closure dropped the overlay and reconciled the cards
        self._pending.pop(pending.id, None)
        if card is None:
            self._render(quadrant_id)
            return
        composer.value = ""
        # The push of this same insert may have arrived already; cards are keyed by id.
        self._show_confirmed(card, quadrant_id)

    async def _insert(
        self, request: _Request, pending: _PendingCard, index: int, generation: int
    ) -> Card | None:
        """The confirmed card, or None (after showing an error) if it could not be added."""
        try:
            return await self._send(
                request,
                cards.insert_card,
                pending.quadrant_id,
                index,
                pending.content,
                pending.creator_display_name,
                card_id=pending.id,
            )
        except cards.CardError:
            logger.exception("Card could not be added to session %s", self._session_id)
            if self._is_current(generation):
                ui.notify("Card could not be added", type="negative")
            return None

    async def _save_edit(self, card_id: str) -> None:
        """Replace the card's content with its draft and show the confirmed card.

        Only the content is sent: authorship and creation time stay the card's own.
        """
        if not self._editable:
            return
        content = self._drafts.get(card_id, "")
        if not content.strip():
            ui.notify("Enter some text", type="warning")
            return
        generation = self._generation
        request = _Request("edit", card_id, self._cards.get(card_id), {"content": content})
        with self._unresolved(request):
            card = await self._update(request, card_id, content, generation)
        if card is None or not self._is_current(generation):
            return
        self._drafts.pop(card_id, None)
        self._show_confirmed(card)

    async def _update(
        self, request: _Request, card_id: str, content: str, generation: int
    ) -> Card | None:
        """The confirmed card, or None (after showing an error) if it could not be updated."""
        try:
            return await self._send(request, cards.update_card, card_id, content)
        except cards.CardError:
            logger.exception("Card %s could not be updated", card_id)
            if self._is_current(generation):
                ui.notify("Card could not be updated", type="negative")
            return None

    async def _move(self, card_id: str, position: int | None, index: float | None) -> None:
        """Move the card to quadrant `position` at `index` and show the confirmed placement.

        The card shows as pending in the target quadrant at once, while its original
        quadrant and index are remembered. Only the placement is sent: authorship and
        creation time stay the card's own.
        """
        move = self._start_move(card_id, position, index) if self._editable else None
        if move is None:
            return
        generation = self._generation
        request = _Request(
            "move",
            card_id,
            self._cards.get(card_id),
            {"quadrant_id": move.quadrant_id, "index": move.index},
        )
        with self._unresolved(request):
            card = await self._relocate(request, card_id, move.quadrant_id, move.index, generation)
        if not self._is_current(generation):
            return
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

    def _clear_optimistic(self, card_id: str) -> set[str]:
        """Drop the card's optimistic insert and move; the quadrants they showed it in."""
        shown: set[str] = set()
        pending = self._pending.pop(card_id, None)
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

    async def _relocate(
        self, request: _Request, card_id: str, quadrant_id: str, index: int, generation: int
    ) -> Card | None:
        """The confirmed card, or None (after showing an error) if it could not be moved."""
        try:
            return await self._send(request, cards.relocate_card, card_id, quadrant_id, index)
        except cards.CardError:
            logger.exception("Card %s could not be moved", card_id)
            if self._is_current(generation):
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
        if not self._editable:
            return
        generation = self._generation
        try:
            request = _Request("delete", card_id, self._cards.get(card_id))
            with self._unresolved(request):
                card = await self._send(request, cards.soft_delete_card, card_id)
        except cards.CardError:
            logger.exception("Card %s could not be deleted", card_id)
            if self._is_current(generation):
                ui.notify("Card could not be deleted", type="negative")
            return
        if not self._is_current(generation):
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
        generation = self._generation
        loaded = await self._register_and_load()
        if loaded is None or self._stopped or not self._is_current(generation):
            return  # a closure meanwhile reloads the cards and quadrants itself
        snapshot, self._quadrant_ids = loaded
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
                button.set_enabled(self._editable)
            for quadrant_id in self._quadrant_ids:
                self._render(quadrant_id)

    async def _register_and_load(self) -> tuple[list[Card], list[str]] | None:
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
            if await self._reconcile_lifecycle():
                return None  # closed: the reconciliation loads the finalized cards
            loaded = await self._load()
        except Exception:
            logger.exception("Cards of session %s could not be loaded", self._session_id)
            if self._stopped:
                return None
            with self._client:
                ui.label("Cards could not be loaded").classes("text-negative")
            return None
        return loaded

    async def _reconcile_lifecycle(self) -> bool:
        """After registration, take the authoritative lifecycle state; True if closed.

        Closure is only ever latched, never undone: an open answer, or one that cannot
        be read, leaves a closure received by push in the meantime in place. A closed
        session is loaded by the reconciliation, whose failure offers a retry.
        """
        status = await self._read_status()
        if self._stopped:
            return False
        with self._client:
            if status == "closed":
                self._set_closed()
            if self._closed and self._reconciliation is None:
                self._start_reconciliation()  # loaded closed: nothing reconciled it yet
                self._refresh_editing()
        return self._closed

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
        if change.table == "workspaces":
            self._on_session_change(change.record)
            return
        if change.table != "cards":
            return
        if self._loading:
            self._buffered.append(change)
            return
        with self._client:
            for quadrant_id in self._fold(change.record):
                self._render(quadrant_id)

    def _on_session_change(self, record: dict) -> None:
        """A closed state received by push latches at once, whatever a request is doing."""
        if record.get("status") == "closed":
            with self._client:
                self._set_closed()

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
        # card, such as the push of its own insert, and any newer change clear it.
        if current is None or _instant(card.updated_at) > _instant(current.updated_at):
            self._clear_optimistic(card.id)
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

    def _card_body(self, card: Card, interactive: bool) -> None:
        """The card's open editor, or its content with controls only if `interactive`."""
        if interactive and card.id in self._drafts:
            self._editor(card)
            return
        ui.label(card.content)
        if interactive:
            ui.button("Edit", on_click=lambda: self._open_editor(card)).mark(f"edit-{card.id}")
            ui.button("Delete", on_click=lambda: self._ask_delete(card)).mark(f"delete-{card.id}")
            self._mover(card)

    def _card_view(self, card: Card, pending: bool = False) -> None:
        """One card: its content or open editor, its creator and its creation time.

        A card whose move is unresolved is dimmed and labelled, marked pending-ID, and
        offers no controls.
        """
        with ui.card().tight().classes("p-2 opacity-50" if pending else "p-2") as view:
            if pending:
                view.mark(f"pending-{card.id}")
            self._card_body(card, interactive=self._editable and not pending)
            ui.label(card.creator_display_name).classes("text-caption")
            created = _instant(card.created_at).strftime("%Y-%m-%d %H:%M:%S %Z")
            ui.badge(created, color="grey-7")
            if pending:
                ui.label("Pending").classes("text-caption text-orange")
