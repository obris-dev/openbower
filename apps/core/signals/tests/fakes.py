"""Stand-ins for the two upstream services a log's lifecycle calls, at their
httpx seams: the signals service (listeners) and the identity service (the
sync key's mint). Each records what it was sent and answers as the real
one does; a test overrides a route's next answer to stage a failure."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs

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


def match_body(n: int, *, company: str) -> dict:
    """The nth match of a listener (its id sorts with n), a funding event
    about `company`."""
    return {
        "id": f"01MAT{n:021d}",
        "version_id": "01JQ" + "V" * 22,
        "confidence": None,
        "reason": "",
        "judged_at": AT,
        "event": {
            "id": f"01EVT{n:021d}",
            "signal": "funding",
            "data": {
                "company": {"name": company, "domain": f"{company.lower()}.example", "hq": None, "description": None},
                "amount": 5_000_000.0,
                "currency": "USD",
                "stage": "seed",
                "instruments": ["equity"],
                "lead_investors": ["Example Ventures"],
                "other_investors": [],
            },
            "logged_at": AT,
            "source": {
                "title": f"{company} raises",
                "summary": "",
                "url": "https://news.example.com/a",
                "published": AT,
            },
        },
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
        # Each listener's matches, oldest first, for its poll; `on_matches`
        # runs once just before the next poll answers (a rival poller).
        self.matches: dict[str, list[dict]] = {}
        self.on_matches = None
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
        if request.method == "GET" and path.endswith("/matches"):
            return self._matches(path.rsplit("/", 2)[-2], parse_qs(request.url.query.decode()))
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

    def _matches(self, listener_id: str, query: dict[str, list[str]]) -> httpx.Response:
        """The service's poll: after a match id, at most `limit`, and the
        cursor given back when nothing is new."""
        rival, self.on_matches = self.on_matches, None
        if rival is not None:
            rival()
        if listener_id not in self.listeners:
            return httpx.Response(404, json={"error": "not_found", "detail": "no such listener"})
        held = self.matches.get(listener_id, [])
        after = query.get("after", [None])[0]
        limit = int(query.get("limit", ["50"])[0])
        if after is not None:
            ids = [match["id"] for match in held]
            if after not in ids:
                return httpx.Response(400, json={"error": "invalid_request", "detail": "unknown cursor"})
            held = held[ids.index(after) + 1 :]
        page = held[:limit]
        next_cursor = page[-1]["id"] if page else after
        return httpx.Response(200, json={"items": page, "next_cursor": next_cursor})


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
