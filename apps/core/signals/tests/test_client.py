"""The signals client against its wire: every call's method, URL and body,
the session's token on each, bodies validated into the shared contract,
and each failure mapped to its named error. The signals service is
mocked at the httpx TRANSPORT (httpx.MockTransport), so the session's
auth flow runs through real httpx machinery.

The bodies below are shaped as the service returns them (recorded from
it, then made vertical-neutral).

Run: DJANGO_ENV=test uv run python manage.py test signals
"""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import httpx
from django.test import TestCase

from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.oauth import TokenResponse
from auth_client.services.sessions import AppSessionService
from openbower_schema import AuthUser
from signals.services.signals_client import (
    SignalsAccessDenied,
    SignalsClientRequestError,
    SignalsClientService,
    SignalsKeyRejected,
    SignalsLimitReached,
    SignalsPreviewRunning,
    SignalsUpstreamError,
    SignalsUpstreamUnavailable,
)

_USER = AuthUser(id="01JQ" + "A" * 22, email="user@example.com", account_id="01JQ" + "B" * 22)
_LISTENER_ID = "01JQ" + "L" * 22
_VERSION_ID = "01JQ" + "V" * 22
_PREVIEW_ID = "01JQ" + "P" * 22
_EVENT_ID = "01JQ" + "E" * 22
_AT = "2026-11-09T16:29:02.062784+00:00"

_EVENT = {
    "id": _EVENT_ID,
    "signal": "funding",
    "data": {
        "company": {
            "name": "Acme Robotics",
            "domain": "acme.example",
            "hq": "Austin, Texas",
            "description": "Builds warehouse robots.",
        },
        "amount": 25_000_000.0,
        "currency": "USD",
        "stage": "series_a",
        "instruments": ["equity"],
        "lead_investors": ["Example Ventures"],
        "other_investors": [],
    },
    "logged_at": _AT,
    "source": {
        "title": "Acme Robotics Raises $25 Million Series A",
        "summary": "Acme Robotics announced a $25 million Series A.",
        "url": "https://news.example.com/acme-series-a",
        "published": _AT,
    },
}
_VERSION = {
    "id": _VERSION_ID,
    "filter": {"stages": ["series_a", "series_b"]},
    "prompt": "Robotics companies in the United States",
    "min_confidence": 0.8,
    "user_id": _USER.id,
    "created_at": _AT,
}
_LISTENER = {
    "id": _LISTENER_ID,
    "label": "US robotics raises",
    "signal": "funding",
    "user_id": _USER.id,
    "status": "active",
    "error": None,
    "version": _VERSION,
    "created_at": _AT,
    "updated_at": _AT,
}
_CATALOG = {
    "signals": [
        {
            "signal": "funding",
            "filter": {"type": "object", "title": "FundingFilter", "properties": {}},
            "event": {"type": "object", "title": "FundingEvent", "properties": {}},
            "min_confidence_default": 0.8,
        }
    ],
    "limits": {"max_listeners": 100, "label_max_length": 200, "prompt_max_length": 8000, "preview_max_events": 20},
}
_MATCH = {
    "id": "01JQ" + "M" * 22,
    "version_id": _VERSION_ID,
    "confidence": 0.93,
    "reason": "A US robotics company raising a Series A.",
    "judged_at": _AT,
    "event": _EVENT,
}
_PREVIEW = {
    "id": _PREVIEW_ID,
    "signal": "funding",
    "filter": {"stages": ["series_a"]},
    "prompt": "Robotics companies",
    "min_confidence": 0.8,
    "user_id": _USER.id,
    "status": "pending",
    "error": None,
    "retry_at": None,
    "created_at": _AT,
    "finished_at": None,
    "rows": [{"outcome": "waiting", "confidence": None, "reason": "", "error": None, "event": _EVENT}],
}


def _signals_transport(*responses):
    """Patch the client's transport with an httpx.MockTransport serving
    `responses` in order (the last repeats), snapshotting each request.
    A spec is (status, body or None), or "network" for a connect error.
    Returns (patcher, calls)."""
    calls: list[dict] = []
    seq = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        content = bytes(request.content)
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers.get("Authorization"),
                "json": json.loads(content) if content else None,
            }
        )
        spec = seq.pop(0) if len(seq) > 1 else seq[0]
        if spec == "network":
            raise httpx.ConnectError("refused")
        status, body = spec
        if body is None:
            return httpx.Response(status)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    patcher = patch(
        "signals.services.signals_client.transport._httpx_transport",
        return_value=httpx.MockTransport(handler),
    )
    return patcher, calls


class _ClientTests(TestCase):
    def setUp(self) -> None:
        tokens = TokenResponse(access_token="access-abc", refresh_token="refresh-abc", expires_in=3600)
        session, _raw = AppSessionService.Global.create(user=_USER, tokens=tokens)
        self.client_ = SignalsClientService.for_session(session)

    def _call(self, response, method: str, **kwargs):
        """One client call against one upstream response: (result, the
        request it sent)."""
        patcher, calls = _signals_transport(response)
        with patcher:
            result = getattr(self.client_, method)(**kwargs)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["authorization"], "Bearer access-abc")
        return result, calls[0]

    def _raises(self, response, error: type[Exception], method: str = "listener", **kwargs):
        kwargs = kwargs or {"listener_id": _LISTENER_ID}
        patcher, _calls = _signals_transport(response)
        with patcher, self.assertRaises(error) as raised:
            getattr(self.client_, method)(**kwargs)
        return raised.exception


class ClientCallsTests(_ClientTests):
    """Each operation: the request it makes, and the typed body it returns."""

    def test_catalog(self):
        catalog, sent = self._call((200, _CATALOG), "catalog")
        self.assertEqual((sent["method"], sent["url"]), ("GET", "http://localhost:8004/v1/signals"))
        self.assertEqual(catalog.signals[0].signal, "funding")
        self.assertEqual(catalog.limits.prompt_max_length, 8000)

    def test_signal_events_pages_by_cursor(self):
        page, sent = self._call(
            (200, {"items": [_EVENT], "next_cursor": _EVENT_ID}),
            "signal_events",
            signal="funding",
            after="01X",
            limit=3,
        )
        self.assertEqual(sent["url"], "http://localhost:8004/v1/signals/funding/events?after=01X&limit=3")
        self.assertEqual(page.items[0].data.company.name, "Acme Robotics")
        self.assertEqual(page.items[0].data.stage, "series_a")
        self.assertEqual(page.next_cursor, _EVENT_ID)

    def test_listeners_page(self):
        page, sent = self._call((200, {"items": [_LISTENER], "next_cursor": None}), "listeners", limit=5)
        self.assertEqual((sent["method"], sent["url"]), ("GET", "http://localhost:8004/v1/listeners?limit=5"))
        self.assertEqual(page.items[0].version.filter.stages, ["series_a", "series_b"])

    def test_create_listener_sends_its_payload(self):
        payload = {"label": "US robotics raises", "signal": "funding", "filter": {"stages": ["series_a"]}}
        listener, sent = self._call((201, _LISTENER), "create_listener", payload=payload)
        self.assertEqual((sent["method"], sent["url"]), ("POST", "http://localhost:8004/v1/listeners"))
        self.assertEqual(sent["json"], payload)
        self.assertEqual(listener.id, _LISTENER_ID)

    def test_listener(self):
        listener, sent = self._call((200, _LISTENER), "listener", listener_id=_LISTENER_ID)
        self.assertEqual((sent["method"], sent["url"]), ("GET", f"http://localhost:8004/v1/listeners/{_LISTENER_ID}"))
        self.assertEqual(listener.status, "active")

    def test_edit_listener_patches(self):
        payload = {"label": "Robotics raises"}
        _listener, sent = self._call((200, _LISTENER), "edit_listener", listener_id=_LISTENER_ID, payload=payload)
        self.assertEqual(sent["method"], "PATCH")
        self.assertEqual(sent["json"], payload)

    def test_delete_listener_takes_a_204(self):
        result, sent = self._call((204, None), "delete_listener", listener_id=_LISTENER_ID)
        self.assertIsNone(result)
        self.assertEqual(sent["method"], "DELETE")

    def test_listener_versions(self):
        page, sent = self._call(
            (200, {"items": [_VERSION], "next_cursor": None}), "listener_versions", listener_id=_LISTENER_ID
        )
        self.assertEqual(sent["url"], f"http://localhost:8004/v1/listeners/{_LISTENER_ID}/versions")
        self.assertEqual(page.items[0].prompt, "Robotics companies in the United States")

    def test_listener_matches_poll_forward(self):
        page, sent = self._call(
            (200, {"items": [_MATCH], "next_cursor": _MATCH["id"]}),
            "listener_matches",
            listener_id=_LISTENER_ID,
            after="01JQ" + "0" * 22,
        )
        self.assertEqual(sent["url"], f"http://localhost:8004/v1/listeners/{_LISTENER_ID}/matches?after=01JQ{'0' * 22}")
        self.assertEqual((page.items[0].confidence, page.items[0].version_id), (0.93, _VERSION_ID))
        self.assertEqual(page.next_cursor, _MATCH["id"])

    def test_create_preview_takes_a_202(self):
        payload = {"signal": "funding", "filter": {}, "prompt": "Robotics companies", "event_ids": [_EVENT_ID]}
        run, sent = self._call((202, _PREVIEW), "create_preview", payload=payload)
        self.assertEqual((sent["method"], sent["url"]), ("POST", "http://localhost:8004/v1/previews"))
        self.assertEqual(sent["json"], payload)
        self.assertEqual((run.status, run.rows[0].outcome), ("pending", "waiting"))

    def test_preview_polls(self):
        done = copy.deepcopy(_PREVIEW)
        done["status"], done["finished_at"] = "complete", _AT
        done["rows"][0].update(outcome="unsure", confidence=0.72, reason="A robotics company, maybe US.")
        run, sent = self._call((200, done), "preview", preview_id=_PREVIEW_ID)
        self.assertEqual((sent["method"], sent["url"]), ("GET", f"http://localhost:8004/v1/previews/{_PREVIEW_ID}"))
        self.assertEqual((run.status, run.rows[0].outcome, run.rows[0].confidence), ("complete", "unsure", 0.72))

    def test_cancel_preview_deletes(self):
        canceled = dict(_PREVIEW, status="canceled", finished_at=_AT)
        run, sent = self._call((200, canceled), "cancel_preview", preview_id=_PREVIEW_ID)
        self.assertEqual(sent["method"], "DELETE")
        self.assertEqual(run.status, "canceled")

    def test_an_id_is_one_path_segment_whatever_it_holds(self):
        _listener, sent = self._call((200, _LISTENER), "listener", listener_id="x?after=y#z")
        self.assertEqual(sent["url"], "http://localhost:8004/v1/listeners/x%3Fafter%3Dy%23z")


class ClientFailureTests(_ClientTests):
    """Each failure as the named error a caller dispatches on."""

    def test_a_refused_request_passes_its_body_through(self):
        body = {"error": "invalid_request", "detail": "event_ids: an event is chosen twice"}
        for status in (400, 404):
            with self.subTest(status=status):
                error = self._raises((status, body), SignalsClientRequestError)
                self.assertEqual((error.status, error.body), (status, body))
                self.assertNotIsInstance(error, (SignalsLimitReached, SignalsPreviewRunning))

    def test_the_listener_cap_is_its_own_error(self):
        body = {"error": "limit_reached", "detail": "an account has at most 100 listeners"}
        payload = {"label": "x", "signal": "funding", "filter": {}}
        error = self._raises((409, body), SignalsLimitReached, "create_listener", payload=payload)
        self.assertEqual((error.status, error.body), (409, body))

    def test_an_open_preview_is_its_own_error_naming_it(self):
        body = {
            "error": "already_running",
            "detail": f"preview {_PREVIEW_ID} is still pending: wait for it, or cancel it",
            "running": {"id": _PREVIEW_ID, "status": "pending", "since": _AT},
        }
        payload = {"signal": "funding", "filter": {}, "event_ids": [_EVENT_ID]}
        error = self._raises((409, body), SignalsPreviewRunning, "create_preview", payload=payload)
        self.assertEqual((error.running.id, error.running.status), (_PREVIEW_ID, "pending"))

    def test_an_open_preview_that_does_not_say_which_breaks_the_contract(self):
        body = {"error": "already_running", "detail": "still pending"}
        payload = {"signal": "funding", "filter": {}, "event_ids": [_EVENT_ID]}
        self._raises((409, body), SignalsUpstreamError, "create_preview", payload=payload)

    def test_another_conflict_is_a_plain_refusal(self):
        error = self._raises((409, {"error": "conflict", "detail": "busy"}), SignalsClientRequestError)
        self.assertNotIsInstance(error, (SignalsLimitReached, SignalsPreviewRunning))

    def test_a_missing_scope_is_access_denied(self):
        self._raises(
            (403, {"error": "forbidden", "detail": "token lacks the signals:write scope"}), SignalsAccessDenied
        )

    def test_an_outage_is_unavailable(self):
        for response in ((503, {"error": "auth_unavailable", "detail": "the IdP is down"}), (429, {}), "network"):
            with self.subTest(response=response):
                self._raises(response, SignalsUpstreamUnavailable)

    def test_a_token_rejected_after_its_refresh_asks_for_a_login(self):
        with patch(
            "auth_client.services.sessions.token_auth.AppSessionService.Global.force_refresh", return_value=None
        ):
            self._raises((401, {"error": "not_authenticated", "detail": "token inactive"}), DownstreamTokenRejected)

    def test_an_unexpected_status_is_an_upstream_error(self):
        payload = {"label": "x", "signal": "funding", "filter": {}}
        self._raises((200, _LISTENER), SignalsUpstreamError, "create_listener", payload=payload)  # a create is a 201

    def test_a_body_off_the_contract_is_an_upstream_error(self):
        off = copy.deepcopy(_LISTENER)
        off["version"]["filter"]["stages"] = ["series_z"]
        cases = {
            "an unknown stage": (200, off),
            "an unknown status": (200, dict(_LISTENER, status="paused")),
            "a missing field": (200, {key: value for key, value in _LISTENER.items() if key != "version"}),
            "not JSON": (200, "<html>"),
            "not an object": (200, [_LISTENER]),
        }
        for case, response in cases.items():
            with self.subTest(case=case):
                self._raises(response, SignalsUpstreamError)


class KeyClientTests(TestCase):
    """The feed's client, acting with an API key: the key as the bearer on
    every call, and a rejected key named as such (a key cannot refresh, so
    a 401 is never the session's retryable rejection)."""

    def setUp(self) -> None:
        self.client_ = SignalsClientService.for_key("obk_live_feed")

    def test_the_key_is_the_bearer(self):
        patcher, calls = _signals_transport((200, {"items": [_MATCH], "next_cursor": _MATCH["id"]}))
        with patcher:
            page = self.client_.listener_matches(listener_id=_LISTENER_ID, after=None)
        self.assertEqual(page.next_cursor, _MATCH["id"])
        self.assertEqual([call["authorization"] for call in calls], ["Bearer obk_live_feed"])

    def test_a_rejected_key_is_named_and_not_retried(self):
        # FAILS if a key's 401 reads as the session's DownstreamTokenRejected:
        # the poller would wait on a refresh that a key never has.
        for status in (401, 403):
            with self.subTest(status=status):
                patcher, calls = _signals_transport((status, {"error": "invalid_token", "detail": "revoked"}))
                with patcher, self.assertRaises(SignalsKeyRejected):
                    self.client_.listener_matches(listener_id=_LISTENER_ID)
                self.assertEqual(len(calls), 1)

    def test_an_outage_stays_an_outage(self):
        patcher, _calls = _signals_transport((503, None))
        with patcher, self.assertRaises(SignalsUpstreamUnavailable):
            self.client_.listener(listener_id=_LISTENER_ID)

    def test_a_missing_listener_stays_a_refused_request(self):
        patcher, _calls = _signals_transport((404, {"error": "not_found", "detail": "no such listener"}))
        with patcher, self.assertRaises(SignalsClientRequestError) as raised:
            self.client_.listener(listener_id=_LISTENER_ID)
        self.assertEqual(raised.exception.status, 404)
