"""Column custody: blank-column adds (the CSV-template flow; a blank
column carries no fill member, and its key derives through the
runtime's ONE derivation rule, because a key the sheet already has
refuses an AI column that would land there and two derivation rules
would make that refusal unpredictable), the column-scoped fill-prompt
edit, and reordering. Account-scoped like every lists service."""

from __future__ import annotations

from django.db import models, transaction
from django.db.models import Value

from agents.runtime.answer import reserved_output_key
from agents.services import AgentService
from openbower_schema.agents import AgentConfig
from openbower_schema.lists import derive_column_key

from ..constants import LIVE_FILL_STATUSES, MAX_LIST_COLUMNS, FillErrorCode, FillStatus
from ..models import Fill, List, ListRow
from . import cell_truth
from .fill_admission import FillColumnNotFound, ProviderRetiredRefusal
from .fill_progress import stop_fill
from .lists import ListNotFound


class _JsonbWithoutKey(models.Func):
    """Postgres `data - 'key'`: the row's JSON minus one key, applied
    by the database so a column's values leave every row in ONE
    statement instead of a read-modify-write per row."""

    arg_joiner = " - "
    template = "%(expressions)s"
    output_field = models.JSONField()


class ColumnRefused(Exception):
    """Base for column-write refusals (adds and reorders alike): `code`
    is the machine leg the view maps to a status, str(self) is
    server-authored copy the client renders verbatim (tier 1)."""

    code = FillErrorCode.COLUMN_REFUSED


class ReservedColumnKey(ColumnRefused):
    """The label derives to nothing, or to a key the answer model
    reserves."""

    code = FillErrorCode.RESERVED_KEY

    def __init__(self, *, label: str) -> None:
        super().__init__(f"{label!r} maps to a reserved column key; pick a different name.")


class ColumnExists(ColumnRefused):
    code = FillErrorCode.COLUMN_EXISTS

    def __init__(self, *, key: str) -> None:
        super().__init__(f"A column named {key} already exists.")


class ColumnsFull(ColumnRefused):
    code = FillErrorCode.COLUMNS_FULL

    def __init__(self) -> None:
        super().__init__(f"A sheet holds at most {MAX_LIST_COLUMNS} columns.")


class ColumnOrderStale(ColumnRefused):
    """The submitted order does not name exactly the columns the sheet
    has. Never a mutation: reorder is the one columns write that adds,
    drops, and renames nothing, so a mismatch means the CLIENT's view
    is stale (a teammate added or deleted a column while this one was
    dragged), and the honest answer is to say so rather than to guess
    which half of the disagreement was meant."""

    code = FillErrorCode.COLUMN_ORDER_STALE

    def __init__(self) -> None:
        super().__init__("This sheet's columns changed while you were reordering; try the move again.")


class ColumnKeysNotUnique(ColumnRefused):
    """A repeat in the submitted order. Distinct from stale: no change
    to the world makes this request right, so it answers 400 and says
    so, rather than borrowing the stale refusal's "try again"."""

    code = FillErrorCode.COLUMN_KEYS_NOT_UNIQUE

    def __init__(self) -> None:
        super().__init__("That reorder named the same column twice.")


class ColumnNotFound(Exception):
    """No column on this sheet holds that key."""

    def __init__(self, key: str) -> None:
        super().__init__(f"no column {key}")


class ColumnService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def add_column(self, target_list_id: str, *, label: str, column_type: str) -> List:
        """Append one blank column and return the updated list."""
        with transaction.atomic():
            # The same List lock every columns writer takes: without it
            # a concurrent add (or a fill's admission) can append over
            # this read and one write silently drops the other's column.
            try:
                target_list = List.objects.select_for_update().get(id=target_list_id, account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(target_list_id) from e
            key = derive_column_key(label)
            if not key or reserved_output_key(key):
                raise ReservedColumnKey(label=label)
            if key in {column["key"] for column in target_list.columns}:
                raise ColumnExists(key=key)
            if len(target_list.columns) >= MAX_LIST_COLUMNS:
                raise ColumnsFull()
            target_list.columns = [*target_list.columns, {"key": key, "label": label, "type": column_type}]
            target_list.save(update_fields=["columns", "updated_at"])
        return target_list

    def reorder(self, target_list_id: str, *, keys: list[str]) -> List:
        """Rewrite the columns array in the given order and return the
        updated list.

        The key SET must be unchanged, which is what keeps this from
        being a mutation path: every other columns writer decides what
        a column IS, and this one may only decide where it sits. It
        carries each column's dict across VERBATIM, so a fill member,
        a type, and a label cannot be edited through an ordering
        request even if the caller sends them.

        The same List lock every columns writer takes, for the same
        reason: a concurrent add appends to the array this read is
        about to replace, and without the lock one write drops the
        other's column."""
        with transaction.atomic():
            try:
                target_list = List.objects.select_for_update().get(id=target_list_id, account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(target_list_id) from e
            by_key = {column["key"]: column for column in target_list.columns}
            # A repeat is judged FIRST and separately, because it is
            # the request being wrong rather than the sheet having
            # moved, and the two owe the caller different answers.
            if len(keys) != len(set(keys)):
                raise ColumnKeysNotUnique()
            # Length AND membership, against the STORED list rather
            # than the map: by_key is already deduped, so a sheet whose
            # columns somehow held a repeat would let an honest request
            # naming each key once pass both checks and quietly drop
            # one. Reorder adds and drops nothing, including that.
            if len(keys) != len(target_list.columns) or set(keys) != set(by_key):
                raise ColumnOrderStale()
            target_list.columns = [by_key[key] for key in keys]
            target_list.save(update_fields=["columns", "updated_at"])
        return target_list

    def _locked(self, target_list_id: str) -> List:
        """The List row every columns writer takes before touching the
        array, so a concurrent add cannot append to the version this
        read is about to replace."""
        try:
            return List.objects.select_for_update().get(id=target_list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(target_list_id) from e

    def rename(self, target_list_id: str, *, key: str, label: str) -> List:
        """Relabel one column. The KEY never moves, and that is the
        whole design: row data is a dict keyed on it, FillCellState
        references it, and an agent output maps down to it, so a key
        that followed the label would strand every cell the column
        holds. A column relabelled "Decision maker" keeps whatever key
        it was born with, which is correct because the key is
        internal and the label is the user's."""
        with transaction.atomic():
            target_list = self._locked(target_list_id)
            columns = list(target_list.columns)
            for column in columns:
                if column["key"] == key:
                    column["label"] = label
                    break
            else:
                raise ColumnNotFound(key)
            target_list.columns = columns
            target_list.save(update_fields=["columns", "updated_at"])
        return target_list

    def delete(self, target_list_id: str, *, key: str) -> List:
        """Delete one column and everything it holds, in ONE
        transaction: the values in every row, the cell states, the
        column entry, and the ephemeral agent if nothing else needs it.

        ANY column, not just an AI one. A plain column is the same
        operation with less to clean up, and a sheet the user cannot
        tidy is the worse failure.

        The ORDER is the worker's order: row data first, then the
        queue. The worker's terminal write locks the ListRow and then
        writes the FillTask in one transaction, so taking them the
        other way round here is an ABBA deadlock against any fill
        running on this sheet, which Postgres resolves by aborting one
        side. Purging the values first also means a worker that was
        mid-row blocks on the row lock and finds its task abandoned
        when it wakes, so it writes the deleted key back to nothing."""
        with transaction.atomic():
            target_list = self._locked(target_list_id)
            doomed = next((column for column in target_list.columns if column["key"] == key), None)
            if doomed is None:
                raise ColumnNotFound(key)
            # Read BEFORE the column leaves the array; afterwards there
            # is nothing left to read it from.
            agent_id = str((doomed.get("fill") or {}).get("agent_id", ""))
            columns = [column for column in target_list.columns if column["key"] != key]

            # ONE UPDATE over the sheet's rows, so an O(rows) write
            # dissolves inside the transaction rather than stranding
            # data invisibly. It touches rows that never held the key
            # too, and that is accepted: narrowing it means asking the
            # blob what it contains, and NOTHING in this codebase
            # queries row data (FillCellState exists so counting
            # filled cells never has to). The blob is storage; the
            # structured record is what answers questions about it.
            ListRow.objects.filter(list_id=str(target_list.id)).update(data=_JsonbWithoutKey("data", Value(key)))

            # Every fill that touched this column stops. A fill can own
            # SEVERAL columns (one multi-output agent makes them
            # together), so a live sibling is stopped too rather than
            # left writing into a column that no longer exists; the
            # sibling refills.
            for fill_id in Fill.objects.filter(
                list_id=str(target_list.id),
                status__in=LIVE_FILL_STATUSES,
                column_keys__contains=[key],
            ).values_list("id", flat=True):
                stop_fill(str(fill_id), FillStatus.CANCELLED)

            cell_truth.purge_column(str(target_list.id), key)
            target_list.columns = columns
            target_list.save(update_fields=["columns", "updated_at"])

            # The ephemeral agent dies with the LAST column that used
            # it, never with the first: a multi-output agent's other
            # columns still need their config readable.
            self._retire_ephemeral(target_list, agent_id=agent_id)
        return target_list

    def _retire_ephemeral(self, target_list: List, *, agent_id: str) -> None:
        if not agent_id:
            return
        if any((column.get("fill") or {}).get("agent_id") == agent_id for column in target_list.columns):
            return
        AgentService(account_id=self.account_id, user_id=self.user_id).delete_ephemeral([agent_id])

    def fill_config(self, target_list_id: str, *, column_key: str) -> AgentConfig:
        """The CURRENT config filling a column (what a refill would
        run), read through the column's custody path. A retired
        provider still reads (peeking is not acting); only the writes
        below refuse it."""
        agents = AgentService(account_id=self.account_id, user_id=self.user_id)
        return self._fill_agent(target_list_id, column_key=column_key, agents=agents).config()

    def update_fill_prompt(self, target_list_id: str, *, column_key: str, prompt: str) -> AgentConfig:
        """Edit the PROMPT of the agent filling a column, from the
        column (the column is the custody path whether the agent is
        ephemeral or roster; the builder stays the roster's full
        editor). Only the prompt moves: the rest of the config
        round-trips through the row untouched. No List lock: the write
        lands on the agent row, and running fills hold their frozen
        snapshot, so the edit reaches the NEXT admission by
        construction. Returns the stored config."""
        agents = AgentService(account_id=self.account_id, user_id=self.user_id)
        agent = self._fill_agent(target_list_id, column_key=column_key, agents=agents)
        # update() persists the whole config, so a coerced substitute
        # spec would silently overwrite the stored provider here; the
        # row must be re-saved against a current provider first.
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        stored = agent.config().model_copy(update={"prompt": prompt})
        agents.update(agent, config=stored)
        return stored

    def _fill_agent(self, target_list_id: str, *, column_key: str, agents: AgentService):
        try:
            target_list = List.objects.get(id=target_list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(target_list_id) from e
        fill = next(
            (
                column.get("fill")
                for column in target_list.columns
                if column["key"] == column_key and column.get("fill")
            ),
            None,
        )
        if fill is None:
            raise FillColumnNotFound(column_key)
        return agents.get_for_fill(str(fill.get("agent_id", "")))
