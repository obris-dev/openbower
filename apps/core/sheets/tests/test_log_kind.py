"""The log kind: a log's sheet order is newest first, fixed at append, so
the one way rows page (the grid's keyset, a walk) reads it newest first;
a time window bounds it by id; the index and folder counts include a log.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_log_kind
"""

from __future__ import annotations

import time

from django.db import connection
from django.test import TestCase
from django.urls import reverse
from ulid import ULID

from common.testing import TEST_IDENTITY, login_session
from sheets.constants import RowSource, SheetKind, SheetOrigin
from sheets.models import Sheet
from sheets.operations.append_rows import AppendRowsOperation
from sheets.services.sheets import (
    FolderService,
    LogRowsFixed,
    RowCursor,
    SheetService,
)

ACCOUNT = TEST_IDENTITY["account_id"]
COLUMNS = [{"kind": "plain", "key": "company", "label": "Company", "type": "text"}]


def _batch(target: Sheet, *names: str) -> list[str]:
    report = AppendRowsOperation(
        account_id=ACCOUNT,
        target_sheet=target,
        rows=[{"company": n} for n in names],
        source=RowSource.FEED if target.kind == SheetKind.LOG else RowSource.MANUAL,
    ).run()
    # The next batch mints its ids in a later millisecond, so a window
    # cut at a batch's first id separates the batches exactly.
    time.sleep(0.003)
    return [str(row.id) for row in report.created]


def _instant(row_id: str) -> str:
    return ULID.from_str(row_id).datetime.isoformat()


class LogKindTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.sheets = SheetService(account_id=ACCOUNT)
        self.log = self.sheets.create(
            owner_id=TEST_IDENTITY["id"],
            label="Funding log",
            columns=COLUMNS,
            origin=SheetOrigin.MANUAL,
            kind=SheetKind.LOG,
        )
        self.first = _batch(self.log, "a", "b")
        self.second = _batch(self.log, "c", "d")
        self.third = _batch(self.log, "e")
        self.newest_first = [*reversed(self.third), *reversed(self.second), *reversed(self.first)]

    def _rows(self, **params: str) -> dict:
        resp = self.client.get(reverse("sheets_rows", kwargs={"id": str(self.log.id)}), params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_a_log_pages_newest_first_across_pages(self):
        # FAILS if a log's append ranks after its last row, like a sheet's.
        page = self._rows(limit="2")
        seen = [row["id"] for row in page["items"]]
        while page["next_cursor"]:
            page = self._rows(limit="2", after=page["next_cursor"])
            seen += [row["id"] for row in page["items"]]
        self.assertEqual(seen, self.newest_first)

    def test_a_walk_in_sheet_order_reads_a_log_newest_first(self):
        # A fill's walk pages through rows_page with the same cursor; on
        # a log that is newest first, so "the next N" are the newest.
        walked: list[str] = []
        after: RowCursor | None = None
        while rows := self.sheets.rows_page(self.log, after=after, limit=2):
            walked += [str(row.id) for row in rows]
            after = RowCursor(str(rows[-1].id), rows[-1].rank)
        self.assertEqual(walked, self.newest_first)

    def test_a_log_row_never_moves(self):
        # FAILS if move_row takes a log: a moved row would break newest first.
        oldest, newest = self.first[0], self.third[-1]
        with self.assertRaises(LogRowsFixed):
            self.sheets.move_row(self.log, oldest, after_id=None)
        with self.assertRaises(LogRowsFixed):
            self.sheets.move_row(self.log, newest, after_id=oldest)
        walked = [str(row.id) for row in self.sheets.rows_page(self.log, limit=10)]
        self.assertEqual(walked, self.newest_first)

    def test_a_standard_sheet_still_appends_at_the_end(self):
        sheet = self.sheets.create(
            owner_id=TEST_IDENTITY["id"], label="Sheet", columns=COLUMNS, origin=SheetOrigin.MANUAL
        )
        ids = _batch(sheet, "a", "b") + _batch(sheet, "c")
        self.assertEqual([str(row.id) for row in self.sheets.rows_page(sheet, limit=10)], ids)

    def test_since_is_inclusive_and_pages_within_the_window(self):
        # FAILS if ?since= is dropped: the oldest batch would page too.
        since = _instant(self.second[0])
        page = self._rows(since=since, limit="2")
        seen = [row["id"] for row in page["items"]]
        while page["next_cursor"]:
            page = self._rows(since=since, limit="2", after=page["next_cursor"])
            seen += [row["id"] for row in page["items"]]
        self.assertEqual(seen, [*reversed(self.third), *reversed(self.second)])

    def test_until_is_exclusive(self):
        # Yesterday is since yesterday's midnight until today's: the row
        # at the until instant belongs to the next window.
        items = self._rows(since=_instant(self.second[0]), until=_instant(self.third[0]))["items"]
        self.assertEqual([row["id"] for row in items], list(reversed(self.second)))

    def test_a_window_on_a_standard_sheet_is_refused(self):
        sheet = self.sheets.create(
            owner_id=TEST_IDENTITY["id"], label="Sheet", columns=COLUMNS, origin=SheetOrigin.MANUAL
        )
        resp = self.client.get(reverse("sheets_rows", kwargs={"id": str(sheet.id)}), {"since": _instant(self.first[0])})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("applies to a log", resp.content.decode())

    def test_a_window_bound_must_be_an_instant_with_an_offset(self):
        # FAILS (a 500) if an impossible date or a pre-1970 instant escapes
        # the parser: the first raises ValueError, the second has no ULID.
        for raw in ("yesterday", "2026-11-11T00:00:00", "2026-02-30T00:00:00+00:00", "1969-12-31T23:59:59+00:00"):
            with self.subTest(raw=raw):
                resp = self.client.get(reverse("sheets_rows", kwargs={"id": str(self.log.id)}), {"since": raw})
                self.assertEqual(resp.status_code, 400)

    def test_a_row_carries_when_it_arrived(self):
        [row] = self._rows(limit="1")["items"]
        stored = self.sheets.row(self.log, row["id"])
        self.assertEqual(row["created_at"], stored.created_at.isoformat())

    def test_the_sheets_index_and_folder_counts_include_a_log(self):
        # FAILS if the index leaves a log out: home is every sheet, a
        # signal's log among them.
        folders = FolderService(account_id=ACCOUNT, user_id=TEST_IDENTITY["id"])
        folder = folders.create(label="Folder")
        Sheet.objects.filter(id=self.log.id).update(folder_id=str(folder.id))
        sheet = self.sheets.create(
            owner_id=TEST_IDENTITY["id"],
            label="Sheet",
            columns=COLUMNS,
            origin=SheetOrigin.MANUAL,
            folder_id=str(folder.id),
        )
        index = self.client.get(reverse("sheets_index")).json()
        self.assertEqual(
            [(item["id"], item["kind"]) for item in index["items"]],
            [(str(sheet.id), "standard"), (str(self.log.id), "log")],
        )
        self.assertEqual(folders.sheet_counts([str(folder.id)]), {str(folder.id): 2})

    def test_the_index_narrows_to_one_kind(self):
        # FAILS if ?kind= is ignored: a producer asking which sheets take a
        # push would be handed a log, whose push answers 409.
        sheet = self.sheets.create(
            owner_id=TEST_IDENTITY["id"], label="Sheet", columns=COLUMNS, origin=SheetOrigin.MANUAL
        )
        url = reverse("sheets_index")
        standard = self.client.get(url, {"kind": "standard"}).json()
        self.assertEqual([item["id"] for item in standard["items"]], [str(sheet.id)])
        logs = self.client.get(url, {"kind": "log"}).json()
        self.assertEqual([item["id"] for item in logs["items"]], [str(self.log.id)])

    def test_the_index_refuses_a_kind_it_does_not_have(self):
        response = self.client.get(reverse("sheets_index"), {"kind": "digest"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("standard, log", str(response.json()))

    def test_the_detail_names_the_kind(self):
        detail = self.client.get(reverse("sheets_detail", kwargs={"id": str(self.log.id)})).json()
        self.assertEqual(detail["kind"], "log")

    def test_a_sheet_made_before_the_kind_reads_standard(self):
        # The column's database default: an insert that names no kind, as
        # a process on the code before the column makes.
        old_id = str(ULID())
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sheets_sheet (id, created_at, updated_at, account_id, user_id, folder_id, label,"
                " columns, origin, origin_ref, row_count) VALUES (%s, now(), now(), %s, %s, '', 'Old', '[]',"
                " 'manual', '', 0)",
                [old_id, ACCOUNT, TEST_IDENTITY["id"]],
            )
        self.assertEqual(Sheet.objects.get(id=old_id).kind, SheetKind.STANDARD)


class FolderKindTests(TestCase):
    """Folders hold sheets of any kind (a folder is a place sheets point
    at), and count them all."""

    def setUp(self) -> None:
        login_session(self.client)
        self.sheets = SheetService(account_id=ACCOUNT)
        self.folders = FolderService(account_id=ACCOUNT, user_id=TEST_IDENTITY["id"])
        self.folder = self.folders.create(label="Funding")

    def _make(self, kind: SheetKind) -> Sheet:
        return self.sheets.create(
            owner_id=TEST_IDENTITY["id"], label=kind.value, columns=COLUMNS, origin=SheetOrigin.MANUAL, kind=kind
        )

    def test_a_folder_holds_and_counts_either_kind(self):
        folder = str(self.folder.id)
        self.sheets.move(self._make(SheetKind.STANDARD), folder_id=folder)
        self.sheets.move(self._make(SheetKind.LOG), folder_id=folder)
        self.assertEqual(self.folders.sheet_counts([folder]), {folder: 2})

    def test_deleting_a_folder_unfiles_every_kind(self):
        folder = str(self.folder.id)
        sheet, log = self._make(SheetKind.STANDARD), self._make(SheetKind.LOG)
        self.sheets.move(sheet, folder_id=folder)
        self.sheets.move(log, folder_id=folder)
        self.folders.delete(self.folder)
        self.assertEqual(set(Sheet.objects.filter(id__in=[sheet.id, log.id]).values_list("folder_id", flat=True)), {""})
