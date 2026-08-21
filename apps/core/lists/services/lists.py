"""List services: all ORM access for the lists domain. Account-scoped
instance services (cross-tenant access fails as not-found, exactly like
a missing row, so foreign ids are not an oracle)."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count

from ..constants import CELL_MAX_LENGTH, MAX_FOLDERS, MAX_LIST_ROWS
from ..models import Folder, List, ListRow


class ListsFull(Exception):
    """Adding rows would exceed MAX_LIST_ROWS."""


class FoldersFull(Exception):
    """Creating a folder would exceed MAX_FOLDERS."""


class ListNotFound(Exception):
    """Missing OR foreign list (cross-tenant reads as not-found)."""


class FolderNotFound(Exception):
    """Missing OR foreign folder (cross-tenant reads as not-found)."""


class FolderService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def create(self, *, label: str) -> Folder:
        # A cap, not paging: the folder GET deliberately ships the whole
        # taxonomy, which is only sane while it is bounded.
        if Folder.objects.filter(account_id=self.account_id).count() >= MAX_FOLDERS:
            raise FoldersFull(f"an account holds at most {MAX_FOLDERS} folders")
        return Folder.objects.create(account_id=self.account_id, user_id=self.user_id, label=label)

    def all(self) -> list[Folder]:
        return list(Folder.objects.filter(account_id=self.account_id).order_by("-id"))

    def get(self, folder_id: str) -> Folder:
        try:
            return Folder.objects.get(id=folder_id, account_id=self.account_id)
        except Folder.DoesNotExist as e:
            raise FolderNotFound(folder_id) from e

    def list_counts(self, folder_ids: list[str]) -> dict[str, int]:
        """Lists per folder, one aggregate query (the wire's list_count)."""
        rows = (
            List.objects.filter(account_id=self.account_id, folder_id__in=folder_ids)
            .values_list("folder_id")
            .annotate(n=Count("id"))
        )
        return dict(rows)

    def rename(self, folder: Folder, *, label: str) -> Folder:
        folder.label = label
        folder.save(update_fields=["label", "updated_at"])
        return folder

    def delete(self, folder: Folder) -> None:
        """Delete the folder; its lists go LOOSE (moved to root), never
        deleted with it: the bucket is taxonomy, the sheets are work."""
        with transaction.atomic():
            # The same folder-row lock move() takes, so a move committed
            # first is seen by the loose-ing update and one committed
            # after finds the folder gone.
            if not Folder.objects.select_for_update().filter(id=folder.id, account_id=self.account_id):
                return
            List.objects.filter(account_id=self.account_id, folder_id=str(folder.id)).update(folder_id="")
            Folder.objects.filter(id=folder.id, account_id=self.account_id).delete()


class ListService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def create(
        self, *, label: str, columns: list[dict], origin: str, origin_ref: str = "", folder_id: str = ""
    ) -> List:
        return List.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
            label=label,
            columns=columns,
            origin=origin,
            origin_ref=origin_ref,
            folder_id=folder_id,
        )

    def page(self, *, after_id: str, limit: int) -> list[List]:
        qs = List.objects.filter(account_id=self.account_id).order_by("-id")
        if after_id:
            qs = qs.filter(id__lt=after_id)
        return list(qs[:limit])

    def get(self, list_id: str) -> List:
        try:
            return List.objects.get(id=list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(list_id) from e

    def rename(self, target: List, *, label: str) -> List:
        target.label = label
        target.save(update_fields=["label", "updated_at"])
        return target

    def move(self, target: List, *, folder_id: str) -> List:
        """Move into a folder ("" = loose). The folder must be the
        account's own; a foreign id reads as missing."""
        with transaction.atomic():
            # Lock the destination: without it a concurrent folder
            # delete can commit between this check and the save, leaving
            # a dangling folder_id.
            if folder_id and not Folder.objects.select_for_update().filter(id=folder_id, account_id=self.account_id):
                raise FolderNotFound(folder_id)
            target.folder_id = folder_id
            target.save(update_fields=["folder_id", "updated_at"])
        return target

    def add_rows(self, target: List, rows: list[dict[str, str]]) -> int:
        """Append rows (each a data dict keyed by column keys). Positions
        are dense and 1-based; the count ceiling AND the cell clamp live
        here so every entry path (import, snapshot, manual) hits one
        writer's rules (authored values clamp, never reject)."""
        if not rows:
            return 0
        rows = [{key: value[:CELL_MAX_LENGTH] for key, value in data.items()} for data in rows]
        with transaction.atomic():
            # Positions allocate from the current count, so concurrent
            # appends must serialize on the list row or the second one
            # collides with the (list_id, position) unique constraint.
            try:
                locked = List.objects.select_for_update().get(id=str(target.id), account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(str(target.id)) from e
            current = ListRow.objects.filter(list_id=str(locked.id)).count()
            if current + len(rows) > MAX_LIST_ROWS:
                raise ListsFull(f"a list holds at most {MAX_LIST_ROWS} rows")
            created = [
                ListRow(list_id=str(locked.id), position=current + offset, data=data)
                for offset, data in enumerate(rows, start=1)
            ]
            ListRow.objects.bulk_create(created, batch_size=1000)
            locked.row_count = current + len(created)
            locked.save(update_fields=["row_count", "updated_at"])
        return len(created)

    def rows_page(self, target: List, *, after_position: int, limit: int) -> list[ListRow]:
        return list(
            ListRow.objects.filter(list_id=str(target.id), position__gt=after_position).order_by("position")[:limit]
        )

    def column_values(self, target: List, *, key: str, limit: int) -> list[str]:
        """One column's non-empty values in position order (the use-time
        read behind seeding: the CALLER interprets them). Walks the rows
        server-side so a 25k-row sheet never round-trips to the browser
        just to extract a column."""
        out: list[str] = []
        after = 0
        while len(out) < limit:
            rows = self.rows_page(target, after_position=after, limit=1000)
            if not rows:
                break
            after = rows[-1].position
            for row in rows:
                value = str(row.data.get(key, "") or "").strip()
                if value:
                    out.append(value)
                    if len(out) >= limit:
                        break
        return out

    def delete(self, target: List) -> None:
        """Delete the list and its rows in one transaction (the service
        owns child cleanup; no cascades in this codebase)."""
        with transaction.atomic():
            # Locked, not exists(): add_rows holds the List row while it
            # inserts, so the delete must wait for it or the row cleanup
            # runs BEFORE the insert commits, orphaning rows forever (no
            # cascades). Also account-scoped: callers pre-scope, but the
            # service must not rely on that.
            if not List.objects.select_for_update().filter(id=target.id, account_id=self.account_id):
                return
            ListRow.objects.filter(list_id=str(target.id)).delete()
            List.objects.filter(id=target.id, account_id=self.account_id).delete()
