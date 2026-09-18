"""The typed column array: List.columns is ListColumn values on load
and on assignment, their dumps at rest, and the field is a plain
JSONField to migrations.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_fields
"""

from __future__ import annotations

import json

from django.db import connection
from django.test import TestCase
from pydantic import ValidationError

from openbower_schema.lists import ColumnFill, ListColumn

from ..fields import ListColumnsField
from ..models import List
from ..services.lists import ListService
from .test_fill_admission import ACCOUNT, USER

NODE_ID = "01NODE" + "A" * 20


class ListColumnsFieldTests(TestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)

    def test_columns_are_typed_on_assignment_load_and_reload(self) -> None:
        sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        # Typed the moment a caller assigns dicts, not only after a reload.
        self.assertEqual(sheet.columns, [ListColumn(key="company", label="Company", type="text")])
        sheet.columns = [
            *sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "fill": {"node_id": NODE_ID}},
        ]
        self.assertEqual(sheet.columns[1].fill, ColumnFill(node_id=NODE_ID))
        sheet.save(update_fields=["columns", "updated_at"])
        sheet.refresh_from_db()
        self.assertEqual([column.key for column in sheet.columns], ["company", "answer"])
        self.assertIsInstance(sheet.columns[1].fill, ColumnFill)

    def test_at_rest_the_column_holds_the_contract_dumps(self) -> None:
        sheet = self.lists.create(owner_id=USER, label="Prospects", columns=[], origin="manual")
        sheet.columns = [ListColumn(key="company", label="Company", type="text")]
        sheet.save(update_fields=["columns", "updated_at"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT columns FROM lists_list WHERE id = %s", [str(sheet.id)])
            [(stored,)] = cursor.fetchall()
        # A raw cursor hands the jsonb back undecoded.
        self.assertEqual(
            json.loads(stored) if isinstance(stored, str) else stored,
            [{"key": "company", "label": "Company", "type": "text", "fill": None, "webhook": None}],
        )

    def test_a_column_the_contract_refuses_fails_at_assignment(self) -> None:
        sheet = List(account_id=ACCOUNT, user_id=USER, label="Prospects", origin="manual")
        with self.assertRaises(ValidationError):
            sheet.columns = [{"key": "company", "label": "Company", "type": "not_a_type"}]
        with self.assertRaises(ValidationError):
            sheet.columns = [{"label": "Company", "type": "text"}]

    def test_the_field_migrates_as_a_plain_json_field(self) -> None:
        field = List._meta.get_field("columns")
        self.assertIsInstance(field, ListColumnsField)
        _name, path, _args, _kwargs = field.deconstruct()
        self.assertEqual(path, "django.db.models.JSONField")
