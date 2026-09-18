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

from common.testing import TEST_IDENTITY, login_session
from lists.constants import MAX_INGEST_EVENT_ID_LENGTH, MAX_ROWS_PER_ADD, ListOrigin
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
    lst = ListService(account_id=_ACCT).create(
        owner_id=TEST_IDENTITY["id"],
        label="webhook target",
        columns=[{"kind": "plain", "key": "domain", "label": "Domain", "type": "url"}],
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

    def test_a_caller_supplied_event_id_is_used_as_the_idempotency_key(self):
        captured: list = []
        with patch("lists.views.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self._ingest({"rows": [{"domain": "acme.com"}], "event_id": "caller-key-123"})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["event_id"], "caller-key-123")
        self.assertEqual(captured[0].event_id, "caller-key-123")

    def test_a_minted_ulid_is_used_when_none_supplied(self):
        captured: list = []
        with patch("lists.views.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self._ingest({"rows": [{"domain": "acme.com"}]})
        minted = resp.json()["event_id"]
        self.assertEqual(len(minted), 26)  # a ULID
        self.assertEqual(captured[0].event_id, minted)

    def test_a_blank_event_id_is_rejected_400(self):
        # A present-but-empty key is a client bug, not a request to mint.
        resp = self._ingest({"rows": [{"domain": "acme.com"}], "event_id": ""})
        self.assertEqual(resp.status_code, 400)

    def test_interim_publisher_does_not_append_rows_yet(self):
        # Honest about async: accepted, but the sheet is unchanged until the
        # worker consumes the bus (a follow-up).
        self._ingest({"rows": [{"domain": "acme.com"}]})
        lst = ListService(account_id=_ACCT).get(self.list_id)
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

    def test_over_cap_batch_is_400(self):
        resp = self._ingest({"rows": [{"domain": "x"}] * (MAX_ROWS_PER_ADD + 1)})
        self.assertEqual(resp.status_code, 400)

    def test_over_length_event_id_is_400(self):
        resp = self._ingest({"rows": [{"domain": "x"}], "event_id": "x" * (MAX_INGEST_EVENT_ID_LENGTH + 1)})
        self.assertEqual(resp.status_code, 400)

    def test_a_publish_failure_is_503_not_a_dropped_202(self):
        from lists.ingest import IngestPublishError

        class _Boom:
            def publish(self, event):
                raise IngestPublishError("bus down")

        with patch("lists.views.get_ingest_publisher", return_value=_Boom()):
            resp = self._ingest({"rows": [{"domain": "acme.com"}]})
        self.assertEqual(resp.status_code, 503)


class IngestSessionTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.list_id = self.client.post(
            reverse("lists_index"),
            {"label": "t", "columns": [{"kind": "plain", "key": "domain", "label": "Domain", "type": "url"}]},
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


class IngestValidationTests(TestCase):
    """The push is validated against the sheet's columns BEFORE the 202:
    the async append can never answer the producer, so a bad value or an
    unknown key is a 400 it can fix, not a silent store-as-sent."""

    def setUp(self) -> None:
        login_session(self.client)
        self.list_id = str(
            ListService(account_id=_ACCT)
            .create(
                owner_id=TEST_IDENTITY["id"],
                label="typed target",
                columns=[
                    {"kind": "plain", "key": "domain", "label": "Domain", "type": "url"},
                    {"kind": "plain", "key": "score", "label": "Score", "type": "number"},
                    {"key": "rank", "label": "Rank", "type": "number", "kind": "ai", "node_id": "01ND" + "A" * 22},
                ],
                origin=ListOrigin.MANUAL,
            )
            .id
        )

    def _push(self, rows, capture=None):
        with patch("lists.views.get_ingest_publisher", return_value=_Capture(capture if capture is not None else [])):
            return self.client.post(
                reverse("lists_ingest", kwargs={"id": self.list_id}),
                {"rows": rows},
                content_type="application/json",
            )

    def test_a_bad_number_is_refused_before_publish(self):
        captured: list = []
        resp = self._push([{"score": "banana"}], captured)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "ingest_invalid")
        self.assertIn("score", resp.json()["detail"])
        self.assertEqual(captured, [])  # refused up front, never published

    def test_an_unknown_column_is_refused(self):
        captured: list = []
        resp = self._push([{"mystery": "x"}], captured)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown column", resp.json()["detail"])
        self.assertEqual(captured, [])  # refused up front, never published

    def test_an_overridden_ai_column_is_validated_too(self):
        # An AI column a producer overrides is held to its type like any
        # other, now that the schema surfaces it as pushable.
        captured: list = []
        resp = self._push([{"rank": "not-a-number"}], captured)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("rank", resp.json()["detail"])
        self.assertEqual(captured, [])  # refused up front, never published

    def test_a_well_typed_push_is_accepted(self):
        captured: list = []
        resp = self._push([{"domain": "acme.com", "score": "42"}], captured)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(len(captured), 1)

    def test_a_blank_value_is_not_provided_and_skips_validation(self):
        # Blank is "not provided" (a producer may leave a column for
        # autofill, or just empty), never a type violation.
        captured: list = []
        resp = self._push([{"score": ""}], captured)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(len(captured), 1)

    def test_a_typed_value_is_normalized_to_the_canonical_form_on_publish(self):
        # cells_for_storage (the shared write transform the POST runs)
        # normalizes (commas stripped, dates canonicalized); the POST
        # publishes that canonical form, so a pushed value stores the SAME
        # shape the fill write path stores (no "1,234" next to "1234").
        captured: list = []
        resp = self._push([{"score": "1,234"}], captured)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(captured[0].rows[0]["score"], "1234")  # not the raw "1,234"

    def test_an_oversized_cell_is_clamped_onto_the_bus_not_published_raw(self):
        # The POST runs cells_for_storage, which clamps to CELL_MAX_LENGTH
        # BEFORE publishing, so the bus carries the stored form, not a raw
        # oversized cell that only gets cut later at add_rows.
        from lists.constants import CELL_MAX_LENGTH

        captured: list = []
        resp = self._push([{"domain": "x" * (CELL_MAX_LENGTH + 50)}], captured)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(len(captured[0].rows[0]["domain"]), CELL_MAX_LENGTH)  # clamped on the bus

    def test_the_problem_list_is_capped(self):
        # A wholly-malformed push cannot return a 400 as large as itself:
        # the problems are truncated at MAX_INGEST_PROBLEMS.
        from lists.constants import MAX_INGEST_PROBLEMS

        resp = self._push([{"score": "x"}] * (MAX_INGEST_PROBLEMS + 5))
        self.assertEqual(resp.status_code, 400)
        # one "row N: …" problem per bad row, truncated at the cap
        self.assertEqual(resp.json()["detail"].count("row "), MAX_INGEST_PROBLEMS)
