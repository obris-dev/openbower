"""Shared test helpers: establish a real bwr_session through the login
callback with the IdP mocked at its httpx boundary, so view tests
exercise real cookie auth without an identity provider."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.urls import reverse

TEST_IDENTITY = {
    "id": "01JQ" + "A" * 22,
    "email": "user@example.com",
    "account_id": "01JQ" + "B" * 22,
}
_TOKENS = {"access_token": "access-abc", "refresh_token": "refresh-abc", "expires_in": 3600}


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


def login_session(client, identity: dict | None = None) -> None:
    resp = client.get(reverse("auth_login"))
    state = parse_qs(urlparse(resp["Location"]).query)["state"][0]
    with (
        patch("auth_client.services.oauth.transport.httpx.post", return_value=FakeResponse(200, _TOKENS)),
        patch(
            "auth_client.services.oauth.transport.httpx.get",
            return_value=FakeResponse(200, identity or TEST_IDENTITY),
        ),
    ):
        client.get(reverse("auth_callback"), {"code": "the-code", "state": state})
