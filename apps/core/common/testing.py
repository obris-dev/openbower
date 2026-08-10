"""Shared test plumbing: establish a real bwr_session by driving the
login callback with the IdP mocked at the httpx boundary (the same seam
every app test mocks)."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.urls import reverse

IDENTITY = {
    "id": "01JQ" + "A" * 22,
    "email": "user@example.com",
    "account_id": "01JQ" + "B" * 22,
}
TOKENS = {"access_token": "access-abc", "refresh_token": "refresh-abc", "expires_in": 3600}


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict:
        if not self._json and self.text:
            raise ValueError("not JSON")
        return self._json


def login(client) -> None:
    """Drive the real login callback against a mocked IdP; afterwards the
    Django test client carries a live bwr_session cookie."""
    resp = client.get(reverse("auth_login"))
    state = parse_qs(urlparse(resp["Location"]).query)["state"][0]
    with (
        patch("auth_client.services.oauth.transport.httpx.post", return_value=FakeResponse(200, TOKENS)),
        patch("auth_client.services.oauth.transport.httpx.get", return_value=FakeResponse(200, IDENTITY)),
    ):
        client.get(reverse("auth_callback"), {"code": "the-code", "state": state})
