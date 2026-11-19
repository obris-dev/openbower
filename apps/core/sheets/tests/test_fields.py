"""The typed JSON fields: Sheet.columns is SheetColumn values (one kind
each) and a cell record's detail a CellDetail (or None), on load and on
assignment, their dumps at rest, and each field a plain JSONField to
migrations.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_fields
"""

from __future__ import annotations

import json
from typing import get_args

from django.db import connection
from django.test import SimpleTestCase, TestCase
from pydantic import ValidationError

from openbower_schema.sheets import AiColumn, ColumnBase, PlainColumn, SheetColumn, WebhookColumn, WorkflowColumn

from ..cells import AgentCellDetail
from ..fields import CellDetailField, SheetColumnsField
from ..models import Sheet, SheetCellState
from ..services.sheets import SheetService
from .test_fill_admission import ACCOUNT, USER

NODE_ID = "01NODE" + "A" * 20


class SheetColumnsFieldTests(TestCase):
    def setUp(self) -> None:
        self.sheets = SheetService(account_id=ACCOUNT)

    def test_columns_are_typed_on_assignment_load_and_reload(self) -> None:
        sheet = self.sheets.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        # Typed the moment a caller assigns dicts, not only after a reload.
        self.assertEqual(sheet.columns, [PlainColumn(key="company", label="Company", type="text")])
        sheet.columns = [
            *sheet.columns,
            {"kind": "ai", "key": "answer", "label": "Answer", "type": "text", "node_id": NODE_ID},
        ]
        self.assertEqual(sheet.columns[1], AiColumn(key="answer", label="Answer", type="text", node_id=NODE_ID))
        sheet.save(update_fields=["columns", "updated_at"])
        sheet.refresh_from_db()
        self.assertEqual([column.key for column in sheet.columns], ["company", "answer"])
        self.assertIsInstance(sheet.columns[1], AiColumn)

    def test_at_rest_the_column_holds_the_contract_dumps(self) -> None:
        sheet = self.sheets.create(owner_id=USER, label="Prospects", columns=[], origin="manual")
        sheet.columns = [
            PlainColumn(key="company", label="Company", type="text"),
            AiColumn(key="answer", label="Answer", type="text", node_id=NODE_ID, current_fill_id="01RUN" + "A" * 21),
            WebhookColumn(key="crm_sync", label="CRM sync", type="text", node_id="01HOOK" + "A" * 20),
        ]
        sheet.save(update_fields=["columns", "updated_at"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT columns FROM sheets_sheet WHERE id = %s", [str(sheet.id)])
            [(stored,)] = cursor.fetchall()
        # A raw cursor hands the jsonb back undecoded.
        self.assertEqual(
            json.loads(stored) if isinstance(stored, str) else stored,
            [
                {"kind": "plain", "key": "company", "label": "Company", "type": "text", "immutable": False},
                {
                    "kind": "ai",
                    "key": "answer",
                    "label": "Answer",
                    "type": "text",
                    "node_id": NODE_ID,
                    "current_fill_id": "01RUN" + "A" * 21,
                    "immutable": False,
                },
                {
                    "kind": "webhook",
                    "key": "crm_sync",
                    "immutable": False,
                    "label": "CRM sync",
                    "type": "text",
                    "node_id": "01HOOK" + "A" * 20,
                },
            ],
        )

    def test_a_column_the_contract_refuses_fails_at_assignment(self) -> None:
        sheet = Sheet(account_id=ACCOUNT, user_id=USER, label="Prospects", origin="manual")
        for bad in (
            {"kind": "plain", "key": "company", "label": "Company", "type": "not_a_type"},
            {"kind": "plain", "label": "Company", "type": "text"},
            {"kind": "ai", "key": "answer", "label": "Answer", "type": "text"},
            {"key": "company", "label": "Company", "type": "text"},
            {"kind": "formula", "key": "company", "label": "Company", "type": "text"},
            # The bases alone are not columns: no kind to store.
            ColumnBase(key="company", label="Company", type="text"),
            WorkflowColumn(key="answer", label="Answer", type="text", node_id=NODE_ID),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                sheet.columns = [bad]

    def test_a_stored_column_with_no_kind_is_refused_on_read(self) -> None:
        """A row from before columns carried a kind fails the read that
        meets it (a values read as much as a model load) rather than
        being inferred into a kind."""
        sheet = self.sheets.create(owner_id=USER, label="Prospects", columns=[], origin="manual")
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sheets_sheet SET columns = %s WHERE id = %s",
                [json.dumps([{"key": "company", "label": "Company", "type": "text"}]), str(sheet.id)],
            )
        with self.assertRaises(ValidationError):
            Sheet.objects.get(id=str(sheet.id))
        with self.assertRaises(ValidationError):
            list(Sheet.objects.filter(id=str(sheet.id)).values_list("columns", flat=True))

    def test_a_values_read_is_typed_without_the_descriptor(self) -> None:
        sheet = self.sheets.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        [columns] = Sheet.objects.filter(id=str(sheet.id)).values_list("columns", flat=True)
        self.assertEqual(columns, [PlainColumn(key="company", label="Company", type="text")])

    def test_the_field_migrates_as_a_plain_json_field(self) -> None:
        field = Sheet._meta.get_field("columns")
        self.assertIsInstance(field, SheetColumnsField)
        _name, path, _args, _kwargs = field.deconstruct()
        self.assertEqual(path, "django.db.models.JSONField")


class CellDetailFieldTests(TestCase):
    DETAIL = AgentCellDetail(confidence=0.62, reason="why", unconfirmed="Acme Holdings")

    def _record(self, detail) -> SheetCellState:
        return SheetCellState.objects.create(
            account_id=ACCOUNT, sheet_id="S" * 26, row_id="R" * 26, column_key="answer", detail=detail
        )

    def test_a_detail_is_typed_on_reload_and_its_dump_at_rest(self) -> None:
        record = self._record(self.DETAIL.model_dump())
        self.assertEqual(record.detail, self.DETAIL)
        record.refresh_from_db()
        self.assertEqual(record.detail, self.DETAIL)
        with connection.cursor() as cursor:
            cursor.execute("SELECT detail FROM sheets_sheetcellstate WHERE id = %s", [str(record.id)])
            [raw] = cursor.fetchone()
        self.assertEqual(json.loads(raw) if isinstance(raw, str) else raw, self.DETAIL.model_dump())

    def test_none_stays_none(self) -> None:
        record = self._record(None)
        record.refresh_from_db()
        self.assertIsNone(record.detail)

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self._record({"kind": "unheard_of", "confidence": 0.5})

    def test_a_key_read_returns_the_fragment(self) -> None:
        from django.db.models.fields.json import KeyTextTransform

        self._record(self.DETAIL)
        [value] = SheetCellState.objects.annotate(u=KeyTextTransform("unconfirmed", "detail")).values_list(
            "u", flat=True
        )
        self.assertEqual(value, "Acme Holdings")

    def test_the_field_migrates_as_a_plain_json_field(self) -> None:
        field = SheetCellState._meta.get_field("detail")
        self.assertIsInstance(field, CellDetailField)
        self.assertEqual(field.deconstruct()[1], "django.db.models.JSONField")


class WorkflowColumnTests(SimpleTestCase):
    def test_a_column_kind_bound_to_a_node_is_a_workflow_column(self) -> None:
        # Both directions: a kind that declares its own node_id instead
        # of extending the base would slip past every reader that asks
        # isinstance(column, WorkflowColumn).
        union, _ = get_args(SheetColumn.__value__)
        members = get_args(union)
        self.assertEqual(
            [member for member in members if issubclass(member, WorkflowColumn)], [AiColumn, WebhookColumn]
        )
        for member in members:
            with self.subTest(member=member.__name__):
                self.assertEqual("node_id" in member.model_fields, issubclass(member, WorkflowColumn))
