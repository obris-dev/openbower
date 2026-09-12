"""The 0011 backfill: existing tasks must denormalize their list, off
the Fill (fill-backed) or the row's ListRow (autofill), so per-list
distribution never has to join back to find it. A backfill that
silently matched nothing would ship the column all-blank.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from importlib import import_module

from django.apps import apps as live_apps
from django.test import TestCase

from common.testing import TEST_IDENTITY

from ..constants import FillKind
from ..models import Fill, FillTask, ListRow

_migration = import_module("lists.migrations.0011_filltask_list_id")


class ListIdBackfillTests(TestCase):
    """Exercises the RunPython body against the live models (the
    historical models share the fields it touches)."""

    def test_backfill_reads_the_list_off_the_fill_or_the_row(self):
        account = TEST_IDENTITY["account_id"]
        user = TEST_IDENTITY["id"]
        list_a = "01LISTA" + "0" * 19
        list_b = "01LISTB" + "0" * 19

        # Fill-backed task: its list comes off its Fill.
        fill = Fill.objects.create(
            account_id=account,
            user_id=user,
            list_id=list_a,
            agent_id="",
            column_keys=["c"],
            config_snapshot={},
            confirmed_row_count=1,
        )
        backed = FillTask.objects.create(account_id=account, fill_run_id=str(fill.id), row_id="01ROW" + "0" * 21)
        # Autofill task: no Fill, its list comes off its row's ListRow.
        row = ListRow.objects.create(list_id=list_b, position=1)
        auto = FillTask.objects.create(
            account_id=account, fill_run_id=None, agent_id="01AGENT" + "0" * 19, row_id=str(row.id)
        )
        # A bench TEST fill (list_id "") leaves its task blank.
        bench_fill = Fill.objects.create(
            account_id=account,
            user_id=user,
            kind=FillKind.TEST,
            list_id="",
            agent_id="",
            column_keys=["c"],
            config_snapshot={},
            confirmed_row_count=1,
        )
        bench = FillTask.objects.create(account_id=account, fill_run_id=str(bench_fill.id), row_id="01ROW" + "9" * 21)

        # All three start blank (the pre-migration state).
        self.assertEqual({backed.list_id, auto.list_id, bench.list_id}, {""})

        _migration.backfill_list_id(live_apps, None)

        for task in (backed, auto, bench):
            task.refresh_from_db()
        self.assertEqual(backed.list_id, list_a)
        self.assertEqual(auto.list_id, list_b)
        self.assertEqual(bench.list_id, "")  # a bench fill's task stays blank, never guessed
