"""The label-to-key derivation, pinned by vector.

Every path that puts a column on a sheet goes through this one
function: a CSV header, a blank column add, an agent output a fill
maps down. It matters because a fill decides what an output key MEANS
by matching it against the sheet's keys, so a second rule anywhere
does not produce a second key, it changes which column an output is
judged against. What that match then does is
FillAdmissionService._resolve_columns's rule, not this module's.

THE SAME VECTORS live in the web's mirror,
web/apps/app/src/app/(app)/_components/agent-config/lib/output-key.test.ts.
Changing one list without the other is the drift these exist to catch.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from django.test import SimpleTestCase

from openbower_schema.lists import COLUMN_KEY_MAX_LENGTH, derive_column_key

# (label, key) pairs. Keep in lockstep with the TS file above.
VECTORS = [
    ("Contact Email", "contact_email"),
    ("LinkedIn URL!", "linkedin_url"),
    ("  spaced  out  ", "spaced_out"),
    ("!!!", ""),
    ("ÜBER Größe", "ber_gr_e"),
    ("Revenue ($)", "revenue"),
    ("2024 ARR", "2024_arr"),
    ("--leading--", "leading"),
    ("Ünïcode Ñame", "n_code_ame"),
    ("MiXeD CaSe", "mixed_case"),
    # Turkish dotted capital: both runtimes lowercase it to an i plus a
    # combining dot, and the dot is not [a-z0-9], so both split it.
    ("İstanbul", "i_stanbul"),
    ("café", "caf"),
]


class ColumnKeyTests(SimpleTestCase):
    def test_vectors(self):
        for label, expected in VECTORS:
            with self.subTest(label=label):
                self.assertEqual(derive_column_key(label), expected)

    def test_an_explicit_key_wins_over_the_label(self):
        self.assertEqual(derive_column_key("Ignored Label", key="person"), "person")

    def test_the_key_clamps_to_the_column_cap(self):
        # Outputs land as sheet columns, so a wider key here would
        # truncate at the mapping seam instead of at the source.
        self.assertEqual(len(derive_column_key("a" * (COLUMN_KEY_MAX_LENGTH * 2))), COLUMN_KEY_MAX_LENGTH)

    def test_a_csv_header_and_an_output_label_derive_the_same_key(self):
        # Import a sheet with a "Contact Email" header, then add an
        # AI output labelled the same: both must derive one key, so the
        # fill judges its output against THAT column (and, under the
        # existence rule, refuses) rather than minting a sibling.
        from lists.operations.import_csv import column_key

        self.assertEqual(column_key("Contact Email", taken=set()), derive_column_key("Contact Email"))


class ShapeHintTests(SimpleTestCase):
    """A shape we REFUSE has to be a shape we asked for. These pin the
    pairing: every type carrying a validator carries a hint, and every
    type without one says nothing."""

    def test_every_shape_ruled_type_tells_the_model_its_shape(self):
        from openbower_schema.cell_types import _VALIDATORS, shape_hint

        for column_type in _VALIDATORS:
            with self.subTest(column_type=column_type):
                self.assertTrue(shape_hint(column_type), f"{column_type} refuses shapes it never asked for")

    def test_pass_through_types_invent_no_rule(self):
        from openbower_schema.cell_types import _VALIDATORS, shape_hint

        from ..constants import ColumnType

        for column_type in ColumnType:
            if column_type not in _VALIDATORS:
                with self.subTest(column_type=column_type):
                    self.assertEqual(shape_hint(column_type), "")

    def test_the_hint_reaches_the_model_beside_the_authored_description(self):
        from agents.runtime.answer.schema import construct_answer_schema
        from openbower_schema.agents import AgentOutput

        answer_type = construct_answer_schema(
            [AgentOutput(key="founded", label="Founded", type="date", description="Year founded")]
        )
        described = answer_type.model_json_schema()["properties"]["founded"]["description"]
        self.assertIn("Year founded", described)
        self.assertIn("YYYY-MM-DD", described)
