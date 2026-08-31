"""The 0003 remap: rows written under the retired cell-state words
must keep reading after the vocabulary changed.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from importlib import import_module

from django.apps import apps as live_apps
from django.test import TestCase

from common.testing import TEST_IDENTITY

from ..constants import StoredCellState
from ..models import Fill, FillCellState, FillTask

_migration = import_module("lists.migrations.0003_cell_state_tools")


class RetiredStateRemapTests(TestCase):
    """Exercises the RunPython body against the live models (the
    historical models share the fields it touches), because a remap
    that silently matches nothing leaves the one stale row that 500s
    its sheet's rows page forever."""

    def test_retired_words_remap_and_current_words_survive(self):
        account = TEST_IDENTITY["account_id"]
        rows = {
            "01ROW" + "0" * 21: "search_throttled",
            "01ROW" + "1" * 21: "contacts_throttled",
            "01ROW" + "2" * 21: "no_tools_door",
            "01ROW" + "3" * 21: StoredCellState.FILLED,
        }
        for row_id, state in rows.items():
            FillCellState.objects.create(
                account_id=account, list_id="01LIST" + "0" * 20, row_id=row_id, column_key="contact", state=state
            )

        _migration._remap_retired_states(live_apps, None)

        states = {c.row_id: c.state for c in FillCellState.objects.all()}
        self.assertEqual(states["01ROW" + "0" * 21], StoredCellState.TOOL_UNAVAILABLE)
        self.assertEqual(states["01ROW" + "1" * 21], StoredCellState.TOOL_UNAVAILABLE)
        self.assertEqual(states["01ROW" + "2" * 21], StoredCellState.TOOL_NOT_CONFIGURED)
        self.assertEqual(states["01ROW" + "3" * 21], StoredCellState.FILLED)

    def test_retired_causes_in_stored_task_results_remap_too(self):
        # blank_cause and declined_cause ride the task's result blob,
        # read back by the drawer and the give-up path; a task with no
        # result (never claimed) must pass through untouched.
        account = TEST_IDENTITY["account_id"]
        fill = Fill.objects.create(
            account_id=account,
            user_id=TEST_IDENTITY["id"],
            list_id="01LIST" + "0" * 20,
            agent_id="01AGENT" + "0" * 19,
            column_keys=["contact"],
            config_snapshot={},
            confirmed_row_count=3,
        )
        stale = FillTask.objects.create(
            account_id=account,
            fill_id=str(fill.id),
            row_id="01ROW" + "0" * 21,
            position=1,
            result={"blank_cause": "search_throttled", "declined_cause": "no_tools_door", "cells": {}},
        )
        clean = FillTask.objects.create(
            account_id=account,
            fill_id=str(fill.id),
            row_id="01ROW" + "1" * 21,
            position=2,
            result={"blank_cause": "no_evidence"},
        )
        bare = FillTask.objects.create(account_id=account, fill_id=str(fill.id), row_id="01ROW" + "2" * 21, position=3)

        _migration._remap_retired_states(live_apps, None)

        stale.refresh_from_db()
        clean.refresh_from_db()
        bare.refresh_from_db()
        self.assertEqual(stale.result["blank_cause"], StoredCellState.TOOL_UNAVAILABLE)
        self.assertEqual(stale.result["declined_cause"], StoredCellState.TOOL_NOT_CONFIGURED)
        self.assertEqual(stale.result["cells"], {})
        self.assertEqual(clean.result["blank_cause"], "no_evidence")
        self.assertEqual(bare.result, {})
