"""Stand-ins for the two upstream services a log's lifecycle calls, at their
httpx seams: the signals service (listeners) and the identity service (the
sync key's mint). Each records what it was sent and answers as the real
one does; a test overrides a route's next answer to stage a failure."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx

from auth_client.services.oauth import TokenResponse
from auth_client.services.sessions import AppSessionService
from openbower_schema import AuthUser

USER = AuthUser(id="01JQ" + "A" * 22, email="user@example.com", account_id="01JQ" + "B" * 22)
AT = "2026-11-09T16:29:02.062784+00:00"


def session_for(user: AuthUser = USER):
    tokens = TokenResponse(access_token="access-abc", refresh_token="refresh-abc", expires_in=3600)
    session, _raw = AppSessionService.Global.create(user=user, tokens=tokens)
    return session


def listener_body(listener_id: str, *, label: str, filter: dict, prompt: str = "", min_confidence: float = 0.7) -> dict:
    return {
        "id": listener_id,
        "label": label,
        "signal": "funding",
        "user_id": USER.id,
        "status": "active",
        "error": None,
        "version": {
            "id": "01JQ" + "V" * 22,
            "filter": filter,
            "prompt": prompt,
            "min_confidence": min_confidence,
            "user_id": USER.id,
            "created_at": AT,
        },
        "created_at": AT,
        "updated_at": AT,
    }


class FakeSignals:
    """The signals service's listener routes over an in-memory store."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.listeners: dict[str, dict] = {}
        self.next: dict[str, httpx.Response] = {}
        # Fixed answers by (method, path), for the routes passed through as
        # they are (the catalog, events, previews).
        self.fixed: dict[tuple[str, str], httpx.Response] = {}
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        staged = self.next.pop(request.method, None)
        if staged is not None:
            return staged
        fixed = self.fixed.get((request.method, path))
        if fixed is not None:
            return fixed
        if request.method == "POST" and path.endswith("/listeners"):
            self._n += 1
            listener_id = f"01LST{self._n:021d}"
            self.listeners[listener_id] = listener_body(
                listener_id,
                label=body["label"],
                filter=body["filter"],
                prompt=body.get("prompt", ""),
                min_confidence=body.get("min_confidence") or 0.7,
            )
            return httpx.Response(201, json=self.listeners[listener_id])
        listener_id = path.rstrip("/").rsplit("/", 1)[-1]
        if listener_id not in self.listeners:
            return httpx.Response(404, json={"error": "not_found", "detail": "no such listener"})
        if request.method == "GET":
            return httpx.Response(200, json=self.listeners[listener_id])
        if request.method == "PATCH":
            current = self.listeners[listener_id]
            if "label" in body:
                current["label"] = body["label"]
            if "filter" in body:
                current["version"]["filter"] = body["filter"]
                current["version"]["prompt"] = body.get("prompt", "")
            return httpx.Response(200, json=current)
        if request.method == "DELETE":
            del self.listeners[listener_id]
            return httpx.Response(204)
        return httpx.Response(405)


class FakeIdentity:
    """The identity service's API keys: mints numbered sync keys."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.next: list[httpx.Response] = []
        self.minted = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if self.next:
            return self.next.pop(0)
        if request.method == "POST":
            self.minted += 1
            key = {
                "id": f"01KEY{self.minted:021d}",
                "name": body["name"],
                "last_four": "abcd",
                "created_at": AT,
                "revoked_at": None,
                "scopes": ["profile", *body.get("scopes", [])],
            }
            return httpx.Response(201, json={"token": f"obw_live_{self.minted}", "key": key})
        return httpx.Response(204)


def install(test, signals: FakeSignals, identity: FakeIdentity) -> None:
    """Route both clients' transports to the fakes for the test's length."""
    for target, fake in (
        ("signals.services.signals_client.transport._httpx_transport", signals),
        ("auth_client.services.api_keys._httpx_transport", identity),
    ):
        patcher = patch(target, return_value=httpx.MockTransport(fake.handler))
        patcher.start()
        test.addCleanup(patcher.stop)
