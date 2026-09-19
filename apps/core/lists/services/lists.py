"""List services: all ORM access for the lists domain. Account-scoped
instance services (cross-tenant access fails as not-found, exactly like
a missing row, so foreign ids are not an oracle)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import NamedTuple

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from agents.services import AgentService
from openbower_schema.cell_types import CellTypeMismatch, normalize_row
from openbower_schema.lists import ListColumn

from ..constants import CELL_MAX_LENGTH, MAX_FOLDERS, MAX_LIST_ROWS
from ..models import Fill, Folder, List, ListRow, NodeRun
from . import cell_truth, webhook_runs
from .workflows import WorkflowService

logger = logging.getLogger(__name__)


class ListsFull(Exception):
    """Adding rows would exceed MAX_LIST_ROWS."""


class FoldersFull(Exception):
    """Creating a folder would exceed MAX_FOLDERS."""


class ListNotFound(Exception):
    """Missing OR foreign list (cross-tenant reads as not-found)."""


class FolderNotFound(Exception):
    """Missing OR foreign folder (cross-tenant reads as not-found)."""


class RowNotFound(Exception):
    """Missing row OR one outside the given list (reads as not-found)."""


class CellMismatch(NamedTuple):
    """One refused key and the why a user can act on."""

    key: str
    why: str


def _clamp_cell(key: str, value: str, *, where: str) -> str:
    """The cell ceiling, at the writer: authored input CLAMPS, never
    rejects (a batch must not fail over one long value), and the clamp
    is LOGGED when it bites, since a cut value is data the user or the
    model wrote that the sheet no longer holds in full."""
    if len(value) <= CELL_MAX_LENGTH:
        return value
    logger.warning("%s: cell %r clamped from %d to %d chars", where, key, len(value), CELL_MAX_LENGTH)
    return value[:CELL_MAX_LENGTH]


def cells_for_storage(
    types: dict[str, str], data: dict[str, str], *, where: str
) -> tuple[dict[str, str], list[CellTypeMismatch]]:
    """THE cell transform every write path funnels through, so none
    restitches it or runs the two steps in a different order: normalize
    each value to its type's canonical form (the contract's normalize_row)
    and clamp it to CELL_MAX_LENGTH (logging a clamp). Returns the values
    to store plus the shape mismatches the caller reacts to per its tier
    (an authored import tolerates them, a machine answer flags them, the
    ingest POST rejects them). The ingest POST also runs this to build the
    rows it publishes, so the bus carries exactly what add_rows will
    store (re-running it on append is idempotent: a canonical value
    re-normalizes unchanged and a clamped one is already at the bound)."""
    normalized, mismatches = normalize_row(types, data)
    stored = {key: _clamp_cell(key, value, where=where) for key, value in normalized.items()}
    return stored, mismatches


class CellWriteResult(NamedTuple):
    """One write_cells call's per-key verdicts: every attempted key
    lands in exactly one of these. Blank values are ABSENT from all
    three (a machine blank writes nothing and is not an event)."""

    written: tuple[str, ...]
    occupied: tuple[str, ...]
    mismatched: tuple[CellMismatch, ...]


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
    """Account-scoped: every method here reads or writes within one
    account. The one owner-stamping op, create, takes the owner as an
    explicit `owner_id` argument rather than the service carrying a
    user_id it would ignore everywhere else."""

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def create(
        self,
        *,
        owner_id: str,
        label: str,
        columns: Sequence[ListColumn],
        origin: str,
        origin_ref: str = "",
        folder_id: str = "",
    ) -> List:
        return List.objects.create(
            account_id=self.account_id,
            user_id=owner_id,
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

    def add_rows(self, target: List, rows: list[dict[str, str]]) -> list[ListRow]:
        """Append rows (each a data dict keyed by column keys). Positions
        are dense and 1-based; the count ceiling AND the cell clamp live
        here so every entry path (import, snapshot, manual) hits one
        writer's rules (authored values clamp, never reject). Returns the
        created rows (WITH ids, a ULID assigned before insert): a caller
        that only wants a count takes len(), and the push path needs the
        ids to enqueue autofill against them."""
        if not rows:
            return []
        # Through the shared write-time transform, so a stored value is its
        # type's canonical form (a pasted "1,234" -> "1234"), the same shape
        # the fill write path stores. Authored input TOLERATES a mismatch:
        # the mismatches are ignored (the raw value stores), because an
        # import must never fail a whole batch over one bad cell.
        types = {column.key: column.type for column in target.columns}
        stored_rows = []
        for data in rows:
            stored, _ = cells_for_storage(types, data, where="add_rows")  # mismatches ignored (tolerate)
            stored_rows.append(stored)
        rows = stored_rows
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
        return created

    def write_cells(self, list_id: str, row_id: str, cells: dict[str, str]) -> CellWriteResult:
        """THE cell writer for machine answers: write-if-blank per key,
        so a user's cell is never destroyed (rows accept arbitrary keys
        from import, snapshot, and manual entry, so nothing here is
        machine-owned by construction). Values clamp at CELL_MAX_LENGTH
        (authored input clamps, never rejects) and pass the column's
        shape validator before anything writes; a blank value writes
        nothing and reports nothing."""
        if not any(value.strip() for value in cells.values()):
            return CellWriteResult((), (), ())
        with transaction.atomic():
            # The hazard this guards is a read-modify-write of ONE
            # row's data, so the lock is on THAT ROW: without it two
            # concurrent fills can both see a cell blank and the later
            # commit clobbers the earlier value. Two fills writing
            # different rows never meet, which is what keeps the
            # worker's pool wide at the terminal write.
            #
            # The list is read UNLOCKED, for the column types only.
            # Appending a column cannot change an existing key's type,
            # and retyping an occupied column does not exist; when it
            # ships it takes the List lock itself.
            try:
                target = List.objects.get(id=list_id, account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(list_id) from e
            try:
                row = ListRow.objects.select_for_update().get(id=row_id, list_id=str(target.id))
            except ListRow.DoesNotExist as e:
                raise RowNotFound(row_id) from e
            types = {column.key: column.type for column in target.columns}
            # row.data is the row's stored cell values, keyed by column key.
            # Work on a mutable copy: this call's writes merge in, keys
            # outside it carry through, and the whole dict is persisted once.
            row_cells = dict(row.data)
            # Write-if-blank (a DB-state decision, not a shape one): only a
            # non-blank cell whose column is currently blank is a candidate
            # to write, so a user's value is never destroyed. The candidates
            # run through the shared write-time transform; this writer's
            # reaction to a mismatch is to FLAG it (TYPE_MISMATCH), never
            # reject.
            candidates = {
                key: value
                for key, value in cells.items()
                if value.strip() and not str(row_cells.get(key, "") or "").strip()
            }
            stored, mismatches = cells_for_storage(types, candidates, where="write_cells")
            why_by_key = {mismatch.key: mismatch.why for mismatch in mismatches}
            written: list[str] = []
            occupied: list[str] = []
            mismatched: list[CellMismatch] = []
            for key, value in cells.items():  # input order for the verdicts
                if not value.strip():
                    continue  # a blank writes nothing and reports nothing
                if key not in candidates:
                    occupied.append(key)
                elif key in why_by_key:
                    mismatched.append(CellMismatch(key=key, why=why_by_key[key]))
                else:
                    row_cells[key] = stored[key]
                    written.append(key)
            if written:
                # Only the data column writes, targeted by row id; the
                # merge base was read under the row's own lock, so keys
                # outside this call's writes carry through current.
                ListRow.objects.filter(id=row_id, list_id=str(target.id)).update(
                    data=row_cells, updated_at=timezone.now()
                )
        return CellWriteResult(tuple(written), tuple(occupied), tuple(mismatched))

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
        """Delete the list with its rows, fills, tasks, cell states, and
        workflow (nodes and paths) in one transaction (the service owns
        child cleanup; no
        cascades in this codebase, so anything left behind is orphaned
        forever and a live orphaned fill would hold one of the
        account's fill slots with nothing visible to cancel)."""
        with transaction.atomic():
            # Locked, not exists(): add_rows holds the List row while it
            # inserts, so the delete must wait for it or the row cleanup
            # runs BEFORE the insert commits, orphaning rows forever (no
            # cascades). Also account-scoped: callers pre-scope, but the
            # service must not rely on that.
            if not List.objects.select_for_update().filter(id=target.id, account_id=self.account_id):
                return
            fills = Fill.objects.filter(list_id=str(target.id))
            # ROWS FIRST, then the queue, because that is the order the
            # consumer's terminal write takes them: write_cells locks the
            # ListRow, then the task settle writes the NodeRun, both in
            # one transaction. Deleting the other way round is an ABBA
            # deadlock against any fill running on this sheet, and
            # Postgres resolves it by aborting one side: a 500 on the
            # delete, or a burned row attempt. Rows vanishing first is
            # already a state the worker handles (RowNotFound resolves
            # the fill CANCELLED, never failed).
            ListRow.objects.filter(list_id=str(target.id)).delete()
            # Bounded by the PARENT key alone, deliberately. The
            # account column is denormalized defence in depth for
            # READS; on a purge it is a liability, because a task whose
            # copy of it is blank would outlive the list forever, and
            # the fills queryset that produced these ids is already
            # list-scoped under an account-scoped lock.
            NodeRun.objects.filter(fill_run_id__in=[str(i) for i in fills.values_list("id", flat=True)]).delete()
            # The sheet's webhook runs go the same way, by list id: no
            # picker ever finds one through its row, so unlike an
            # autofill run it cannot retire itself.
            webhook_runs.purge_for_list(str(target.id))
            # The columns' ephemeral agents die with the columns that
            # owned them: nothing else can reach them once the fills are
            # gone, and they are excluded from the roster and its cap,
            # so a survivor is litter no surface can ever show. The ids
            # come from the FILL rows, which are list-scoped and current;
            # the caller's `target` may be a snapshot taken before the
            # column it is about to delete even existed.
            AgentService(account_id=self.account_id).delete_ephemeral(
                [str(agent_id) for agent_id in fills.values_list("agent_id", flat=True)]
            )
            # The fill-backed runs went first (they point at nodes). This
            # list's autofill runs are NOT purged: they carry list_id and a
            # node_id the next line orphans, and a claimed one finds its row
            # gone and settles ROW_MISSING before it reaches the node (the
            # autofill provisioner picks READY tasks globally, which is what
            # still claims them). The workflow is list-owned, so it is purged here
            # and nowhere else; the bench node has no workflow and is
            # untouched.
            WorkflowService(account_id=self.account_id).delete_for_list(str(target.id))
            cell_truth.purge_list(str(target.id))
            fills.delete()
            List.objects.filter(id=target.id, account_id=self.account_id).delete()
