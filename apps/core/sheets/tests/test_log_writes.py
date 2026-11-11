"""How a log is written: its base columns are immutable (a person never
edits their cells, and they cannot be deleted), while a person's own
columns on the same log stay writable.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_log_writes
"""

from __future__ import annotations

import time

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from openbower_kernel.fields import new_ulid
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.sheets import PlainColumn
from sheets.constants import CellErrorCode, FillErrorCode, IngestErrorCode, SheetKind, SheetOrigin
from sheets.ingest.consumer import handle_ingest_event
from sheets.ingest.events import IngestEvent
from sheets.models import NodeRun, Sheet
from sheets.operations.append_rows import AppendRowsOperation, LogRowsFromSignal
from sheets.processors.base import queue_rank
from sheets.services.node_runs import NodeRunFlow
from sheets.services.sheets import SheetService
from sheets.services.workflows import WorkflowService

ACCOUNT = TEST_IDENTITY["account_id"]


class ImmutableColumnTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.sheets = SheetService(account_id=ACCOUNT)
        self.log = self.sheets.create(
            owner_id=TEST_IDENTITY["id"],
            label="Funding log",
            columns=[
                PlainColumn(key="company", label="Company", type="text", immutable=True),
                PlainColumn(key="notes", label="Notes", type="text"),
            ],
            origin=SheetOrigin.MANUAL,
            kind=SheetKind.LOG,
        )
        report = AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=self.log, rows=[{"company": "acme.example"}], into_log=True
        ).run()
        self.row_id = str(report.created[0].id)

    def _patch_cells(self, data: dict[str, str]):
        return self.client.patch(
            reverse("sheets_row", kwargs={"id": str(self.log.id), "row_id": self.row_id}),
            {"data": data},
            content_type="application/json",
        )

    def test_a_person_cannot_edit_an_immutable_cell(self):
        # FAILS if set_cells ignores the flag: the arrived value would change.
        resp = self._patch_cells({"company": "renamed.example"})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], CellErrorCode.COLUMN_NOT_WRITABLE)
        self.assertIn("cannot be edited", resp.json()["detail"])
        self.assertEqual(self.sheets.row(self.log, self.row_id).data["company"], "acme.example")

    def test_a_persons_own_column_on_a_log_stays_writable(self):
        resp = self._patch_cells({"notes": "call next week"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.sheets.row(self.log, self.row_id).data["notes"], "call next week")

    def test_an_immutable_column_cannot_be_deleted(self):
        # FAILS if the delete ignores the flag: the column and its values go.
        resp = self.client.delete(reverse("sheets_column_detail", kwargs={"id": str(self.log.id), "key": "company"}))
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], FillErrorCode.COLUMN_IMMUTABLE)
        self.log.refresh_from_db()
        self.assertEqual([column.key for column in self.log.columns], ["company", "notes"])

    def test_an_immutable_column_can_be_renamed_and_reordered(self):
        renamed = self.client.patch(
            reverse("sheets_column_detail", kwargs={"id": str(self.log.id), "key": "company"}),
            {"label": "Company name"},
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        reordered = self.client.patch(
            reverse("sheets_columns_order", kwargs={"id": str(self.log.id)}),
            {"keys": ["notes", "company"]},
            content_type="application/json",
        )
        self.assertEqual(reordered.status_code, 200, reordered.content)
        self.log.refresh_from_db()
        self.assertEqual(
            [(c.key, c.label, c.immutable) for c in self.log.columns][1], ("company", "Company name", True)
        )


def _log_with_an_ai_column() -> Sheet:
    """A log whose AI column is an entry action, so an arriving row that
    holds something is offered to it."""
    sheets = SheetService(account_id=ACCOUNT)
    log = sheets.create(
        owner_id=TEST_IDENTITY["id"],
        label="Funding log",
        columns=[PlainColumn(key="company", label="Company", type="text", immutable=True)],
        origin=SheetOrigin.MANUAL,
        kind=SheetKind.LOG,
    )
    config = AgentConfig(
        prompt="Find the answer for {{company}}",
        provider="openai_compatible",
        source="ollama",
        model="scripted",
        tools=AgentTools(),
        outputs=[AgentOutput(key="answer", label="Answer", type="text")],
    )
    agent = AgentService(account_id=ACCOUNT).create_ephemeral(
        owner_id=TEST_IDENTITY["id"], label="Answer", config=config
    )
    node = WorkflowService(account_id=ACCOUNT).get_or_create_column_agent_node(log, agent_id=str(agent.id))
    log.columns = [
        *log.columns,
        {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(node.id)},
    ]
    log.save(update_fields=["columns", "updated_at"])
    return log


class LogAppendTests(TestCase):
    """A log's rows arrive from its signal only, and its autofill switch
    decides whether they are offered to its columns."""

    def setUp(self) -> None:
        login_session(self.client)
        self.log = _log_with_an_ai_column()

    def test_a_person_cannot_add_rows_to_a_log(self):
        # FAILS if the operation takes a log from any door.
        resp = self.client.post(
            reverse("sheets_rows", kwargs={"id": str(self.log.id)}),
            {"rows": [{"company": "acme.example"}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], IngestErrorCode.LOG_ROWS_FROM_SIGNAL)
        self.assertEqual(SheetService(account_id=ACCOUNT).rows_page(self.log, limit=10), [])

    def test_a_push_to_a_log_is_refused_before_the_bus(self):
        resp = self.client.post(
            reverse("sheets_ingest", kwargs={"id": str(self.log.id)}),
            {"rows": [{"company": "acme.example"}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], IngestErrorCode.LOG_ROWS_FROM_SIGNAL)

    def test_a_pushed_event_for_a_log_is_dropped(self):
        # An event that skipped the door: terminal, never redelivered.
        event = IngestEvent(
            event_id=new_ulid(),
            sheet_id=str(self.log.id),
            account_id=ACCOUNT,
            user_id=TEST_IDENTITY["id"],
            rows=[{"company": "acme.example"}],
            received_at=timezone.now(),
        )
        self.assertEqual(handle_ingest_event(event), "dropped")

    def test_the_signal_appends_and_its_rows_autofill(self):
        report = AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=self.log, rows=[{"company": "acme.example"}], into_log=True
        ).run()
        self.assertEqual(report.added, 1)
        self.assertEqual(report.queued, 1)
        self.assertEqual(NodeRun.objects.filter(sheet_id=str(self.log.id)).count(), 1)

    def test_autofill_off_lands_the_rows_with_no_work(self):
        # FAILS if react is ignored: the row would queue a run.
        report = AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=self.log, rows=[{"company": "acme.example"}], into_log=True, react=False
        ).run()
        self.assertEqual((report.added, report.queued), (1, 0))
        self.assertFalse(NodeRun.objects.filter(sheet_id=str(self.log.id)).exists())

    def test_the_refusal_names_the_log(self):
        with self.assertRaises(LogRowsFromSignal) as caught:
            AppendRowsOperation(account_id=ACCOUNT, target_sheet=self.log, rows=[{"company": "x.example"}]).run()
        self.assertEqual(caught.exception.sheet_id, str(self.log.id))


class LogQueueOrderTests(TestCase):
    """A log's runs wait in ARRIVAL order (oldest first), so a backlog
    drains FIFO; a standard sheet's still wait in sheet order."""

    def test_a_logs_autofill_backlog_drains_oldest_first(self):
        # FAILS if a log's runs are stamped with the row's rank: on a log
        # that is newest first, so the newest batch would be claimed first.
        log = _log_with_an_ai_column()
        arrivals: list[str] = []
        for names in (["first.example"], ["second.example", "third.example"], ["fourth.example"]):
            report = AppendRowsOperation(
                account_id=ACCOUNT, target_sheet=log, rows=[{"company": n} for n in names], into_log=True
            ).run()
            arrivals += [str(row.id) for row in report.created]
            time.sleep(0.003)
        claimed = [run.row_id for run in NodeRunFlow.iter_ready(limit=10) if run.sheet_id == str(log.id)]
        self.assertEqual(claimed, arrivals)

    def test_the_queue_position_follows_the_kind(self):
        log = _log_with_an_ai_column()
        report = AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=log, rows=[{"company": "acme.example"}], into_log=True
        ).run()
        [row] = report.created
        self.assertEqual(queue_rank(log, row), str(row.id))
        sheet = SheetService(account_id=ACCOUNT).create(
            owner_id=TEST_IDENTITY["id"], label="Sheet", columns=[], origin=SheetOrigin.MANUAL
        )
        [standard_row] = SheetService(account_id=ACCOUNT).add_rows(sheet, [{"company": "acme.example"}])
        self.assertEqual(queue_rank(sheet, standard_row), standard_row.rank)
