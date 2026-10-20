"""The machine API key proxy: the session's token dials the identity
service, its answers and refusals relay verbatim, and the lanes that
cannot use it are refused. The identity service is a MockTransport at
the httpx seam, so the auth flow runs for real.

Run: DJANGO_ENV=test uv run python manage.py test auth_client
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
from django.test import TestCase
from django.urls import reverse

from auth_client.models import AppSession
from common.testing import login_session
from resource_server.claims_cache import get_claims, remember_claims

KEY = {
    "id": "01KEYAAAAAAAAAAAAAAAAAAAAA",
    "name": "import script",
    "last_four": "k9ZQ",
    "created_at": "2026-10-20T17:00:00+00:00",
    "revoked_at": None,
}


class _TokenEndpoint:
    """The identity service's token endpoint, refusing the refresh: a
    definitive rejection, so the session is revoked and the 401 stands."""

    status_code = 400

    @staticmethod
    def json() -> dict:
        return {"error": "invalid_grant"}


class _Upstream:
    """Scripted identity service: answers in order, records what it saw."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers.pop(0)


class ApiKeyProxyTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.session = AppSession.objects.get()

    def _with(self, *answers: httpx.Response):
        upstream = _Upstream(*answers)
        patcher = patch(
            "auth_client.services.api_keys._httpx_transport", return_value=httpx.MockTransport(upstream.handler)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return upstream

    def test_list_dials_the_service_as_the_user(self):
        upstream = self._with(httpx.Response(200, json={"keys": [KEY]}))
        resp = self.client.get(reverse("auth_api_keys"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {"keys": [KEY]})
        (request,) = upstream.requests
        self.assertEqual(request.method, "GET")
        self.assertTrue(str(request.url).endswith("/auth/api-keys"))
        self.assertEqual(request.headers["authorization"], f"Bearer {self.session.access_token}")

    def test_mint_relays_the_token_once(self):
        upstream = self._with(httpx.Response(201, json={"token": "obk_live_x", "key": KEY}))
        resp = self.client.post(reverse("auth_api_keys"), {"name": "import script"}, content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["token"], "obk_live_x")
        self.assertEqual(resp.json()["key"]["name"], "import script")
        (request,) = upstream.requests
        self.assertEqual(json.loads(request.content), {"name": "import script"})

    def test_refusals_relay_verbatim(self):
        self._with(httpx.Response(409, json={"error": "key_limit_reached", "detail": "key limit reached (8)"}))
        resp = self.client.post(reverse("auth_api_keys"), {"name": "one more"}, content_type="application/json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": "key_limit_reached", "detail": "key limit reached (8)"})

    def test_revoke_answers_as_the_service_did(self):
        upstream = self._with(
            httpx.Response(204), httpx.Response(404, json={"error": "not_found", "detail": "no such api key"})
        )
        resp = self.client.delete(reverse("auth_api_key_detail", kwargs={"id": KEY["id"]}))
        self.assertEqual(resp.status_code, 204)
        self.assertTrue(str(upstream.requests[0].url).endswith(f"/auth/api-keys/{KEY['id']}"))
        # A key the service no longer knows is already revoked: 204, so a
        # row revoked from another tab leaves the list instead of sticking.
        resp = self.client.delete(reverse("auth_api_key_detail", kwargs={"id": "01" + "B" * 24}))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(len(upstream.requests), 2)

    def test_a_status_the_web_cannot_act_on_is_not_relayed(self):
        # A 403 relayed verbatim would read as the login boundary on the
        # web; the service's unknown refusals become a 503 with a code.
        self._with(httpx.Response(403, json={"error": "forbidden", "detail": "keys disabled"}))
        resp = self.client.get(reverse("auth_api_keys"))
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "identity_unavailable")

    def test_revoke_forgets_the_cached_claims_on_this_install(self):
        remember_claims("obw_live", {"active": True, "key_id": KEY["id"]}, ttl=60)
        self.assertIsNotNone(get_claims("obw_live"))
        self._with(httpx.Response(204))
        resp = self.client.delete(reverse("auth_api_key_detail", kwargs={"id": KEY["id"]}))
        self.assertEqual(resp.status_code, 204)
        self.assertIsNone(get_claims("obw_live"))

    def test_a_malformed_id_is_404_without_an_upstream_call(self):
        upstream = self._with(httpx.Response(204))
        # The client decodes nothing: Django does, before routing, which is
        # what would let the suffix reach the upstream URL.
        resp = self.client.delete("/v1/auth/api-keys/01NOPE%3Fx%3D1")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(upstream.requests, [])

    def test_an_unavailable_service_is_503_and_a_dead_session_is_401(self):
        self._with(httpx.Response(503, json={}), httpx.Response(401, json={}))
        resp = self.client.get(reverse("auth_api_keys"))
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json(), {"error": "identity_unavailable", "detail": "identity service unavailable"})
        # The flow refreshes once on a 401; the token endpoint refuses the
        # refresh, so the session is revoked and the 401 stands.
        with patch("auth_client.services.oauth.transport.httpx.post", return_value=_TokenEndpoint()) as post:
            self.assertEqual(self.client.get(reverse("auth_api_keys")).status_code, 401)
        self.assertEqual(post.call_count, 1)

    def test_a_name_is_required(self):
        self._with()
        resp = self.client.post(reverse("auth_api_keys"), {"name": ""}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get(reverse("auth_api_keys")).status_code, 401)
