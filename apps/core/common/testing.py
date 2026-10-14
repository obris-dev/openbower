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
    """An httpx response at the boundary the tests mock: a status, a
    body, and headers (a vendor's Retry-After rides there). `not_json`
    is a response whose body is not JSON (a proxy page, a truncated
    reply): json() raises as httpx's does."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict | list | None = None,
        headers: dict | None = None,
        *,
        not_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self._not_json = not_json

    def json(self) -> dict | list:
        if self._not_json:
            raise ValueError("body is not JSON")
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
