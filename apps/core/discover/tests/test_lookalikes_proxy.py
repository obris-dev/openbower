"""Session -> discover-proxy flow for the look-alike endpoint.

The whole app side runs for real (login callback, AppSession, cookie auth,
the proxy view, the client's AUTH FLOW with its refresh-retry, shared-
contract validation); the data service is mocked at the httpx TRANSPORT
(httpx.MockTransport), so the auth flow runs through real httpx machinery,
and the IdP pair is mocked at its own httpx boundary during login. This
guards the seam the static checks can't: the proxy forwarding the
session's access token and faithfully mapping the data service's
success/400/403/outage answers.

Run: DJANGO_ENV=test uv run python manage.py test discover
"""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from auth_client.models import AppSession
from auth_client.services.oauth import AuthUpstreamError, AuthUpstreamUnavailable, TokenResponse

_IDENTITY = {
    "id": "01JQ" + "A" * 22,
    "email": "user@example.com",
    "account_id": "01JQ" + "B" * 22,
}
_TOKENS = {"access_token": "access-abc", "refresh_token": "refresh-abc", "expires_in": 3600}

_COMPANY = {
    "id": "01JQ" + "C" * 22,
    "domain": "similar.example",
    "name": "Similar Co",
    "industry": "computer software",
    "locality": "",
    "region": "",
    "country": "",
    "linkedin_url": "",
    "size_band": "11-50",
    "founded_year": None,
    "source": "pdl_free",
    "snapshot_date": "2026-07-30",
}
_LOOKALIKES = {
    "engine": "stub",
    "items": [{"company": _COMPANY, "score": 0.99, "rank": 1}],
    "next_cursor": None,
    "unresolved_domains": ["unknown.example"],
}


class _Resp:
    """Minimal stand-in for an httpx.Response for the IDP mocks (the data
    service side uses real httpx.Response through MockTransport)."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


def _data_transport(*responses):
    """Patch the data client's transport with an httpx.MockTransport that
    serves `responses` in order (the last repeats) and snapshots every
    request. Snapshots, not the objects: the auth flow's retry MUTATES the
    request it re-yields, so late reads of a stored request would see the
    second attempt's headers. Each response spec is (status, kwargs) for
    httpx.Response, or the string "network" to raise a connect error.
    Returns (patcher, calls)."""
    calls: list[dict] = []
    seq = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization"),
                "content": bytes(request.content),
            }
        )
        spec = seq.pop(0) if len(seq) > 1 else seq[0]
        if spec == "network":
            raise httpx.ConnectError("refused")
        status, kwargs = spec
        return httpx.Response(status, **kwargs)

    patcher = patch(
        "discover.services.index_client.transport._httpx_transport",
        return_value=httpx.MockTransport(handler),
    )
    return patcher, calls


class LookalikesProxyTests(TestCase):
    def _login(self) -> None:
        """Establish a real bwr_session via the login callback, mocking the
        IdP at the httpx boundary."""
        resp = self.client.get(reverse("auth_login"))
        state = parse_qs(urlparse(resp["Location"]).query)["state"][0]
        with (
            patch("auth_client.services.oauth.transport.httpx.post", return_value=_Resp(200, _TOKENS)),
            patch("auth_client.services.oauth.transport.httpx.get", return_value=_Resp(200, _IDENTITY)),
        ):
            self.client.get(reverse("auth_callback"), {"code": "the-code", "state": state})

    def _query(self, data_response, body: dict | None = None):
        """POST the proxy with the data service mocked at the transport,
        returning (django response, captured request snapshots)."""
        patcher, calls = _data_transport(data_response)
        with patcher:
            resp = self.client.post(
                reverse("discover_lookalikes"),
                body if body is not None else {"domains": ["acme.com", "initech.com"], "limit": 5},
                content_type="application/json",
            )
        return resp, calls

    def test_cancel_forwards_and_translates_the_canceled_envelope(self):
        # POST cancel proxies to the data service's cancel endpoint and
        # translates canceled as TERMINAL (200), never as keep-polling.
        self._login()
        canceled = {
            "engine": "stub",
            "status": "canceled",
            "run_id": "01JQ" + "C" * 22,
            "items": [],
            "next_cursor": None,
        }
        patcher, calls = _data_transport((200, {"json": canceled}))
        with patcher:
            resp = self.client.post(
                reverse("discover_lookalike_run_cancel", kwargs={"id": "01JQ" + "C" * 22}),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "canceled")
        self.assertIn("/lookalikes/runs/01JQ" + "C" * 22 + "/cancel", calls[-1]["url"])

    def test_authorize_request_carries_scope_and_resources(self):
        # The authorize redirect is the seam static checks can't cover: a
        # regression in the scope string or the doseq resource encoding would
        # silently break the data-audience binding.
        resp = self.client.get(reverse("auth_login"))
        query = parse_qs(urlparse(resp["Location"]).query)
        self.assertEqual(query["scope"], ["profile data:read data:write"])
        # RFC 8707: both resource indicators (IdP + data) present as repeated
        # params, so the minted token is bound to both audiences.
        self.assertEqual(
            sorted(query["resource"]),
            sorted([settings.OPENBOWER_AUTH_URL, settings.OPENBOWER_DATA_URL]),
        )

    def test_unauthenticated_is_401(self):
        resp = self.client.post(reverse("discover_lookalikes"), {}, content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "not_authenticated")

    def test_forwards_token_and_drops_unknown_keys(self):
        self._login()
        # Send bogus keys alongside the real ones: the security property is
        # that only the known query keys reach the data service.
        resp, calls = self._query(
            (200, {"json": _LOOKALIKES}),
            body={"domains": ["acme.com", "initech.com"], "limit": 5, "is_admin": True, "webhook_url": "http://evil"},
        )
        self.assertEqual(resp.status_code, 200)
        # The session's IdP access token rode the Authorization header.
        self.assertEqual(calls[-1]["authorization"], "Bearer access-abc")
        # Only the known keys were forwarded; the smuggled ones are gone.
        self.assertEqual(json.loads(calls[-1]["content"]), {"domains": ["acme.com", "initech.com"], "limit": 5})
        body = resp.json()
        self.assertEqual(body["engine"], "stub")
        self.assertEqual(body["items"][0]["company"]["domain"], "similar.example")
        self.assertEqual(body["unresolved_domains"], ["unknown.example"])

    def test_data_404_passes_through(self):
        self._login()
        resp, _ = self._query((404, {"json": {"error": "not_found", "detail": "no seed set with that id"}}))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "not_found")

    def test_network_error_maps_to_503(self):
        self._login()
        resp, _ = self._query("network", body={"domains": ["acme.com", "initech.com"]})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "data_unavailable")

    def test_non_json_data_body_maps_to_502(self):
        self._login()
        resp, _ = self._query((200, {"text": "not json"}))
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error"], "data_error")

    def test_data_400_passes_through(self):
        self._login()
        resp, _ = self._query((400, {"json": {"error": "invalid_request", "detail": "none of the domains resolve"}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_request")

    def test_data_403_maps_to_reauth(self):
        self._login()
        resp, _ = self._query((403, {"json": {"error": "forbidden", "detail": "token lacks the data:read scope"}}))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "data_access_denied")

    def test_data_outage_maps_to_503(self):
        self._login()
        resp, _ = self._query((500, {"json": {}}))
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "data_unavailable")

    def test_malformed_data_body_maps_to_502(self):
        self._login()
        resp, _ = self._query((200, {"json": {"engine": "stub"}}))  # missing items: fails contract
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error"], "data_error")

    def test_non_object_data_body_maps_to_502(self):
        # A 200 whose JSON is valid but not an object (a bare list) is an
        # upstream contract break, not a client error: it must map to 502,
        # never be model_validated as if it were the response envelope.
        self._login()
        resp, _ = self._query((200, {"json": ["not", "an", "object"]}))
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error"], "data_error")

    # The data side is mocked at the TRANSPORT, so the auth flow's
    # 401-refresh-retry runs through real httpx machinery; the refresh
    # itself (an IdP call) is patched one level up at the OAuth client.
    _REFRESH_TARGET = "auth_client.services.oauth.OAuthClientService.Global.refresh"

    def test_expired_token_refreshes_and_retries(self):
        # The data service rejects the forwarded token as expired (401). The
        # proxy must refresh the session once and retry with the NEW token,
        # so the user sees a normal 200, not a bounce to re-login.
        self._login()
        fresh = TokenResponse(access_token="access-def", refresh_token="refresh-def", expires_in=3600)
        patcher, calls = _data_transport(
            (401, {"json": {"error": "invalid_token", "detail": "expired"}}),
            (200, {"json": _LOOKALIKES}),
        )
        with patcher, patch(self._REFRESH_TARGET, return_value=fresh) as refresh:
            resp = self.client.post(
                reverse("discover_lookalikes"),
                {"domains": ["acme.com", "initech.com"], "limit": 5},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        # Two data calls: the stale token, then the refreshed one.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["authorization"], "Bearer access-abc")
        self.assertEqual(calls[1]["authorization"], "Bearer access-def")
        self.assertEqual(refresh.call_count, 1)

    def test_dead_refresh_token_forces_reauth(self):
        # 401 from data, and the refresh token is dead (terminal upstream
        # error): the session genuinely cannot reach data, so re-login.
        self._login()
        patcher, _ = _data_transport((401, {"json": {"error": "invalid_token"}}))
        with patcher, patch(self._REFRESH_TARGET, side_effect=AuthUpstreamError("dead refresh token")):
            resp = self.client.post(
                reverse("discover_lookalikes"),
                {"domains": ["acme.com", "initech.com"]},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "data_access_denied")

    def test_transient_refresh_failure_maps_to_503_and_keeps_session(self):
        # Data 401 triggers a refresh, but the IdP is transiently down. The
        # session must SURVIVE (a blip is not a dead session) and the user gets
        # a 503, not a forced re-login. Guards the regression where force_refresh
        # revoked on the transient subclass of AuthUpstreamError.
        self._login()
        patcher, _ = _data_transport((401, {"json": {"error": "invalid_token"}}))
        with patcher, patch(self._REFRESH_TARGET, side_effect=AuthUpstreamUnavailable("idp down")):
            resp = self.client.post(
                reverse("discover_lookalikes"),
                {"domains": ["acme.com", "initech.com"]},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "data_unavailable")
        self.assertIsNone(AppSession.objects.get(email=_IDENTITY["email"]).revoked_at)

    def test_refreshed_token_still_rejected_forces_reauth(self):
        # Refresh succeeds but the brand-new token is ALSO rejected: not a
        # transient lapse, so stop retrying and re-login.
        self._login()
        fresh = TokenResponse(access_token="access-def", refresh_token="refresh-def", expires_in=3600)
        patcher, calls = _data_transport((401, {"json": {"error": "invalid_token"}}))
        with patcher, patch(self._REFRESH_TARGET, return_value=fresh):
            resp = self.client.post(
                reverse("discover_lookalikes"),
                {"domains": ["acme.com", "initech.com"]},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "data_access_denied")
        # Tried exactly twice (original + one retry), then gave up.
        self.assertEqual(len(calls), 2)

    def test_pending_202_passes_through(self):
        # The async lifecycle: a cold cohort answers 202 with a run to
        # poll; the proxy mirrors body and status untouched.
        self._login()
        pending = {
            "engine": "embedding_v1",
            "status": "pending",
            "run_id": "01JQ" + "R" * 22,
            "items": [],
            "next_cursor": None,
            "unresolved_domains": [],
        }
        resp, _ = self._query((202, {"json": pending}))
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["run_id"], pending["run_id"])
        self.assertEqual(body["items"], [])

    def test_run_poll_serves_complete_page(self):
        self._login()
        complete = {**_LOOKALIKES, "status": "complete", "run_id": "01JQ" + "R" * 22}
        patcher, calls = _data_transport((200, {"json": complete}))
        with patcher:
            resp = self.client.get(reverse("discover_lookalike_run", kwargs={"id": "01JQ" + "R" * 22}))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["items"][0]["company"]["domain"], "similar.example")
        # The session token rode the poll too.
        self.assertEqual(calls[-1]["authorization"], "Bearer access-abc")

    def test_run_poll_failed_maps_to_data_error(self):
        # A failed RUN is an upstream fault from the web's point of view:
        # the proxy translates the (successful) poll into 502 data_error
        # with a GENERIC detail: the run's own detail is upstream-internal
        # error text (logged, never forwarded to the browser). The data
        # service self-heals the run on the next query POST.
        self._login()
        failed = {
            "engine": "embedding_v1",
            "status": "failed",
            "run_id": "01JQ" + "R" * 22,
            "items": [],
            "detail": "boom",
        }
        patcher, _ = _data_transport((200, {"json": failed}))
        with patcher:
            resp = self.client.get(reverse("discover_lookalike_run", kwargs={"id": "01JQ" + "R" * 22}))
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json(), {"error": "data_error", "detail": "look-alike computation failed"})

    def test_run_poll_404_passes_through(self):
        self._login()
        patcher, _ = _data_transport((404, {"json": {"error": "not_found", "detail": "no run with that id"}}))
        with patcher:
            resp = self.client.get(reverse("discover_lookalike_run", kwargs={"id": "01JQ" + "R" * 22}))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "not_found")

    def test_own_validation_400_is_on_contract(self):
        # The proxy's OWN request-validation failure must return the same
        # {error, detail} contract as every other error, not DRF's default
        # field-keyed body (so the web's body.detail read works).
        self._login()
        resp = self.client.post(
            reverse("discover_lookalikes"),
            {"domains": []},  # empty list violates allow_empty=False
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_request")
        self.assertIn("detail", body)
