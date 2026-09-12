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

    def test_ingest_get_returns_the_hard_columns_only(self):
        # The webhook is self-describing: GET returns the columns a producer
        # fills (key + type), and OMITS AI columns (fill-owned) so a push
        # never sends what autofill will.
        lst = ListService(account_id=TEST_IDENTITY["account_id"]).create(
            owner_id=TEST_IDENTITY["id"],
            label="Push target",
            columns=[
                {"key": "company", "label": "Company", "type": "url"},
                {"key": "contact", "label": "Contact", "type": "text"},
                {"key": "answer", "label": "Answer", "type": "text", "fill": {"agent_id": "01AG" + "A" * 22}},
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
        self.assertEqual([r["position"] for r in first["items"]], [1, 2])
        self.assertEqual(first["next_cursor"], "2")
        rest = self.client.get(
            reverse("lists_rows", kwargs={"id": list_id}), {"limit": 5, "after": first["next_cursor"]}
        ).json()
        self.assertEqual([r["position"] for r in rest["items"]], [3, 4, 5])
        self.assertIsNone(rest["next_cursor"])

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
