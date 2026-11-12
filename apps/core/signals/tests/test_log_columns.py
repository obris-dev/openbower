"""A log's base columns and the cells a match fills them with: every base
column is filled, each value fits its column's type (the append stores
it canonically with no mismatch), and what the article left out is blank.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_log_columns
"""

from __future__ import annotations

from django.test import SimpleTestCase

from openbower_schema import MatchWire
from sheets.services.sheets import cells_for_storage
from signals.log_columns import FUNDING_COLUMNS, LOG_SHAPES, funding_cells

_AT = "2026-11-09T16:29:02.062784+00:00"


def _match(**data_over) -> MatchWire:
    data = {
        "company": {"name": "Acme Robotics", "domain": "acme.example", "hq": "Austin, Texas", "description": "Robots."},
        "amount": 25_000_000.0,
        "currency": "USD",
        "stage": "series_a",
        "instruments": ["equity"],
        "lead_investors": ["Example Ventures"],
        "other_investors": ["Sample Capital", "Demo Partners"],
    }
    data.update(data_over)
    return MatchWire.model_validate(
        {
            "id": "01JQ" + "M" * 22,
            "version_id": "01JQ" + "V" * 22,
            "confidence": 0.93,
            "reason": "A US robotics company raising a Series A.",
            "judged_at": _AT,
            "event": {
                "id": "01JQ" + "E" * 22,
                "signal": "funding",
                "data": data,
                "logged_at": _AT,
                "source": {
                    "title": "Acme Robotics Raises $25 Million Series A",
                    "summary": "Acme Robotics announced a $25 million Series A.",
                    "url": "https://news.example.com/acme-series-a",
                    "published": _AT,
                },
            },
        }
    )


class FundingCellsTests(SimpleTestCase):
    def test_a_match_fills_every_base_column(self):
        cells = funding_cells(_match())
        self.assertEqual(set(cells), {column.key for column in FUNDING_COLUMNS})
        self.assertEqual(
            cells,
            {
                "company": "Acme Robotics",
                "domain": "acme.example",
                "stage": "series_a",
                "amount": "25000000",
                "currency": "USD",
                # FAILS if investors are dropped or joined some other way.
                "lead_investors": "Example Ventures",
                "other_investors": "Sample Capital, Demo Partners",
                "hq": "Austin, Texas",
                "source": "https://news.example.com/acme-series-a",
                "published": "2026-11-09",
                "confidence": "0.93",
                "reason": "A US robotics company raising a Series A.",
            },
        )

    def test_every_value_fits_its_column_type(self):
        # What the append stores: no value the type would refuse.
        types = {column.key: column.type for column in FUNDING_COLUMNS}
        _stored, mismatches = cells_for_storage(types, funding_cells(_match()), where="test")
        self.assertEqual(mismatches, [])

    def test_what_the_article_left_out_is_blank(self):
        cells = funding_cells(
            _match(
                company={"name": "Acme", "domain": None, "hq": None, "description": None},
                amount=None,
                currency=None,
                stage=None,
                lead_investors=[],
                other_investors=[],
            )
        )
        blank = {key for key, value in cells.items() if value == ""}
        self.assertEqual(blank, {"domain", "amount", "currency", "stage", "lead_investors", "other_investors", "hq"})

    def test_the_base_columns_are_immutable_and_keyed_by_signal(self):
        self.assertTrue(all(column.immutable for column in FUNDING_COLUMNS))
        self.assertEqual(len(FUNDING_COLUMNS), 12)
        self.assertEqual(LOG_SHAPES["funding"].columns, FUNDING_COLUMNS)
