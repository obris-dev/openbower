"""/v1/lists through real cookie auth (the IdP mocked at its httpx
boundary via the shared login helper): CRUD, rows paging, and the CSV
import upload.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

import io

from django.test import TestCase
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from lists.constants import ListOrigin
from lists.models import List
from lists.services.lists import ListService


class ListsViewsTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_unauthenticated_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get(reverse("lists_index")).status_code, 401)

    def test_create_index_detail_rename_delete(self):
        created = self.client.post(
            reverse("lists_index"),
            {"label": "My sheet", "columns": [{"key": "a", "label": "A", "type": "text"}]},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        list_id = created.json()["id"]

        index = self.client.get(reverse("lists_index")).json()
        self.assertEqual([x["id"] for x in index["items"]], [list_id])
        self.assertIsNone(index["next_cursor"])

        renamed = self.client.patch(
            reverse("lists_detail", kwargs={"id": list_id}),
            {"label": "Renamed"},
            content_type="application/json",
        )
        self.assertEqual(renamed.json()["label"], "Renamed")

        gone = self.client.delete(reverse("lists_detail", kwargs={"id": list_id}))
        self.assertEqual(gone.status_code, 204)
        self.assertEqual(self.client.get(reverse("lists_detail", kwargs={"id": list_id})).status_code, 404)

    def test_a_create_body_cannot_choose_a_column_kind(self):
        """The create request declares key, label, and type; a kind a
        client sends is not part of it and never lands (the view builds
        plain columns), so a round-tripped AI column stays plain."""
        resp = self.client.post(
            reverse("lists_index"),
            {
                "label": "Round trip",
                "columns": [{"kind": "ai", "key": "a", "label": "A", "type": "text", "node_id": "x"}],
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        [column] = resp.json()["columns"]
        self.assertEqual(column, {"kind": "plain", "key": "a", "label": "A", "type": "text"})

    def test_ingest_get_omits_the_columns_a_producer_cannot_send(self):
        # The push is self-describing: GET returns the columns a producer
        # OWNS. A column whose cells are recorded is absent, because its
        # value and the record of what filled it are written together by
        # the landing and a push writes rows alone. FAILS if the schema
        # advertises a column the door would refuse.
        lst = ListService(account_id=TEST_IDENTITY["account_id"]).create(
            owner_id=TEST_IDENTITY["id"],
            label="Push target",
            columns=[
                {"kind": "plain", "key": "company", "label": "Company", "type": "url"},
                {"kind": "plain", "key": "contact", "label": "Contact", "type": "text"},
                {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": "01ND" + "A" * 22},
            ],
            origin=ListOrigin.MANUAL,
        )
        schema = self.client.get(reverse("lists_ingest", kwargs={"id": str(lst.id)})).json()
        self.assertEqual(
            schema["columns"],
            [
                {"key": "company", "label": "Company", "type": "url"},
                {"key": "contact", "label": "Contact", "type": "text"},
            ],
        )

    def test_move_to_unknown_folder_is_400(self):
        # Reachable from the UI: the Move-to menu can hold a folder
        # deleted in another tab.
        list_id = self.client.post(
            reverse("lists_index"),
            {"label": "Movable", "columns": [{"key": "a", "label": "A", "type": "text"}]},
            content_type="application/json",
        ).json()["id"]
        resp = self.client.patch(
            reverse("lists_detail", kwargs={"id": list_id}),
            {"folder_id": "01AA" + "A" * 22},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_rows_append_and_keyset_page(self):
        list_id = self.client.post(
            reverse("lists_index"),
            {"label": "Rows", "columns": [{"key": "a", "label": "A", "type": "text"}]},
            content_type="application/json",
        ).json()["id"]
        added = self.client.post(
            reverse("lists_rows", kwargs={"id": list_id}),
            {"rows": [{"a": str(i)} for i in range(5)]},
            content_type="application/json",
        )
        self.assertEqual(added.status_code, 201)
        self.assertEqual(added.json(), {"added": 5, "row_count": 5})

        first = self.client.get(reverse("lists_rows", kwargs={"id": list_id}), {"limit": 2}).json()
        self.assertEqual([r["data"]["a"] for r in first["items"]], ["0", "1"])
        # The cursor is opaque and self-contained (the last row's rank
        # and id): nothing about order rides a row, and the next page
        # needs no lookup.
        self.assertTrue(first["next_cursor"].endswith("." + first["items"][-1]["id"]))
        self.assertNotIn("position", first["items"][0])
        rest = self.client.get(
            reverse("lists_rows", kwargs={"id": list_id}), {"limit": 5, "after": first["next_cursor"]}
        ).json()
        self.assertEqual([r["data"]["a"] for r in rest["items"]], ["2", "3", "4"])
        self.assertIsNone(rest["next_cursor"])
        row_id = first["items"][0]["id"]
        bad_ranks = ("", "zz", "a\x00", "a1!", "a" * 100)
        for bad in ("01ROW" + "0" * 21, "a0.nope", "not a cursor", *(f"{r}.{row_id}" for r in bad_ranks)):
            with self.subTest(bad=bad):
                resp = self.client.get(reverse("lists_rows", kwargs={"id": list_id}), {"after": bad})
                self.assertEqual(resp.status_code, 400)

    def test_foreign_list_is_404(self):
        foreign = ListService(account_id="01AC" + "Z" * 22).create(
            owner_id="01US" + "Z" * 22, label="Not yours", columns=[], origin=ListOrigin.MANUAL
        )
        self.assertEqual(self.client.get(reverse("lists_detail", kwargs={"id": str(foreign.id)})).status_code, 404)

    def test_csv_import_upload(self):
        upload = io.BytesIO(b"Website,Name\nacme.com,Acme\ninitech.com,Initech\n")
        upload.name = "prospects.csv"
        resp = self.client.post(reverse("lists_import"), {"file": upload})
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["rows"], 2)
        self.assertEqual(body["list"]["label"], "prospects")  # filename is the default label
        self.assertEqual(body["list"]["origin"], "csv")
        self.assertEqual(List.objects.get(id=body["list"]["id"]).row_count, 2)

    def test_import_rejects_junk(self):
        upload = io.BytesIO(b"\xff\xfe nope")
        upload.name = "junk.csv"
        self.assertEqual(self.client.post(reverse("lists_import"), {"file": upload}).status_code, 400)
        self.assertEqual(self.client.post(reverse("lists_import"), {}).status_code, 400)


class FoldersViewsTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_folder_index_carries_server_side_counts(self):
        folder_id = self.client.post(
            reverse("lists_folders"), {"label": "Accounts"}, content_type="application/json"
        ).json()["id"]
        for n in range(3):
            list_id = self.client.post(
                reverse("lists_index"),
                {"label": f"Sheet {n}", "columns": [{"key": "a", "label": "A", "type": "text"}]},
                content_type="application/json",
            ).json()["id"]
            self.client.patch(
                reverse("lists_detail", kwargs={"id": list_id}),
                {"folder_id": folder_id},
                content_type="application/json",
            )
        items = self.client.get(reverse("lists_folders")).json()["items"]
        self.assertEqual([f["list_count"] for f in items if f["id"] == folder_id], [3])


class RowViewTests(TestCase):
    """PATCH and DELETE /v1/lists/{id}/rows/{row_id}: a person's cell
    writes and a row's removal."""

    def setUp(self) -> None:
        login_session(self.client)
        self.list_id = self.client.post(
            reverse("lists_index"),
            {
                "label": "Rows",
                "columns": [
                    {"key": "company", "label": "Company", "type": "text"},
                    {"key": "employees", "label": "Employees", "type": "number"},
                ],
            },
            content_type="application/json",
        ).json()["id"]
        self.client.post(
            reverse("lists_rows", kwargs={"id": self.list_id}),
            {"rows": [{"company": "acme.com", "employees": "10"}, {"company": "example.io"}]},
            content_type="application/json",
        )
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        self.first_id, self.second_id = [row["id"] for row in page["items"]]

    def row_url(self, row_id: str) -> str:
        return reverse("lists_row", kwargs={"id": self.list_id, "row_id": row_id})

    def patch(self, row_id: str, data: dict):
        return self.client.patch(self.row_url(row_id), {"data": data}, content_type="application/json")

    def test_patch_writes_the_cells_and_answers_the_row(self):
        resp = self.patch(self.first_id, {"company": "Acme Holdings"})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["id"], self.first_id)
        self.assertEqual(body["data"], {"company": "Acme Holdings", "employees": "10"})
        self.assertEqual(body["states"], {})
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        self.assertEqual(page["items"][0]["data"]["company"], "Acme Holdings")

    def test_patch_refusals_carry_a_code_and_a_sentence_and_write_nothing(self):
        mismatch = self.patch(self.first_id, {"employees": "lots"})
        self.assertEqual(mismatch.status_code, 400, mismatch.content)
        self.assertEqual(mismatch.json()["error"], "cell_type_mismatch")
        self.assertIn("Employees", mismatch.json()["detail"])
        blank = self.patch(self.first_id, {"company": "  "})
        self.assertEqual(blank.status_code, 400, blank.content)
        self.assertEqual(blank.json()["error"], "cell_blank")
        unknown = self.patch(self.first_id, {"revenue": "1"})
        self.assertEqual(unknown.status_code, 400, unknown.content)
        self.assertEqual(unknown.json()["error"], "cell_column_unknown")
        self.assertIn("revenue", unknown.json()["detail"])
        self.assertEqual(self.patch("01ROW" + "0" * 21, {"company": "x"}).status_code, 404)
        self.assertEqual(self.patch(self.first_id, {}).status_code, 400)
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        self.assertEqual(page["items"][0]["data"], {"company": "acme.com", "employees": "10"})

    def test_delete_rows_takes_the_named_rows_or_all_but_the_kept(self):
        third = self.client.post(
            reverse("lists_rows", kwargs={"id": self.list_id}),
            {"rows": [{"company": "third.io"}]},
            content_type="application/json",
        )
        self.assertEqual(third.status_code, 201, third.content)
        named = self.client.delete(
            reverse("lists_rows", kwargs={"id": self.list_id}),
            {"row_ids": [self.first_id, self.second_id, "01ROW" + "0" * 21]},
            content_type="application/json",
        )
        self.assertEqual(named.status_code, 200, named.content)
        self.assertEqual(named.json(), {"deleted": 2, "row_count": 1})
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        (kept,) = page["items"]
        self.assertEqual(kept["data"]["company"], "third.io")
        self.client.post(
            reverse("lists_rows", kwargs={"id": self.list_id}),
            {"rows": [{"company": "fourth.io"}, {"company": "fifth.io"}]},
            content_type="application/json",
        )
        everything_but = self.client.delete(
            reverse("lists_rows", kwargs={"id": self.list_id}),
            {"all": True, "keep": [kept["id"]]},
            content_type="application/json",
        )
        self.assertEqual(everything_but.status_code, 200, everything_but.content)
        self.assertEqual(everything_but.json(), {"deleted": 2, "row_count": 1})
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        self.assertEqual([row["id"] for row in page["items"]], [kept["id"]])

    def test_delete_rows_needs_exactly_one_of_named_or_all(self):
        for body in ({}, {"row_ids": [self.first_id], "all": True}, {"keep": [self.first_id]}, {"row_ids": []}):
            refused = self.client.delete(
                reverse("lists_rows", kwargs={"id": self.list_id}), body, content_type="application/json"
            )
            self.assertEqual(refused.status_code, 400, (body, refused.content))
        self.assertEqual(
            self.client.delete(
                reverse("lists_rows", kwargs={"id": "01LST" + "0" * 21}),
                {"all": True},
                content_type="application/json",
            ).status_code,
            404,
        )
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        self.assertEqual(len(page["items"]), 2)

    def test_delete_removes_the_row_and_recounts(self):
        gone = self.client.delete(self.row_url(self.first_id))
        self.assertEqual(gone.status_code, 204)
        page = self.client.get(reverse("lists_rows", kwargs={"id": self.list_id})).json()
        self.assertEqual([row["id"] for row in page["items"]], [self.second_id])
        detail = self.client.get(reverse("lists_detail", kwargs={"id": self.list_id})).json()
        self.assertEqual(detail["row_count"], 1)
        self.assertEqual(self.client.delete(self.row_url(self.first_id)).status_code, 404)
        self.assertEqual(self.client.delete(self.row_url("01ROW" + "0" * 21)).status_code, 404)
