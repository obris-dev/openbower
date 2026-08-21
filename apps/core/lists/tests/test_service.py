"""The lists service contract: account scoping, caps, dense positions,
column reads, and child cleanup.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from lists.constants import ListOrigin
from lists.models import List, ListRow
from lists.services.lists import FolderNotFound, ListNotFound, ListService, ListsFull

_COLUMNS = [{"key": "domain", "label": "Domain", "type": "url"}, {"key": "name", "label": "Name", "type": "text"}]


def _service(account="01AC" + "A" * 22, user="01US" + "A" * 22) -> ListService:
    return ListService(account_id=account, user_id=user)


class ListServiceTests(TestCase):
    def test_create_page_get_rename_delete(self):
        service = _service()
        a = service.create(label="First", columns=_COLUMNS, origin=ListOrigin.CSV)
        b = service.create(label="Second", columns=[], origin=ListOrigin.MANUAL)
        # Same-millisecond ULIDs do not order by creation; assert against
        # the id order the keyset contract actually promises.
        newest, oldest = sorted((a, b), key=lambda x: str(x.id), reverse=True)
        page = service.page(after_id="", limit=10)
        self.assertEqual([x.id for x in page], [newest.id, oldest.id])
        older = service.page(after_id=str(newest.id), limit=10)
        self.assertEqual([x.id for x in older], [oldest.id])
        service.rename(a, label="Renamed")
        self.assertEqual(service.get(str(a.id)).label, "Renamed")
        service.add_rows(a, [{"domain": "acme.com", "name": "Acme"}])
        service.delete(a)
        self.assertEqual(List.objects.filter(id=a.id).count(), 0)
        self.assertEqual(ListRow.objects.filter(list_id=str(a.id)).count(), 0)  # child cleanup

    def test_foreign_account_reads_as_missing(self):
        mine = _service().create(label="Mine", columns=[], origin=ListOrigin.MANUAL)
        theirs = _service(account="01AC" + "Z" * 22)
        with self.assertRaises(ListNotFound):
            theirs.get(str(mine.id))
        self.assertEqual(theirs.page(after_id="", limit=10), [])

    def test_rows_are_dense_and_page_by_position(self):
        service = _service()
        target = service.create(label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
        service.add_rows(target, [{"domain": f"a{i}.com", "name": str(i)} for i in range(5)])
        service.add_rows(target, [{"domain": "later.com", "name": "later"}])  # appends continue the sequence
        rows = service.rows_page(target, after_position=0, limit=10)
        self.assertEqual([r.position for r in rows], [1, 2, 3, 4, 5, 6])
        page2 = service.rows_page(target, after_position=3, limit=2)
        self.assertEqual([r.position for r in page2], [4, 5])
        target.refresh_from_db()
        self.assertEqual(target.row_count, 6)

    def test_row_cap_is_enforced(self):
        service = _service()
        target = service.create(label="Full", columns=[], origin=ListOrigin.MANUAL)
        # A patched cap, not 50k real inserts: the policy under test is
        # the arithmetic, not bulk_create's throughput.
        with patch("lists.services.lists.MAX_LIST_ROWS", 5):
            service.add_rows(target, [{"a": str(i)} for i in range(4)])
            with self.assertRaises(ListsFull):
                service.add_rows(target, [{"a": "x"}, {"a": "y"}])
            service.add_rows(target, [{"a": "fits"}])
        target.refresh_from_db()
        self.assertEqual(target.row_count, 5)

    def test_column_values_in_position_order_skipping_blanks(self):
        service = _service()
        target = service.create(label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
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
            label="Sheet", columns=[{"key": "a", "label": "A", "type": "text"}], origin=ListOrigin.MANUAL
        )
        service.add_rows(target, [{"a": "x" * (CELL_MAX_LENGTH + 8)}])
        row = service.rows_page(target, after_position=0, limit=1)[0]
        self.assertEqual(len(row.data["a"]), CELL_MAX_LENGTH)


class FolderServiceTests(TestCase):
    def test_crud_and_delete_sets_lists_loose(self):
        from lists.services.lists import FolderService

        lists = _service()
        folders = FolderService(account_id="01AC" + "A" * 22, user_id="01US" + "A" * 22)
        bucket = folders.create(label="Clients")
        folders.rename(bucket, label="Customers")
        self.assertEqual(folders.get(str(bucket.id)).label, "Customers")

        inside = lists.create(label="Inside", columns=[], origin=ListOrigin.MANUAL, folder_id=str(bucket.id))
        loose = lists.create(label="Loose", columns=[], origin=ListOrigin.MANUAL)
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
        target = lists.create(label="Sheet", columns=[], origin=ListOrigin.MANUAL)
        mine = FolderService(account_id="01AC" + "A" * 22, user_id="01US" + "A" * 22).create(label="Mine")
        theirs = FolderService(account_id="01AC" + "Z" * 22, user_id="01US" + "Z" * 22).create(label="Theirs")

        lists.move(target, folder_id=str(mine.id))
        self.assertEqual(lists.get(str(target.id)).folder_id, str(mine.id))
        with self.assertRaises(FolderNotFound):
            lists.move(target, folder_id=str(theirs.id))
        lists.move(target, folder_id="")
        self.assertEqual(lists.get(str(target.id)).folder_id, "")
