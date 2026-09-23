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
from jobs.services import JobService
from openbower_kernel.fields import is_valid_ulid
from openbower_kernel.ranks import RankError, key_between, keys_between, respace_keys
from openbower_kernel.ranks import validate as validate_rank
from openbower_schema.cell_types import CellTypeMismatch, normalize_row
from openbower_schema.lists import ListColumn

from ..cells.writes import CellWrite, LandingContext, RowLanding
from ..constants import (
    CELL_MAX_LENGTH,
    FILL_WRITE_BATCH,
    MAX_FOLDERS,
    MAX_LIST_ROWS,
    RANK_MAX_LENGTH,
    RANK_REBALANCE_LENGTH,
    StoredCellState,
)
from ..models import Folder, List, ListRow, NodeRun
from . import cell_truth, fill_progress, webhook_runs
from .cell_truth import CellRecord
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


class RowRankTooDeep(Exception):
    """A move would write a rank past the column bound: the gap has been
    split past what a re-space has caught up with. The move must wait
    for the sheet's re-space."""


class FillsOpen(Exception):
    """A re-space refused because the list has open fills: a walk's
    cursor holds a key of the old spacing and would land somewhere else
    under the new one."""


class InvalidRowCursor(Exception):
    """A rows cursor this server did not write."""


# Neither alphabet contains a dot (ranks are base 62, ids Crockford
# base 32), so a cursor splits unambiguously.
_CURSOR_SEPARATOR = "."


class RowCursor(NamedTuple):
    """Where a page of rows in sheet order continues from: the last row
    seen, its id and its rank. Self-contained, so the client can hand
    it straight back as the page's `next_cursor` (`wire` / `parse`) and
    the next page needs no lookup."""

    row_id: str
    rank: str

    def wire(self) -> str:
        """The opaque string a client echoes: rank, a dot, id. Neither
        alphabet contains a dot (ranks are base 62, ids Crockford base
        32), so the split is unambiguous."""
        return f"{self.rank}{_CURSOR_SEPARATOR}{self.row_id}"

    @classmethod
    def parse(cls, raw: str) -> RowCursor:
        """Read a cursor a client sent back. Raises InvalidRowCursor for
        anything this server did not write (a bare id, a stale format,
        garbage), which the view answers as a 400."""
        rank, separator, row_id = raw.partition(_CURSOR_SEPARATOR)
        if not separator or not is_valid_ulid(row_id):
            raise InvalidRowCursor(raw)
        try:
            validate_rank(rank)
        except RankError as e:
            raise InvalidRowCursor(raw) from e
        return cls(row_id, rank)


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
    """A landing's verdict on EVERY write it carried for one row: each
    lands in exactly one of these. A value landed (written), a value
    was already there and write-if-blank kept it (occupied), the
    column's shape refused the value (mismatched), or the write
    carried no value, or a blank one (unanswered: its own state was
    recorded)."""

    written: tuple[str, ...]
    occupied: tuple[str, ...]
    mismatched: tuple[CellMismatch, ...]
    unanswered: tuple[str, ...]


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
        """Append rows (each a data dict keyed by column keys), ranked
        after the sheet's last row; the count ceiling AND the cell clamp
        live here so every entry path (import, snapshot, manual) hits one
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
            # Ranks allocate after the sheet's last row, so concurrent
            # appends must serialize on the list row or the second one
            # collides with the (list_id, rank) unique constraint.
            try:
                locked = List.objects.select_for_update().get(id=str(target.id), account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(str(target.id)) from e
            current = ListRow.objects.filter(list_id=str(locked.id)).count()
            if current + len(rows) > MAX_LIST_ROWS:
                raise ListsFull(f"a list holds at most {MAX_LIST_ROWS} rows")
            last = (
                ListRow.objects.filter(list_id=str(locked.id))
                .order_by("-rank", "-id")
                .values_list("rank", flat=True)
                .first()
            )
            created = [
                ListRow(list_id=str(locked.id), rank=rank, data=data)
                for rank, data in zip(keys_between(last, None, len(rows)), rows, strict=True)
            ]
            ListRow.objects.bulk_create(created, batch_size=1000)
            locked.row_count = current + len(created)
            locked.save(update_fields=["row_count", "updated_at"])
        return created

    def land_row(self, ctx: LandingContext, landing: RowLanding) -> CellWriteResult:
        """One row's landing: see land_rows."""
        return self.land_rows(ctx, [landing])[landing.row_id]

    def land_rows(self, ctx: LandingContext, landings: Sequence[RowLanding]) -> dict[str, CellWriteResult]:
        """THE cell writer, kind-blind: per row, the writes' values onto
        the sheet row (write-if-blank, through the column's type, one
        row lock; a writer names its facts, the landing has the last
        word) and then one record per write onto the cell ledger for
        the whole batch, in ONE upsert, so a value and the record that
        says what it is can never be written apart. The record's state
        is the write's intent corrected by what the row reported: a
        value that landed or found the cell occupied is FILLED, one the
        column's shape refused is TYPE_MISMATCH, and a write with no
        value records the state it intends (an agent's cause, a send's
        SENT or FAILED). FILLED is only ever DERIVED from the row: a
        write that meant a value and landed none records nothing. Runs inside the caller's transaction, which
        closes its runs after, in the deletes' lock order: ListRow,
        ListCellState, NodeRun. Returns each row's verdict by row id."""
        by_row: dict[str, list[CellWrite]] = {}
        for landing in landings:
            by_row.setdefault(landing.row_id, []).extend(landing.writes)
        verdicts: dict[str, CellWriteResult] = {}
        records: list[CellRecord] = []
        for row_id, writes in by_row.items():
            values = {write.key: write.value for write in writes if write.value is not None}
            written = self._write_values(ctx.list_id, row_id, values)
            filled = {*written.written, *written.occupied}
            refused = {mismatch.key for mismatch in written.mismatched}
            unanswered: list[str] = []
            for write in writes:
                if write.key in filled:
                    state = StoredCellState.FILLED
                elif write.key in refused:
                    state = StoredCellState.TYPE_MISMATCH
                else:
                    unanswered.append(write.key)
                    if write.state is StoredCellState.FILLED:
                        # A blank meant as a value: nothing landed, and the
                        # writer named no state for a blank (a person's), so
                        # nothing is recorded; the cell stays as it was.
                        continue
                    state = write.state
                records.append(CellRecord(row_id, write.key, state, write.tools))
            verdicts[row_id] = CellWriteResult(written.written, written.occupied, written.mismatched, tuple(unanswered))
        cell_truth.write_records(
            account_id=self.account_id,
            list_id=ctx.list_id,
            records=records,
            fill_run_id=ctx.fill_run_id,
            source=ctx.source,
        )
        return verdicts

    def _write_values(self, list_id: str, row_id: str, cells: dict[str, str]) -> CellWriteResult:
        """The value half of a landing: the sheet row alone. A blank
        value writes nothing, and the row is not even locked when every
        value is blank."""
        if not any(value.strip() for value in cells.values()):
            return CellWriteResult((), (), (), ())
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
            for key, value in cells.items():  # column order for the verdicts
                if not value.strip():
                    continue  # unanswered, named above
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
        return CellWriteResult(tuple(written), tuple(occupied), tuple(mismatched), ())

    def rows_page(
        self, target: List, *, after: RowCursor | None = None, limit: int, until_id: str = ""
    ) -> list[ListRow]:
        """A page of rows in SHEET ORDER after a cursor: ranks are unique
        per sheet, so the keyset is on the rank alone (the id rides the
        cursor to name the row, and breaks no tie). With
        `until_id`, none newer than it (a fill's consent set: the rows
        that existed at the click). The cursor's rank is the truth: a
        keyset needs no row to exist (a walk's cursor row deleted
        mid-walk is fine), and the one thing that rewrites ranks, the
        re-space a deep move queues, waits for the list's open fills
        (`respace`), while the sheet's own scroll tolerates the one
        overlapping page it could see. A row moved from below the cursor
        to above it between two pages is not walked, exactly like a row
        appended after the click; one moved the other way is offered
        twice, and the open-run key drops the second."""
        rows = ListRow.objects.filter(list_id=str(target.id))
        if until_id:
            rows = rows.filter(id__lte=until_id)
        if after is not None:
            rows = rows.filter(rank__gt=after.rank)
        return list(rows.order_by("rank", "id")[:limit])

    def move_row(self, target: List, row_id: str, *, after_id: str | None) -> ListRow:
        """Put the row right after `after_id` (None: at the top). ONE
        write, on the moved row: its new rank is a key between its two
        new neighbours; none at all when it already sits there (a row
        dropped on itself or on the row it follows), so a no-op never
        deepens a key. Under the list lock, so two moves into the same
        gap cannot compute the same key. A key past RANK_REBALANCE_LENGTH
        means the gap has been split too many times: the sheet's ranks
        are re-spaced by a job, after this write commits."""
        with transaction.atomic():
            locked = List.objects.select_for_update().filter(id=str(target.id), account_id=self.account_id).first()
            if locked is None:
                raise ListNotFound(str(target.id))
            row = ListRow.objects.filter(id=row_id, list_id=str(locked.id)).first()
            if row is None:
                raise RowNotFound(row_id)
            if after_id == row_id:
                return row
            others = ListRow.objects.filter(list_id=str(locked.id)).exclude(id=row_id)
            if after_id is None:
                before_rank = None
                nxt = others.order_by("rank", "id").only("rank").first()
            else:
                before = others.filter(id=after_id).only("rank").first()
                if before is None:
                    raise RowNotFound(after_id)
                before_rank = before.rank
                nxt = others.filter(rank__gt=before.rank).order_by("rank", "id").only("rank").first()
            # Ranks are unique per sheet, so "already between" is strict.
            above = before_rank is None or before_rank < row.rank
            below = nxt is None or row.rank < nxt.rank
            if above and below:
                return row
            key = key_between(before_rank, nxt.rank if nxt is not None else None)
            if len(key) > RANK_MAX_LENGTH:
                raise RowRankTooDeep(row_id)
            row.rank = key
            row.save(update_fields=["rank", "updated_at"])
            if len(row.rank) > RANK_REBALANCE_LENGTH:
                # The kind imports this service; the edge back is local.
                from ..jobs.rerank import Rerank

                jobs = JobService(account_id=self.account_id)
                if not jobs.has_open(Rerank, target_id=str(locked.id)):
                    jobs.enqueue_system(Rerank(list_id=str(locked.id)), target_id=str(locked.id))
        return row

    def respace(self, list_id: str) -> None:
        """Re-space the sheet's ranks: the same order, fresh keys with no
        fractional tail. One transaction under the list lock, so a move
        cannot interleave a key computed against the old spacing; a
        sheet holds at most MAX_LIST_ROWS rows, so the lock is held for
        seconds at the worst. Refused (FillsOpen) while the list has an
        open fill, checked under the lock: a walk's cursor holds a key
        of the old spacing. A fill admitted after that check reads its
        first page no earlier than this transaction, and its cursor
        cannot straddle the commit unless that page is read during the
        seconds the lock is held; the walk takes no lock by design.

        The fresh keys are disjoint from the old ones (respace_keys says
        why: the unique index is checked row by row as the write
        proceeds), so no write order can collide."""
        with transaction.atomic():
            locked = List.objects.select_for_update().filter(id=list_id, account_id=self.account_id).first()
            if locked is None:
                raise ListNotFound(list_id)
            if fill_progress.open_fills().filter(target_id=list_id).exists():
                raise FillsOpen(list_id)
            rows = list(ListRow.objects.filter(list_id=list_id).order_by("rank", "id").only("id", "rank"))
            if not rows:
                return
            fresh = respace_keys([row.rank for row in rows])
            for row, rank in zip(rows, fresh, strict=True):
                row.rank = rank
            ListRow.objects.bulk_update(rows, ["rank"], batch_size=FILL_WRITE_BATCH)

    def column_values(self, target: List, *, key: str, limit: int) -> list[str]:
        """One column's non-empty values in sheet order (the use-time
        read behind seeding: the CALLER interprets them). Walks the rows
        server-side so a 25k-row sheet never round-trips to the browser
        just to extract a column."""
        out: list[str] = []
        after: RowCursor | None = None
        while len(out) < limit:
            rows = self.rows_page(target, after=after, limit=1000)
            if not rows:
                break
            after = RowCursor(str(rows[-1].id), rows[-1].rank)
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
            fills = fill_progress.fill_jobs().filter(target_id=str(target.id))
            # ROWS FIRST, then their truth, then the queue, because that
            # is the order a landing takes them: write_cells locks the
            # ListRow and writes its ListCellState records, then the
            # task settle writes the NodeRun, all in one transaction.
            # Deleting in another order is an ABBA deadlock against any
            # fill running on this sheet, which Postgres resolves by
            # aborting one side: a 500 on the delete, or a burned row
            # attempt. Rows vanishing first is already a state the
            # worker handles (RowNotFound resolves the fill CANCELLED,
            # never failed).
            ListRow.objects.filter(list_id=str(target.id)).delete()
            cell_truth.purge_list(str(target.id))
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
            # come from the FILL jobs, which are list-scoped and current;
            # the caller's `target` may be a snapshot taken before the
            # column it is about to delete even existed.
            AgentService(account_id=self.account_id).delete_ephemeral(
                [consent.agent_id for _fill_run_id, consent in fill_progress.iter_consents(fills)]
            )
            # The fill-backed runs went first (they point at nodes). This
            # list's autofill runs are NOT purged: they carry list_id and a
            # node_id the next line orphans, and a claimed one finds its row
            # gone and settles ROW_MISSING before it reaches the node (the
            # autofill provisioner picks READY tasks globally, which is what
            # still claims them). The workflow is list-owned, so it is purged here
            # and nowhere else; the preview node has no workflow and is
            # untouched.
            # Its preview runs carry no list and no fill, so nothing here
            # reaches them either; the cron prunes them by age.
            WorkflowService(account_id=self.account_id).delete_for_list(str(target.id))
            # Every job of the list's goes with it, by target: the fills,
            # a webhook backfill, a re-space (one delete; a tick holding
            # one of them finds no list on its next slice and ends, its
            # settle missing on the deleted row).
            JobService(account_id=self.account_id).delete_for_target(str(target.id))
            List.objects.filter(id=target.id, account_id=self.account_id).delete()
