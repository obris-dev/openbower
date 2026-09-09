"""Core as a resource server: a Bearer machine token is verified by
RELAYING it to the hub's tokeninfo endpoint (no client secret) into the
SAME AppUser the cookie builds, gated by AUDIENCE (core has no scope
tier). The hub is mocked at core's own transport boundary; nothing here
reaches a real hub.

Run: DJANGO_ENV=test uv run python manage.py test resource_server
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

_CORE_AUD = "openbower-core"


def _claims(**over):
    base = {
        "active": True,
        "sub": "01JQ" + "A" * 22,
        "account_id": "01JQ" + "B" * 22,
        "username": "user@example.com",
        "aud": [_CORE_AUD],
        "scope": "profile",
        "exp": 4102444800,  # year 2100
    }
    base.update(over)
    return base


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


@override_settings(CORE_AUDIENCE=_CORE_AUD, TOKENINFO_MISS_LIMIT_PER_MINUTE=100000)
class MachineTokenAuthTests(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def _get_lists(self, token="obw_live", claims=None):
        with patch(
            "resource_server.idp.transport.httpx.get",
            return_value=FakeResponse(200, claims if claims is not None else _claims()),
        ) as get:
            resp = self.client.get(reverse("lists_index"), HTTP_AUTHORIZATION=f"Bearer {token}")
        return resp, get

    def test_a_valid_bound_token_authenticates(self):
        resp, _ = self._get_lists()
        self.assertEqual(resp.status_code, 200)

    def test_the_relay_carries_the_token_as_bearer_and_no_client_secret(self):
        # The whole point: core holds no secret. The presented token is
        # relayed as the ONLY credential, never confidential-client Basic auth.
        _, get = self._get_lists(token="obw_relayme")
        _, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer obw_relayme")
        self.assertNotIn("auth", kwargs)  # no (client_id, secret) Basic tuple

    def test_wrong_audience_is_401(self):
        # A token minted for a different service (its aud is not this core).
        resp, _ = self._get_lists(claims=_claims(aud=["https://data.openbower.com"]))
        self.assertEqual(resp.status_code, 401)

    def test_inactive_token_is_401(self):
        resp, _ = self._get_lists(claims={"active": False})
        self.assertEqual(resp.status_code, 401)

    def test_missing_account_is_403(self):
        resp, _ = self._get_lists(claims=_claims(account_id=None))
        self.assertEqual(resp.status_code, 403)

    def test_hub_unreachable_is_503(self):
        import httpx

        with patch("resource_server.idp.transport.httpx.get", side_effect=httpx.ConnectError("down")):
            resp = self.client.get(reverse("lists_index"), HTTP_AUTHORIZATION="Bearer obw_x")
        self.assertEqual(resp.status_code, 503)

    def test_a_cache_hit_makes_one_relay_call(self):
        with patch("resource_server.idp.transport.httpx.get", return_value=FakeResponse(200, _claims())) as get:
            self.client.get(reverse("lists_index"), HTTP_AUTHORIZATION="Bearer obw_same")
            self.client.get(reverse("lists_index"), HTTP_AUTHORIZATION="Bearer obw_same")
        self.assertEqual(get.call_count, 1)

    def test_the_principal_is_built_from_authority(self):
        from auth_client.authentication import AppUser

        user = AppUser.from_tokeninfo(_claims())
        self.assertEqual(user.email, "user@example.com")  # from the username claim
        self.assertEqual(user.account_id, "01JQ" + "B" * 22)

    def test_a_machine_token_is_refused_off_the_attached_view(self):
        # Discover is cookie-only; a machine token there must not pass.
        with patch("resource_server.idp.transport.httpx.get", return_value=FakeResponse(200, _claims())):
            resp = self.client.post(
                reverse("discover_lookalikes"),
                {"domains": ["acme.com"]},
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer obw_live",
            )
        self.assertEqual(resp.status_code, 401)
