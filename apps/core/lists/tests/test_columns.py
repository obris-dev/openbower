"""The sheet's column writes, POST /v1/lists/{id}/columns (the
blank-column add) and PATCH /v1/lists/{id}/column-order (the reorder),
through real cookie auth (the IdP mocked at its httpx boundary via the shared login
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
from ..services.columns import ColumnKeysNotUnique, ColumnOrderStale, ColumnService
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
        # Blank means blank: no fill member, ever. The add starts
        # nothing, and a column that exists refuses an AI column that
        # would land on the same key.
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


class ColumnOrderTests(TestCase):
    """PATCH /v1/lists/{id}/column-order: the one columns write that
    may only decide WHERE a column sits."""

    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        self.sheet = self.lists.create(
            label="Prospects",
            # Labels deliberately NOT derivable from their keys, and
            # one carrying a fill member: this is what proves the
            # column dicts are carried across rather than rebuilt.
            columns=[
                {"key": "company", "label": "Company Name", "type": "text"},
                {"key": "contact", "label": "Primary Contact", "type": "email", "fill": {"agent_id": "01AGENT"}},
                {"key": "notes", "label": "Free Notes", "type": "text"},
            ],
            origin="manual",
        )

    def reorder(self, keys, list_id: str = ""):
        return self.client.patch(
            reverse("lists_columns_order", kwargs={"id": list_id or str(self.sheet.id)}),
            {"keys": keys},
            content_type="application/json",
        )

    def test_reorders_and_echoes_the_new_order(self) -> None:
        resp = self.reorder(["notes", "company", "contact"])
        self.assertEqual(resp.status_code, 200, resp.content)
        wire = ListSummary(**resp.json())
        self.assertEqual([c.key for c in wire.columns], ["notes", "company", "contact"])
        self.sheet.refresh_from_db()
        self.assertEqual([c["key"] for c in self.sheet.columns], ["notes", "company", "contact"])

    def test_it_carries_each_column_across_verbatim(self) -> None:
        # The guard that keeps this from being a mutation door: the
        # request names keys and nothing else, so a label, a type, or a
        # fill member cannot be edited through an ordering request.
        before = {c["key"]: dict(c) for c in self.sheet.columns}
        self.assertEqual(self.reorder(["notes", "contact", "company"]).status_code, 200)
        self.sheet.refresh_from_db()
        self.assertEqual({c["key"]: dict(c) for c in self.sheet.columns}, before)

    def test_a_missing_key_refuses(self) -> None:
        resp = self.reorder(["company", "contact"])
        self.assertEqual(resp.status_code, 409, resp.content)
        # The WHOLE envelope, like the sibling refusal tests: `detail`
        # is tier-1 copy the client renders verbatim, so a rewrite of
        # it is a user-visible change and has to be deliberate.
        self.assertEqual(
            resp.json(),
            {
                "error": "column_order_stale",
                "detail": "This sheet's columns changed while you were reordering; try the move again.",
            },
        )

    def test_an_unknown_key_refuses(self) -> None:
        resp = self.reorder(["company", "contact", "invented"])
        self.assertEqual(resp.status_code, 409, resp.content)

    def test_a_duplicate_key_is_a_bad_request_not_a_conflict(self) -> None:
        # A repeat is the request being wrong, and no change to the
        # world makes it right, so it must not borrow the 409's "try
        # again" copy.
        resp = self.reorder(["company", "contact", "notes", "notes"])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.sheet.refresh_from_db()
        self.assertEqual([c["key"] for c in self.sheet.columns], ["company", "contact", "notes"])

    def test_a_duplicate_that_also_drops_a_key_is_a_bad_request(self) -> None:
        resp = self.reorder(["company", "company", "contact"])
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_the_service_names_a_repeat_and_a_stale_set_differently(self) -> None:
        # One rule in one place: the wire and a direct caller get the
        # same verdict, and the two refusals stay distinct because they
        # owe the caller different answers.
        columns = ColumnService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        with self.assertRaises(ColumnKeysNotUnique):
            columns.reorder(str(self.sheet.id), keys=["company", "contact", "notes", "notes"])
        with self.assertRaises(ColumnKeysNotUnique):
            columns.reorder(str(self.sheet.id), keys=["company", "company", "contact"])
        with self.assertRaises(ColumnOrderStale):
            columns.reorder(str(self.sheet.id), keys=["company", "contact", "gone"])
        self.sheet.refresh_from_db()
        self.assertEqual([c["key"] for c in self.sheet.columns], ["company", "contact", "notes"])

    def test_a_key_no_column_could_have_is_a_bad_request(self) -> None:
        # The round-trip that was answering "try the move again" to a
        # request that could never succeed: a key outside the derived
        # shape names no column that can exist.
        for bad in ("Notes", "no tes", "../etc"):
            with self.subTest(key=bad):
                resp = self.reorder(["company", bad])
                self.assertEqual(resp.status_code, 400, resp.content)
        self.sheet.refresh_from_db()
        self.assertEqual([c["key"] for c in self.sheet.columns], ["company", "contact", "notes"])

    def test_a_foreign_sheet_reads_as_missing(self) -> None:
        other = ListService(account_id="01OTHERACCOUNTBBBBBBBBBBBB", user_id="01OTHERUSERBBBBBBBBBBBBBBB").create(
            label="Theirs", columns=[{"key": "a", "label": "A", "type": "text"}], origin="manual"
        )
        self.assertEqual(self.reorder(["a"], list_id=str(other.id)).status_code, 404)
