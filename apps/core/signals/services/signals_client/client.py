"""The signals client bound to an app session: callers name the OPERATION,
not the credential. The same shape as discover's SessionIndexClient: one
construction binds the session (the auth flow owns token attachment and
refresh-retry), and each method is one transport call on a short-lived
client."""

from __future__ import annotations

from typing import Any

from openbower_schema import (
    ListenersPage,
    ListenerVersionsPage,
    ListenerWire,
    MatchesPage,
    PreviewRunWire,
    SignalEventsPage,
    SignalsCatalog,
)

from . import transport


class SessionSignalsClient:
    def __init__(self, session) -> None:
        self._session = session

    def catalog(self) -> SignalsCatalog:
        with transport.client_for(self._session) as client:
            return transport.catalog(client)

    def signal_events(self, *, signal: str, after: str | None = None, limit: int | None = None) -> SignalEventsPage:
        with transport.client_for(self._session) as client:
            return transport.signal_events(client, signal=signal, after=after, limit=limit)

    def listeners(self, *, after: str | None = None, limit: int | None = None) -> ListenersPage:
        with transport.client_for(self._session) as client:
            return transport.listeners(client, after=after, limit=limit)

    def create_listener(self, *, payload: dict[str, Any]) -> ListenerWire:
        with transport.client_for(self._session) as client:
            return transport.create_listener(client, payload=payload)

    def listener(self, *, listener_id: str) -> ListenerWire:
        with transport.client_for(self._session) as client:
            return transport.listener(client, listener_id=listener_id)

    def edit_listener(self, *, listener_id: str, payload: dict[str, Any]) -> ListenerWire:
        with transport.client_for(self._session) as client:
            return transport.edit_listener(client, listener_id=listener_id, payload=payload)

    def delete_listener(self, *, listener_id: str) -> None:
        with transport.client_for(self._session) as client:
            transport.delete_listener(client, listener_id=listener_id)

    def listener_versions(
        self, *, listener_id: str, after: str | None = None, limit: int | None = None
    ) -> ListenerVersionsPage:
        with transport.client_for(self._session) as client:
            return transport.listener_versions(client, listener_id=listener_id, after=after, limit=limit)

    def listener_matches(self, *, listener_id: str, after: str | None = None, limit: int | None = None) -> MatchesPage:
        with transport.client_for(self._session) as client:
            return transport.listener_matches(client, listener_id=listener_id, after=after, limit=limit)

    def create_preview(self, *, payload: dict[str, Any]) -> PreviewRunWire:
        with transport.client_for(self._session) as client:
            return transport.create_preview(client, payload=payload)

    def preview(self, *, preview_id: str) -> PreviewRunWire:
        with transport.client_for(self._session) as client:
            return transport.preview(client, preview_id=preview_id)

    def cancel_preview(self, *, preview_id: str) -> PreviewRunWire:
        with transport.client_for(self._session) as client:
            return transport.cancel_preview(client, preview_id=preview_id)


class SignalsClientService:
    @staticmethod
    def for_session(session) -> SessionSignalsClient:
        """A client acting as the session's user (the entry point for the
        views that will call signals: `SignalsClientService.for_session(request.auth)`)."""
        return SessionSignalsClient(session)
