"""The discover <-> lists round-trip: save a completed run as a sheet,
and seed a search from a sheet's chosen identifier column. The data
service is mocked at the httpx transport, lists run for real.

Run: DJANGO_ENV=test uv run python manage.py test discover
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
from django.test import TestCase
from django.urls import reverse

from common.testing import login_session
from lists.constants import ListOrigin
from lists.models import List, ListRow
from lists.services.lists import ListService

_RUN_ID = "01JQ" + "R" * 22


def _company(n: int) -> dict:
    return {
        "id": "01JQ" + f"{n:022d}",
        "domain": f"similar{n}.example",
        "name": f"Similar {n}",
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


def _page(ranks: list[int], *, next_cursor: str | None) -> dict:
    return {
        "engine": "embedding_v1",
        "status": "complete",
        "run_id": _RUN_ID,
        "items": [{"company": _company(r), "score": 0.9, "rank": r, "group": "", "description": ""} for r in ranks],
        "next_cursor": next_cursor,
        "unresolved_domains": [],
    }


def _transport(*responses):
    calls: list[dict] = []
    seq = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "content": bytes(request.content)})
        spec = seq.pop(0) if len(seq) > 1 else seq[0]
        status, kwargs = spec
        return httpx.Response(status, **kwargs)

    patcher = patch(
        "discover.services.index_client.transport._httpx_transport",
        return_value=httpx.MockTransport(handler),
    )
    return patcher, calls


class SaveListTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def _save(self, *responses, body: dict | None = None):
        patcher, calls = _transport(*responses)
        with patcher:
            resp = self.client.post(
                reverse("discover_lookalike_run_save_list", kwargs={"id": _RUN_ID}),
                body if body is not None else {"label": "Saved run"},
                content_type="application/json",
            )
        return resp, calls

    def test_snapshots_all_pages_into_a_sheet(self):
        resp, calls = self._save(
            (200, {"json": _page([1, 2], next_cursor=f"{_RUN_ID}:2")}),
            (200, {"json": _page([3], next_cursor=None)}),
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["row_count"], 3)
        self.assertEqual(body["origin"], ListOrigin.DISCOVER)
        self.assertEqual(body["origin_ref"], _RUN_ID)
        rows = ListRow.objects.filter(list_id=body["id"]).order_by("rank", "id")
        self.assertEqual(rows[0].data["domain"], "similar1.example")
        # The first upstream page was asked from the top of the run.
        self.assertIn(f"{_RUN_ID}:0", calls[0]["content"].decode())

    def test_malformed_run_id_is_404(self):
        login_session(self.client)
        resp = self.client.post(
            reverse("discover_lookalike_run_save_list", kwargs={"id": "x" * 80}),
            {"label": "Saved run"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(List.objects.count(), 0)

    def test_non_object_body_is_400(self):
        resp, _ = self._save((200, {"json": _page([], next_cursor=None)}), body=[])
        self.assertEqual(resp.status_code, 400)

    def test_absurd_limit_is_400(self):
        # Past CPython's int/str digit limit. Sent as raw bytes (the
        # test client cannot even serialize the number); the parse layer
        # answers 400, never a 500 from int().
        login_session(self.client)
        resp = self.client.post(
            reverse("discover_lookalike_run_save_list", kwargs={"id": _RUN_ID}),
            data='{"label": "x", "limit": ' + "9" * 5000 + "}",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(List.objects.count(), 0)

    def test_full_list_keeps_what_fit_from_the_final_page(self):
        # Cap 3, pages of 2: the second page must part-fill to exactly
        # the cap, not drop whole.
        with patch("discover.views.MAX_LIST_ROWS", 3):
            resp, _ = self._save(
                (200, {"json": _page([1, 2], next_cursor=f"{_RUN_ID}:2")}),
                (200, {"json": _page([3, 4], next_cursor=None)}),
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["row_count"], 3)

    def test_service_full_mid_save_keeps_the_partial(self):
        # The service's own cap trips (the concurrent-writer path): the
        # partial stays, honestly, with a 201.
        with patch("lists.services.lists.MAX_LIST_ROWS", 3):
            resp, _ = self._save(
                (200, {"json": _page([1, 2], next_cursor=f"{_RUN_ID}:2")}),
                (200, {"json": _page([3, 4], next_cursor=None)}),
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["row_count"], 2)

    def test_unexpected_error_mid_save_leaves_no_half_sheet(self):
        self.client.raise_request_exception = False
        with patch("lists.services.lists.ListService.add_rows", side_effect=RuntimeError("mapping bug")):
            resp, _ = self._save((200, {"json": _page([1, 2], next_cursor=None)}))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(List.objects.count(), 0)

    def test_excluded_domains_do_not_reach_the_sheet(self):
        # The rank walk stays pre-exclusion (limit means the cutoff);
        # exclusion removes from what lands, mirroring the table.
        resp, _ = self._save(
            (200, {"json": _page([1, 2], next_cursor=f"{_RUN_ID}:2")}),
            (200, {"json": _page([3], next_cursor=None)}),
            body={"label": "Saved run", "exclude": ["https://www.similar2.example/x", "unrelated.example"]},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["row_count"], 2)
        domains = [r.data["domain"] for r in ListRow.objects.filter(list_id=body["id"]).order_by("rank", "id")]
        self.assertEqual(domains, ["similar1.example", "similar3.example"])

    def test_over_cap_limit_clamps_to_201(self):
        # Past the row cap "means everything": the exact regression a
        # 400 here would reintroduce.
        resp, _ = self._save(
            (200, {"json": _page([1], next_cursor=None)}),
            body={"label": "Big", "limit": 999_999},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["row_count"], 1)

    def test_stuck_cursor_deletes_the_partial(self):
        # The same page forever: the advance guard must stop the walk
        # and take the partial with it.
        resp, _ = self._save((200, {"json": _page([1, 2], next_cursor=f"{_RUN_ID}:0")}))
        self.assertGreaterEqual(resp.status_code, 500)
        self.assertEqual(List.objects.count(), 0)

    def test_list_deleted_mid_save_is_a_409(self):
        from lists.services.lists import ListNotFound

        self.client.raise_request_exception = False
        with patch("lists.services.lists.ListService.add_rows", side_effect=ListNotFound("gone")):
            resp, _ = self._save((200, {"json": _page([1], next_cursor=None)}))
        self.assertEqual(resp.status_code, 409)
        self.assertIn("deleted during", resp.json()["detail"])

    def test_incomplete_run_is_a_400_and_saves_nothing(self):
        resp, _ = self._save((200, {"json": {**_page([], next_cursor=None), "status": "pending"}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(List.objects.count(), 0)

    def test_upstream_failure_mid_save_deletes_the_partial_list(self):
        resp, _ = self._save(
            (200, {"json": _page([1], next_cursor=f"{_RUN_ID}:1")}),
            (500, {"json": {"error": "boom"}}),
        )
        # An upstream 5xx maps to 503 data_unavailable (transient), and
        # the partial sheet must not survive it.
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(List.objects.count(), 0)
        self.assertEqual(ListRow.objects.count(), 0)

    def test_limit_caps_the_snapshot(self):
        resp, _ = self._save(
            (200, {"json": _page([1, 2], next_cursor=f"{_RUN_ID}:2")}),
            body={"label": "Top two", "limit": 2},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["row_count"], 2)


class SeedFromListTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.service = ListService(account_id="01JQ" + "B" * 22)

    def _sheet(self, rows: list[dict]) -> List:
        target = self.service.create(
            owner_id="01US" + "B" * 22,
            label="Sheet",
            columns=[{"kind": "plain", "key": "website", "label": "Website", "type": "url"}],
            origin=ListOrigin.CSV,
        )
        self.service.add_rows(target, rows)
        return target

    def _seed(self, body: dict):
        patcher, calls = _transport((200, {"json": _page([1], next_cursor=None)}))
        with patcher:
            resp = self.client.post(reverse("discover_lookalikes"), body, content_type="application/json")
        return resp, calls

    def test_unknown_identifier_key_is_400(self):
        target = self._sheet([{"website": "acme.com"}, {"website": "initech.com"}])
        resp, _ = self._seed({"list_id": str(target.id), "identifier_key": "no_such_column"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("does not exist", resp.json()["detail"])

    def test_column_values_normalize_and_forward_as_domains(self):
        target = self._sheet(
            [
                {"website": "https://www.acme.com/about"},
                {"website": "ACME.com"},  # dedupes with the dressed form
                {"website": "initech.com."},
                {"website": "not a domain"},
            ]
        )
        resp, calls = self._seed({"list_id": str(target.id), "identifier_key": "website"})
        self.assertEqual(resp.status_code, 200)
        sent = json.loads(calls[-1]["content"])
        self.assertEqual(sent["domains"], ["acme.com", "initech.com"])

    def test_too_few_usable_values_is_a_clear_400(self):
        target = self._sheet([{"website": "only-one.example"}, {"website": "junk"}])
        resp, _ = self._seed({"list_id": str(target.id), "identifier_key": "website"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("usable domains", resp.json()["detail"])

    def test_list_and_key_go_together_and_exclude_domains(self):
        target = self._sheet([{"website": "acme.com"}, {"website": "initech.com"}])
        resp, _ = self._seed({"list_id": str(target.id)})
        self.assertEqual(resp.status_code, 400)
        resp, _ = self._seed({"list_id": str(target.id), "identifier_key": "website", "domains": ["acme.com", "x.com"]})
        self.assertEqual(resp.status_code, 400)

    def test_foreign_list_reads_as_missing(self):
        foreign = ListService(account_id="01AC" + "Z" * 22).create(
            owner_id="01US" + "B" * 22, label="Not yours", columns=[], origin=ListOrigin.MANUAL
        )
        resp, _ = self._seed({"list_id": str(foreign.id), "identifier_key": "website"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no list", resp.json()["detail"])
