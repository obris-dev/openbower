"""POST /v1/lists/{id}/columns (the blank-column add) through real
cookie auth (the IdP mocked at its httpx boundary via the shared login
helper); responses validate back through the contract models (the
parity idiom).

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_columns
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from openbower_schema.lists import ListSummary

from ..constants import MAX_LIST_COLUMNS
from ..services.lists import ListService


class ColumnsViewTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        self.sheet = self.lists.create(
            label="Prospects", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )

    def post_column(self, list_id: str = "", **overrides):
        body: dict = {"label": "Contact Email", "type": "email"}
        body.update(overrides)
        return self.client.post(
            reverse("lists_columns", kwargs={"id": list_id or str(self.sheet.id)}),
            body,
            content_type="application/json",
        )

    def test_appends_the_blank_column_with_the_derived_key(self) -> None:
        resp = self.post_column()
        self.assertEqual(resp.status_code, 200, resp.content)
        wire = ListSummary(**resp.json())
        self.assertEqual(wire.id, str(self.sheet.id))
        added = wire.columns[-1]
        self.assertEqual(added.key, "contact_email")
        self.assertEqual(added.label, "Contact Email")
        self.assertEqual(added.type, "email")
        # Blank means blank: no fill member, ever (adoption points a
        # later fill at this column; the add itself starts nothing).
        self.assertIsNone(added.fill)
        self.sheet.refresh_from_db()
        self.assertEqual([column["key"] for column in self.sheet.columns], ["company", "contact_email"])

    def test_duplicate_key_refuses_with_the_envelope(self) -> None:
        resp = self.post_column(label="Company!", type="text")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "column_exists", "detail": "A column named company already exists."})

    def test_reserved_and_empty_derived_keys_refuse(self) -> None:
        resp = self.post_column(label="Model Config", type="text")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "reserved_key")
        self.assertIn("reserved column key", body["detail"])
        self.assertEqual(self.post_column(label="!!!", type="text").json()["error"], "reserved_key")

    def test_column_cap_refuses(self) -> None:
        self.sheet.columns = [{"key": f"col_{n}", "label": f"Col {n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)]
        self.sheet.save(update_fields=["columns"])
        resp = self.post_column()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"error": "columns_full", "detail": f"A sheet holds at most {MAX_LIST_COLUMNS} columns."},
        )

    def test_foreign_list_reads_as_missing(self) -> None:
        foreign = ListService(account_id="01AC" + "Z" * 22, user_id="01US" + "Z" * 22).create(
            label="Not yours", columns=[], origin="manual"
        )
        self.assertEqual(self.post_column(list_id=str(foreign.id)).status_code, 404)

    def test_blank_label_and_unknown_type_fail_validation(self) -> None:
        self.assertEqual(self.post_column(label="   ").status_code, 400)
        self.assertEqual(self.post_column(type="picture").status_code, 400)
