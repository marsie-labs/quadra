"""Realtime synchronization service: pushes committed session changes to views.

One Supabase Realtime channel per session, shared by every connected view of that
session and carrying its session, quadrant and card changes, runs on supabase-py's asynchronous Realtime client inside the NiceGUI event
loop. Committed changes are pushed by Supabase (no polling) and dispatched in-process
only to the views registered for the affected session.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from realtime import (
    AsyncRealtimeChannel,
    RealtimePostgresChangesListenEvent,
    RealtimeSubscribeStates,
)
from realtime.message import SuccessSystemPayload

from supabase import AsyncClient, acreate_client

logger = logging.getLogger(__name__)

SUBSCRIBE_TIMEOUT = 10.0
UNSUBSCRIBE_TIMEOUT = 5.0


class RealtimeError(RuntimeError):
    """A session subscription could not be established."""


@dataclass(frozen=True)
class SessionChange:
    """A committed INSERT or UPDATE on a session, its quadrants or its cards.

    A card soft-delete arrives as an UPDATE of the card row.
    """

    session_id: str
    table: str
    event: str
    record: dict[str, Any]
    old_record: dict[str, Any]
    commit_timestamp: str


ViewCallback = Callable[[SessionChange], None]


def _to_change(session_id: str, data: dict[str, Any]) -> SessionChange:
    """Build the change a view receives from a Realtime postgres_changes payload."""
    return SessionChange(
        session_id=session_id,
        table=data["table"],
        event=str(getattr(data["type"], "value", data["type"])),
        record=data.get("record") or {},
        old_record=data.get("old_record") or {},
        commit_timestamp=data["commit_timestamp"],
    )


@dataclass
class _SessionSubscription:
    channel: AsyncRealtimeChannel
    views: dict[str, ViewCallback]
    ready: bool = False
    closed: asyncio.Future[None] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class RealtimeBridge:
    """Registry of session subscriptions and the views that share them."""

    def __init__(self) -> None:
        self._client: AsyncClient | None = None
        self._subscriptions: dict[str, _SessionSubscription] = {}
        self._lock = asyncio.Lock()

    async def register_view(
        self, session_id: str, view_id: str, callback: ViewCallback
    ) -> None:
        """Register a view of a session; return once its subscription is ready.

        The first view of a session opens its subscription, later views share it.
        Raises RealtimeError when the subscription cannot be established.
        """
        uuid.UUID(session_id)  # session ids are interpolated into server-side filters
        async with self._lock:
            subscription = self._subscriptions.get(session_id)
            if subscription is None:
                await self._open(session_id, {view_id: callback})
            else:
                subscription.views[view_id] = callback

    async def unregister_view(self, session_id: str, view_id: str) -> bool:
        """Remove a view; release the session's subscription with its last view.

        Returns True when the subscription was released and Supabase acknowledged
        its removal, False when it is kept for another view or was not registered.
        """
        async with self._lock:
            subscription = self._subscriptions.get(session_id)
            if subscription is None:
                return False
            subscription.views.pop(view_id, None)
            if subscription.views:
                return False
            del self._subscriptions[session_id]
            acknowledged = await self._release(subscription)
            await self._close_client_if_idle()
            return acknowledged

    def is_ready(self, session_id: str) -> bool:
        """Whether the session's subscription is established and receiving changes."""
        subscription = self._subscriptions.get(session_id)
        return subscription is not None and subscription.ready

    def view_ids(self, session_id: str) -> frozenset[str]:
        """The views currently registered for a session."""
        subscription = self._subscriptions.get(session_id)
        return frozenset(subscription.views) if subscription else frozenset()

    async def _open(self, session_id: str, views: dict[str, ViewCallback]) -> None:
        """Open and register a subscription whose first views are registered at once."""
        client = await self._get_client()
        try:
            canvas_ids = await self._canvas_ids(client, session_id)
            channel = client.channel(f"session:{session_id}")
            subscription = _SessionSubscription(channel=channel, views=views)
            self._subscriptions[session_id] = subscription  # dispatch needs it at once
            self._bind(subscription, session_id, canvas_ids)
            await self._subscribe(subscription)
        except BaseException:
            self._subscriptions.pop(session_id, None)
            await self._close_client_if_idle()
            raise

    async def _get_client(self) -> AsyncClient:
        if self._client is None:
            self._client = await acreate_client(
                os.environ["SUPABASE_URL"], os.environ["SUPABASE_PUBLISHABLE_KEY"]
            )
        return self._client

    @staticmethod
    async def _canvas_ids(client: AsyncClient, session_id: str) -> list[str]:
        """Quadrants carry no session id, so the session's canvases scope them.

        A single read when the subscription opens, never repeated.
        """
        response = (
            await client.table("canvases")
            .select("id")
            .eq("workspace_id", session_id)
            .execute()
        )
        return [row["id"] for row in response.data]

    def _bind(
        self,
        subscription: _SessionSubscription,
        session_id: str,
        canvas_ids: list[str],
    ) -> None:
        """Listen to the session, quadrant and card sources, scoped server-side."""
        sources = {
            "workspaces": f"id=eq.{session_id}",
            "canvases": f"workspace_id=eq.{session_id}",
            "cards": f"session_id=eq.{session_id}",
        }
        if canvas_ids:
            sources["quadrants"] = f"canvas_id=in.({','.join(canvas_ids)})"
        for table, row_filter in sources.items():
            for event in (
                RealtimePostgresChangesListenEvent.Insert,
                RealtimePostgresChangesListenEvent.Update,
            ):
                subscription.channel.on_postgres_changes(
                    event,
                    partial(self._dispatch, session_id),
                    table=table,
                    schema="public",
                    filter=row_filter,
                )

    def _dispatch(self, session_id: str, payload: dict[str, Any]) -> None:
        """Hand a committed change to every view of its own session."""
        subscription = self._subscriptions.get(session_id)
        if subscription is None:
            return
        change = _to_change(session_id, payload["data"])
        for view_id, callback in list(subscription.views.items()):
            try:
                callback(change)
            except Exception:
                logger.exception("Realtime view %s failed to handle a change", view_id)

    async def _subscribe(self, subscription: _SessionSubscription) -> None:
        """Join the channel and wait until Supabase confirms it is subscribed."""
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        joined = False
        listening = False  # Supabase confirmed its database change stream is attached

        def settle() -> None:
            subscription.ready = joined and listening
            if subscription.ready and not ready.done():
                ready.set_result(None)

        def on_state(state: RealtimeSubscribeStates, error: Exception | None) -> None:
            nonlocal joined
            joined = state == RealtimeSubscribeStates.SUBSCRIBED
            settle()
            if not joined and not ready.done():
                ready.set_exception(
                    RealtimeError(f"Realtime subscription failed: {state} {error or ''}")
                )

        def on_system(payload: SuccessSystemPayload) -> None:
            # The join reply precedes the change stream; a commit in between is lost.
            nonlocal listening
            if payload.extension == "postgres_changes":
                listening = True
                settle()

        subscription.channel.on_system(on_system)
        await subscription.channel.subscribe(on_state)
        try:
            await asyncio.wait_for(ready, SUBSCRIBE_TIMEOUT)
        except BaseException:
            await self._release(subscription)
            raise

    async def _release(self, subscription: _SessionSubscription) -> bool:
        """Leave the channel; True once Supabase acknowledged its removal."""
        subscription.ready = False
        channel = subscription.channel
        close = channel.on_close

        def on_close() -> None:
            close()
            if not subscription.closed.done():
                subscription.closed.set_result(None)

        # The leave acknowledgement closes the channel through on_close.
        channel.on_close = on_close  # type: ignore[method-assign]
        await channel.unsubscribe()
        try:
            await asyncio.wait_for(subscription.closed, UNSUBSCRIBE_TIMEOUT)
        except TimeoutError:
            logger.warning("Realtime did not acknowledge leaving %s", channel.topic)
            return False
        return True

    async def _close_client_if_idle(self) -> None:
        """Drop the Realtime socket when no session needs a subscription."""
        if self._subscriptions or self._client is None:
            return
        client, self._client = self._client, None
        await client.realtime.close()
        await client.postgrest.aclose()


_bridge = RealtimeBridge()
register_view = _bridge.register_view
unregister_view = _bridge.unregister_view
is_ready = _bridge.is_ready
view_ids = _bridge.view_ids
