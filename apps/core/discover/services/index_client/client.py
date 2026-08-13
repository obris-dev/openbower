"""The data-service client bound to an app session: callers name the
OPERATION, not the credential. The session binding is one construction
(the auth flow owns token attachment and refresh-retry); each method is a
plain client-scoped transport call. Clients are short-lived by design,
one per operation, matching the request-scoped views that use them."""

from __future__ import annotations

from typing import Any

from openbower_schema import LookalikeListResponse

from . import transport


class SessionIndexClient:
    def __init__(self, session) -> None:
        self._session = session

    def lookalikes(self, *, payload: dict[str, Any]) -> LookalikeListResponse:
        with transport.client_for(self._session) as client:
            return transport.lookalikes(client, payload=payload)

    def run_status(self, *, run_id: str, limit: int | None = None) -> LookalikeListResponse:
        with transport.client_for(self._session) as client:
            return transport.run_status(client, run_id=run_id, limit=limit)

    def cancel_run(self, *, run_id: str) -> LookalikeListResponse:
        with transport.client_for(self._session) as client:
            return transport.cancel_run(client, run_id=run_id)


class IndexClientService:
    @staticmethod
    def for_session(session) -> SessionIndexClient:
        """A client acting as the session's user (the discover views' entry
        point: `IndexClientService.for_session(request.user.session)`)."""
        return SessionIndexClient(session)
