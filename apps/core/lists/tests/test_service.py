"""The lists service contract: account scoping, caps, ranked rows,
column reads, and child cleanup.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner, JobService
from lists.constants import ListOrigin
from lists.jobs.rerank import Rerank
from lists.models import List, ListRow
from lists.services.lists import (
    ColumnNotWritable,
    FolderNotFound,
    InvalidRowCursor,
    ListNotFound,
    ListService,
    ListsFull,
    RowCursor,
    RowNotFound,
    RowRankTooDeep,
)
from lists.tests.fill_helpers import open_fill_job
from openbower_kernel.fields import new_ulid

_COLUMNS = [
    {"kind": "plain", "key": "domain", "label": "Domain", "type": "url"},
    {"kind": "plain", "key": "name", "label": "Name", "type": "text"},
]


def _service(account="01AC" + "A" * 22, user="01US" + "A" * 22) -> ListService:
    return ListService(account_id=account)


class ListServiceTests(TestCase):
    def test_create_page_get_rename_delete(self):
        service = _service()
        a = service.create(owner_id="01US" + "A" * 22, label="First", columns=_COLUMNS, origin=ListOrigin.CSV)
        b = service.create(owner_id="01US" + "A" * 22, label="Second", columns=[], origin=ListOrigin.MANUAL)
        # Ids mint monotonic even within a millisecond, so creation
        # order IS id order, and the page (newest first) shows it.
        self.assertGreater(str(b.id), str(a.id))
        page = service.page(after_id="", limit=10)
        self.assertEqual([x.id for x in page], [b.id, a.id])
        older = service.page(after_id=str(b.id), limit=10)
        self.assertEqual([x.id for x in older], [a.id])
        service.rename(a, label="Renamed")
        self.assertEqual(service.get(str(a.id)).label, "Renamed")
        service.add_rows(a, [{"domain": "acme.com", "name": "Acme"}])
        service.delete(a)
        self.assertEqual(List.objects.filter(id=a.id).count(), 0)
        self.assertEqual(ListRow.objects.filter(list_id=str(a.id)).count(), 0)  # child cleanup

    def test_foreign_account_reads_as_missing(self):
        mine = _service().create(owner_id="01US" + "A" * 22, label="Mine", columns=[], origin=ListOrigin.MANUAL)
        theirs = _service(account="01AC" + "Z" * 22)
        with self.assertRaises(ListNotFound):
            theirs.get(str(mine.id))
        self.assertEqual(theirs.page(after_id="", limit=10), [])

    def test_rows_are_ranked_in_order_and_page_by_a_row_cursor(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(5)])
        service.add_rows(target, [{"domain": "later.com", "name": "later"}])  # appends continue the order
        rows = service.rows_page(target, limit=10)
        self.assertEqual([r.data["name"] for r in rows], ["0", "1", "2", "3", "4", "later"])
        self.assertEqual([r.rank for r in rows], sorted(r.rank for r in rows))
        self.assertLessEqual(max(len(r.rank) for r in rows), 2)
        cursor = RowCursor(str(rows[2].id), rows[2].rank)
        page2 = service.rows_page(target, after=RowCursor.parse(cursor.wire()), limit=2)
        self.assertEqual([r.data["name"] for r in page2], ["3", "4"])
        # A bad id half, then a bad rank half beside a real id: empty,
        # off the alphabet, and far past the column bound each refuse.
        row_id = str(rows[0].id)
        bad_ranks = ("", "zz", "a10", "a\x00", "a1!", "a" * 100)
        for bad in ("01ROW" + "0" * 21, "a0.nope", "!.01ROW" + "0" * 21, *(f"{r}.{row_id}" for r in bad_ranks)):
            with self.subTest(bad=bad), self.assertRaises(InvalidRowCursor):
                RowCursor.parse(bad)
        # A cursor carries its rank, so a keyset needs no row to exist:
        # the walk continues from where a deleted cursor row sat.
        gone = rows[2]
        ListRow.objects.filter(id=gone.id).delete()
        page3 = service.rows_page(target, after=RowCursor(str(gone.id), gone.rank), limit=2)
        self.assertEqual([r.data["name"] for r in page3], ["3", "4"])
        target.refresh_from_db()
        self.assertEqual(target.row_count, 6)

    def test_the_consent_set_is_bounded_by_id_and_walked_in_sheet_order(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        rows = service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(4)])
        # Moved to the top: the sheet order changes, and a walk follows
        # it; the set "existed at the click" is still an id bound.
        service.move_row(target, str(rows[3].id), after_id=None)
        self.assertEqual([r.data["name"] for r in service.rows_page(target, limit=10)], ["3", "0", "1", "2"])
        walked = service.rows_page(target, limit=10, until_id=str(rows[2].id))
        self.assertEqual([r.data["name"] for r in walked], ["0", "1", "2"])
        # An id minted after the rows bounds the same set as the newest row's.
        self.assertEqual(
            [r.data["name"] for r in service.rows_page(target, limit=10, until_id=new_ulid())], ["3", "0", "1", "2"]
        )

    def test_a_move_writes_one_row_and_a_deep_gap_queues_a_rerank(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        rows = service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(4)])
        before = {str(r.id): r.rank for r in service.rows_page(target, limit=10)}
        moved = service.move_row(target, str(rows[0].id), after_id=str(rows[2].id))
        after = {str(r.id): r.rank for r in service.rows_page(target, limit=10)}
        self.assertEqual([r.data["name"] for r in service.rows_page(target, limit=10)], ["1", "2", "0", "3"])
        # One row changed rank; every other row kept its key.
        changed = {row_id for row_id in before if before[row_id] != after[row_id]}
        self.assertEqual(changed, {str(moved.id)})
        self.assertEqual(Job.objects.filter(kind="rerank").count(), 0)
        # Two rows leapfrogging into the same gap (each lands between
        # row 1 and the other's fresh key) deepen the key by about a
        # character per move until the rebalance bound: the sheet is
        # re-spaced by a job, the order kept, the keys short again.
        with patch("lists.services.lists.RANK_REBALANCE_LENGTH", 3):
            for n in range(10):
                mover = rows[0] if n % 2 == 0 else rows[2]
                service.move_row(target, str(mover.id), after_id=str(rows[1].id))
        # Queued ONCE while it is open, however many moves cross the bound.
        (job,) = list(Job.objects.filter(kind="rerank"))
        self.assertIsNone(job.user_id)  # a system job: nobody asked
        self.assertEqual((job.payload, job.target_id), ({"list_id": str(target.id)}, str(target.id)))
        JobRunner(worker_id="test:1").tick()
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.DONE)
        rows_now = service.rows_page(target, limit=10)
        self.assertEqual([r.data["name"] for r in rows_now], ["1", "2", "0", "3"])
        self.assertLessEqual(max(len(r.rank) for r in rows_now), 2)
        with self.assertRaises(RowNotFound):
            service.move_row(target, str(rows[0].id), after_id="01ROW" + "0" * 21)
        # Scoped like every write: a foreign account reads the list as
        # missing, and a row or a neighbour from another sheet as missing.
        other = service.create(owner_id="01US" + "A" * 22, label="Other", columns=_COLUMNS, origin=ListOrigin.CSV)
        (stranger,) = service.add_rows(other, [{"domain": "z.com", "name": "z"}])
        with self.assertRaises(ListNotFound):
            _service(account="01AC" + "Z" * 22).move_row(target, str(rows[0].id), after_id=None)
        with self.assertRaises(RowNotFound):
            service.move_row(target, str(stranger.id), after_id=None)
        with self.assertRaises(RowNotFound):
            service.move_row(target, str(rows[0].id), after_id=str(stranger.id))

    def test_a_rerank_re_spaces_a_reordered_sheet_without_a_rank_collision(self):
        # A row moved to the top shifts every other row's fresh key onto
        # a key a neighbour still holds; the re-space must not depend on
        # the order the database visits rows in. Twice, so the keys are
        # shown to settle around a0 rather than climb.
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        rows = service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(3)])
        service.move_row(target, str(rows[2].id), after_id=None)
        jobs = JobService(account_id="01AC" + "A" * 22)
        for _round in range(2):
            jobs.enqueue_system(Rerank(list_id=str(target.id)), target_id=str(target.id))
            with patch("lists.services.lists.FILL_WRITE_BATCH", 2):
                JobRunner(worker_id="test:1").tick()
            (job,) = list(Job.objects.filter(kind="rerank", status=JobStatus.DONE))
            job.delete()
            page = service.rows_page(target, limit=10)
            self.assertEqual([r.data["name"] for r in page], ["2", "0", "1"])
            self.assertLessEqual(max(len(r.rank) for r in page), 2)

    def test_a_rerank_waits_for_the_lists_open_fills(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        rows = service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(3)])
        service.move_row(target, str(rows[2].id), after_id=None)
        before = [r.rank for r in service.rows_page(target, limit=10)]
        # A fill mid-walk, held by another worker so this tick cannot close it.
        fill = open_fill_job(
            account_id="01AC" + "A" * 22,
            user_id="01US" + "A" * 22,
            list_id=str(target.id),
            node_id="01ND" + "A" * 22,
            agent_id="01AG" + "A" * 22,
            column_keys=["name"],
            consented=3,
            status=JobStatus.PROCESSING,
        )
        jobs = JobService(account_id="01AC" + "A" * 22)
        rerank = jobs.enqueue_system(Rerank(list_id=str(target.id)), target_id=str(target.id))
        with patch("lists.jobs.rerank.RERANK_WAIT_SECONDS", 0):
            JobRunner(worker_id="test:1").tick()
            rerank.refresh_from_db()
            # Parked, not run: the keys are untouched and no attempt spent.
            self.assertEqual((rerank.status, rerank.attempts), (JobStatus.READY, 0))
            self.assertEqual([r.rank for r in service.rows_page(target, limit=10)], before)
            Job.objects.filter(id=fill.id).update(status=JobStatus.CANCELLED)
            JobRunner(worker_id="test:1").tick()
        rerank.refresh_from_db()
        self.assertEqual(rerank.status, JobStatus.DONE)
        self.assertEqual([r.data["name"] for r in service.rows_page(target, limit=10)], ["2", "0", "1"])
        self.assertNotEqual([r.rank for r in service.rows_page(target, limit=10)], before)

    def test_a_move_that_changes_nothing_writes_nothing(self):
        # A row dropped on itself, or right after the row it already
        # follows, keeps its key: no write, no deepening, no rerank
        # (the bound is patched to 0 so any write would queue one).
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        rows = service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(3)])
        before = {str(r.id): r.rank for r in service.rows_page(target, limit=10)}
        with patch("lists.services.lists.RANK_REBALANCE_LENGTH", 0):
            service.move_row(target, str(rows[1].id), after_id=str(rows[1].id))
            service.move_row(target, str(rows[1].id), after_id=str(rows[0].id))
            service.move_row(target, str(rows[0].id), after_id=None)
        self.assertEqual({str(r.id): r.rank for r in service.rows_page(target, limit=10)}, before)
        self.assertEqual(Job.objects.filter(kind="rerank").count(), 0)

    def test_a_move_refuses_a_rank_past_the_column_bound(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        rows = service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(3)])
        # The re-space queued past the rebalance length has not run yet
        # (nothing ticks it here); the bound is met by a few more moves.
        with (
            patch("lists.services.lists.RANK_REBALANCE_LENGTH", 3),
            patch("lists.services.lists.RANK_MAX_LENGTH", 4),
            self.assertRaises(RowRankTooDeep),
        ):
            for n in range(20):
                mover = rows[0] if n % 2 == 0 else rows[2]
                service.move_row(target, str(mover.id), after_id=str(rows[1].id))
        self.assertLessEqual(max(len(r.rank) for r in service.rows_page(target, limit=10)), 4)
        self.assertEqual(Job.objects.filter(kind="rerank").count(), 1)

    def test_a_row_with_no_rank_is_refused_at_the_insert(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        with self.assertRaisesMessage(IntegrityError, "list_row_rank_named"):
            ListRow.objects.create(list_id=str(target.id), data={"domain": "acme.com", "name": "x"}, rank="")

    def test_row_cap_is_enforced(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Full", columns=[], origin=ListOrigin.MANUAL)
        # A patched cap, not 50k real inserts: the policy under test is
        # the arithmetic, not bulk_create's throughput.
        with patch("lists.services.lists.MAX_LIST_ROWS", 5):
            service.add_rows(target, [{"a": str(i)} for i in range(4)])
            with self.assertRaises(ListsFull):
                service.add_rows(target, [{"a": "x"}, {"a": "y"}])
            service.add_rows(target, [{"a": "fits"}])
        target.refresh_from_db()
        self.assertEqual(target.row_count, 5)

    def test_column_values_in_sheet_order_skipping_blanks(self):
        service = _service()
        target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        service.add_rows(
            target,
            [
                {"domain": "https://b.example/x", "name": "B"},
                {"domain": "", "name": "blank"},
                {"domain": "a.example", "name": "A"},
            ],
        )
        self.assertEqual(
            service.column_values(target, key="domain", limit=10),
            ["https://b.example/x", "a.example"],
        )
        self.assertEqual(service.column_values(target, key="missing", limit=10), [])


class CellClampTests(TestCase):
    def test_every_entry_path_clamps_cells_at_the_writer(self):
        # The clamp lives in add_rows, the single writer, so CSV
        # import and manual appends cannot bypass it (and a batch is
        # never rejected over one long authored value).
        from lists.constants import CELL_MAX_LENGTH

        service = _service()
        target = service.create(
            owner_id="01US" + "A" * 22,
            label="Sheet",
            columns=[{"kind": "plain", "key": "a", "label": "A", "type": "text"}],
            origin=ListOrigin.MANUAL,
        )
        # And the clamp is LOGGED: a cut value is data the sheet no
        # longer holds in full, so silence would hide it.
        with self.assertLogs("lists.services.lists", level="WARNING") as logs:
            service.add_rows(target, [{"a": "x" * (CELL_MAX_LENGTH + 8)}])
        row = service.rows_page(target, limit=1)[0]
        self.assertEqual(len(row.data["a"]), CELL_MAX_LENGTH)
        self.assertIn(f"clamped from {CELL_MAX_LENGTH + 8} to {CELL_MAX_LENGTH}", logs.output[0])
        with self.assertNoLogs("lists.services.lists"):
            service.add_rows(target, [{"a": "x" * CELL_MAX_LENGTH}])


class CellNormalizeTests(TestCase):
    def test_add_rows_normalizes_typed_cells_at_the_writer(self):
        # add_rows runs through the shared normalize_row, so EVERY entry
        # path (CSV import, manual append, the push worker) stores a typed
        # value in its canonical form, the same shape the fill write path
        # stores. FAILS if add_rows stops normalizing (the drift this
        # consolidation closed: a pushed "1,234" next to an autofilled
        # "1234").
        service = _service()
        target = service.create(
            owner_id="01US" + "A" * 22,
            label="Typed",
            columns=[
                {"kind": "plain", "key": "score", "label": "Score", "type": "number"},
                {"kind": "plain", "key": "when", "label": "When", "type": "date"},
            ],
            origin=ListOrigin.MANUAL,
        )
        service.add_rows(target, [{"score": "1,234", "when": "2026/3/4"}])
        row = service.rows_page(target, limit=1)[0]
        self.assertEqual(row.data["score"], "1234")  # commas stripped
        self.assertEqual(row.data["when"], "2026-03-04")  # date canonicalized

    def test_add_rows_tolerates_a_type_mismatch_and_stores_it_raw(self):
        # Authored input never fails a batch: a value its type refuses is
        # stored raw (the producer-facing 400 is the POST's reaction, not
        # the shared writer's), so a messy CSV still imports.
        service = _service()
        target = service.create(
            owner_id="01US" + "A" * 22,
            label="Typed",
            columns=[{"kind": "plain", "key": "score", "label": "Score", "type": "number"}],
            origin=ListOrigin.CSV,
        )
        service.add_rows(target, [{"score": "banana"}])
        row = service.rows_page(target, limit=1)[0]
        self.assertEqual(row.data["score"], "banana")  # tolerated, not rejected


class RecordedColumnTests(TestCase):
    """A column whose cells are RECORDED is written through the landing,
    which writes the value and its truth in one transaction. add_rows
    writes rows and nothing else, so it refuses one: a value stored
    there with no record reads as never attempted forever, and no
    barrier waiting on that column ever completes for the row."""

    def _sheet(self):
        return _service().create(
            owner_id="01US" + "A" * 22,
            label="Mixed",
            columns=[
                {"kind": "plain", "key": "company", "label": "Company", "type": "text"},
                {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": "01ND" + "A" * 22},
                {"key": "crm", "label": "CRM", "type": "text", "kind": "webhook", "node_id": "01ND" + "B" * 22},
            ],
            origin=ListOrigin.MANUAL,
        )

    def test_a_row_carrying_a_recorded_column_is_refused_and_nothing_is_written(self):
        # Both recorded kinds, and the whole batch: a caller bug is a
        # door to fix, not a row to salvage, so the good row beside it
        # lands nothing either. FAILS if the refusal goes, or if it
        # fires after the rows are written.
        service = _service()
        target = self._sheet()
        for cells in ({"company": "acme.com", "answer": "pinned"}, {"company": "acme.com", "crm": "sent"}):
            with self.subTest(cells=cells), self.assertRaises(ColumnNotWritable):
                service.add_rows(target, [{"company": "fine.io"}, cells])
        self.assertEqual(ListRow.objects.filter(list_id=str(target.id)).count(), 0)

    def test_the_refusal_names_the_column_and_the_row(self):
        # The message is what an upstream door reads to fix itself.
        service = _service()
        target = self._sheet()
        with self.assertRaisesMessage(ColumnNotWritable, "row 1: ['answer']"):
            service.add_rows(target, [{"company": "fine.io"}, {"answer": "pinned"}])

    def test_a_key_matching_no_column_is_still_tolerated(self):
        # The refusal is scoped to RECORDED columns. A key the sheet has
        # no column for keeps the writer's tolerance (the discover save
        # writes its own keys into a sheet that need not have them), so
        # widening the raise stays a separate decision. FAILS if the
        # refusal swallows the tolerant path.
        service = _service()
        target = self._sheet()
        (row,) = service.add_rows(target, [{"company": "acme.com", "stray": "kept"}])
        self.assertEqual(row.data["stray"], "kept")


class FolderServiceTests(TestCase):
    def test_crud_and_delete_sets_lists_loose(self):
        from lists.services.lists import FolderService

        lists = _service()
        folders = FolderService(account_id="01AC" + "A" * 22, user_id="01US" + "A" * 22)
        bucket = folders.create(label="Clients")
        folders.rename(bucket, label="Customers")
        self.assertEqual(folders.get(str(bucket.id)).label, "Customers")

        inside = lists.create(
            owner_id="01US" + "A" * 22, label="Inside", columns=[], origin=ListOrigin.MANUAL, folder_id=str(bucket.id)
        )
        loose = lists.create(owner_id="01US" + "A" * 22, label="Loose", columns=[], origin=ListOrigin.MANUAL)
        folders.delete(bucket)
        inside.refresh_from_db()
        loose.refresh_from_db()
        # The bucket dies; the sheets survive, loose.
        self.assertEqual(inside.folder_id, "")
        self.assertEqual(loose.folder_id, "")
        self.assertEqual(folders.all(), [])

    def test_move_validates_folder_ownership(self):
        from lists.services.lists import FolderService

        lists = _service()
        target = lists.create(owner_id="01US" + "A" * 22, label="Sheet", columns=[], origin=ListOrigin.MANUAL)
        mine = FolderService(account_id="01AC" + "A" * 22, user_id="01US" + "A" * 22).create(label="Mine")
        theirs = FolderService(account_id="01AC" + "Z" * 22, user_id="01US" + "Z" * 22).create(label="Theirs")

        lists.move(target, folder_id=str(mine.id))
        self.assertEqual(lists.get(str(target.id)).folder_id, str(mine.id))
        with self.assertRaises(FolderNotFound):
            lists.move(target, folder_id=str(theirs.id))
        lists.move(target, folder_id="")
        self.assertEqual(lists.get(str(target.id)).folder_id, "")
