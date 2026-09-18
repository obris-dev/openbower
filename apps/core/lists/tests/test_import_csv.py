"""CSV import: the pure parsing/typing helpers (no database) and the
operation end to end.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from lists.constants import MAX_LIST_COLUMNS, ColumnType
from lists.operations.import_csv import (
    CsvUnusable,
    ImportCsvOperation,
    column_key,
    infer_type,
    parse_csv,
)


class ParseCsvTests(SimpleTestCase):
    def test_header_becomes_schema_and_rows_key_on_it(self):
        columns, rows, skipped = parse_csv(b"Company Name,Website\nAcme,acme.com\nInitech,initech.com\n")
        self.assertEqual([c["key"] for c in columns], ["company_name", "website"])
        self.assertEqual(
            rows,
            [
                {"company_name": "Acme", "website": "acme.com"},
                {"company_name": "Initech", "website": "initech.com"},
            ],
        )
        self.assertEqual(skipped, 0)

    def test_short_rows_pad_and_wide_rows_skip(self):
        _columns, rows, skipped = parse_csv(b"a,b\n1\n1,2,3\n,\n4,5\n")
        self.assertEqual(rows, [{"a": "1", "b": ""}, {"a": "4", "b": "5"}])
        self.assertEqual(skipped, 2)  # the too-wide row and the blank row

    def test_duplicate_headers_get_unique_keys(self):
        columns, _, _ = parse_csv(b"Name,Name,name!\nx,y,z\n")
        self.assertEqual([c["key"] for c in columns], ["name", "name_2", "name_3"])

    def test_bom_and_empty_file(self):
        columns, _, _ = parse_csv("﻿a,b\n1,2\n".encode())
        self.assertEqual([c["key"] for c in columns], ["a", "b"])
        with self.assertRaises(CsvUnusable):
            parse_csv(b"")
        with self.assertRaises(CsvUnusable):
            parse_csv(b"\xff\xfe broken")

    def test_type_inference_is_display_only_and_decisive(self):
        self.assertEqual(infer_type(["acme.com", "https://www.initech.com/x", "globex.io"]), ColumnType.URL)
        self.assertEqual(infer_type(["1", "2,500", "-3.5"]), ColumnType.NUMBER)
        self.assertEqual(infer_type(["a@acme.com", "b@initech.com"]), ColumnType.EMAIL)
        # Mixed columns stay text: a wrong type renders wrong everywhere.
        self.assertEqual(infer_type(["acme.com", "not a domain", "hello", "12", "x"]), ColumnType.TEXT)
        self.assertEqual(infer_type([]), ColumnType.TEXT)

    def test_too_many_columns_refuses_with_the_cap_named(self):
        wide = ",".join(f"c{i}" for i in range(MAX_LIST_COLUMNS + 1))
        with self.assertRaises(CsvUnusable) as caught:
            parse_csv(f"{wide}\n{','.join('x' * (MAX_LIST_COLUMNS + 1))}\n".encode())
        self.assertIn(str(MAX_LIST_COLUMNS), str(caught.exception))

    def test_csv_module_refusals_read_as_unusable(self):
        # A field over the csv module's 128KiB limit raises csv.Error
        # mid-iteration (well inside our 5MB byte cap).
        huge_field = "x" * 200_000
        with self.assertRaises(CsvUnusable):
            parse_csv(f"a,b\n{huge_field},2\n".encode())

    def test_column_key_stability(self):
        self.assertEqual(column_key("Company Name!", taken=set()), "company_name")
        self.assertEqual(column_key("", taken=set()), "column")


class ImportCsvOperationTests(TestCase):
    def test_import_builds_a_plain_sheet(self):
        stats = ImportCsvOperation(
            account_id="01AC" + "A" * 22,
            user_id="01US" + "A" * 22,
            label="Imported",
            raw=b"Website,Name\nacme.com,Acme\n,Blankless\ninitech.com,Initech\n",
        ).run()
        self.assertEqual(stats.rows, 3)  # a row with SOME data imports; blanks pad
        self.assertEqual(stats.skipped, 0)
        self.assertEqual(stats.target.row_count, 3)
        self.assertEqual(stats.target.origin, "csv")
        types = {c.key: c.type for c in stats.target.columns}
        self.assertEqual(types["website"], "url")
        self.assertEqual(types["name"], "text")
