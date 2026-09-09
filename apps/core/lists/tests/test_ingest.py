"""The async row-push webhook: POST /v1/lists/{id}/ingest accepts a batch
from a minted machine key (or a session), publishes it to the ingest bus,
and returns 202 WITHOUT appending (a worker does that off the bus). The
interim publisher logs and drops, so these tests also pin that the rows do
NOT land in the sheet yet.

The machine path mocks the hub tokeninfo relay at core's transport
boundary; the session path uses the shared cookie-login helper.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_ingest
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.cache import caches
from django.test import TestCase, override_settings
from django.urls import reverse

from common.testing import login_session
from lists.constants import ListOrigin
from lists.services.lists import ListService

_CORE_AUD = "openbower-core"
# Matches TEST_IDENTITY's account, so a session and a machine key resolve to
# the SAME account and reach the same list.
_ACCT = "01JQ" + "B" * 22
_USER = "01JQ" + "A" * 22


class _Capture:
    """A stand-in publisher that records what the endpoint published."""

    def __init__(self, sink: list) -> None:
        self.sink = sink

    def publish(self, event) -> None:
        self.sink.append(event)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


def _claims(**over):
    base = {
        "active": True,
        "sub": _USER,
        "account_id": _ACCT,
        "username": "user@example.com",
        "aud": [_CORE_AUD],
        "scope": "profile",
        "exp": 4102444800,
    }
    base.update(over)
    return base


def _make_list() -> str:
    lst = ListService(account_id=_ACCT, user_id=_USER).create(
        label="webhook target",
        columns=[{"key": "domain", "label": "Domain", "type": "url"}],
        origin=ListOrigin.MANUAL,
    )
    return str(lst.id)


@override_settings(CORE_AUDIENCE=_CORE_AUD, TOKENINFO_MISS_LIMIT_PER_MINUTE=100000)
class IngestMachineTests(TestCase):
    def setUp(self) -> None:
        caches["default"].clear()
        caches["tokeninfo"].clear()
        self.list_id = _make_list()

    def _ingest(self, body, token="obw_live", claims=None):
        with patch(
            "resource_server.idp.transport.httpx.get",
            return_value=_FakeResponse(200, claims if claims is not None else _claims()),
        ):
            return self.client.post(
                reverse("lists_ingest", kwargs={"id": self.list_id}),
                body,
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

    def test_machine_key_push_is_accepted_and_published(self):
        captured: list = []
        with patch("lists.views.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self._ingest({"rows": [{"domain": "acme.com"}, {"domain": "example.io"}]})
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["accepted"], 2)
        self.assertTrue(body["event_id"])
        # Exactly one event, carrying the rows bound to this list + account.
        self.assertEqual(len(captured), 1)
        event = captured[0]
        self.assertEqual(event.list_id, self.list_id)
        self.assertEqual(event.account_id, _ACCT)
        self.assertEqual([r["domain"] for r in event.rows], ["acme.com", "example.io"])
        self.assertEqual(event.event_id, body["event_id"])

    def test_interim_publisher_does_not_append_rows_yet(self):
        # Honest about async: accepted, but the sheet is unchanged until the
        # worker consumes the bus (a follow-up).
        self._ingest({"rows": [{"domain": "acme.com"}]})
        lst = ListService(account_id=_ACCT, user_id=_USER).get(self.list_id)
        self.assertEqual(lst.row_count, 0)

    def test_a_foreign_accounts_list_is_404(self):
        resp = self._ingest({"rows": [{"domain": "x"}]}, claims=_claims(account_id="01JQ" + "C" * 22))
        self.assertEqual(resp.status_code, 404)

    def test_no_auth_is_401(self):
        resp = self.client.post(
            reverse("lists_ingest", kwargs={"id": self.list_id}),
            {"rows": [{"domain": "x"}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_empty_batch_is_400(self):
        resp = self._ingest({"rows": []})
        self.assertEqual(resp.status_code, 400)


class IngestSessionTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.list_id = self.client.post(
            reverse("lists_index"),
            {"label": "t", "columns": [{"key": "domain", "label": "Domain", "type": "url"}]},
            content_type="application/json",
        ).json()["id"]

    def test_session_push_is_accepted_and_published(self):
        captured: list = []
        with patch("lists.views.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self.client.post(
                reverse("lists_ingest", kwargs={"id": self.list_id}),
                {"rows": [{"domain": "acme.com"}]},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["accepted"], 1)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].list_id, self.list_id)
