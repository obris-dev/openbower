"""Column custody: blank-column adds (the CSV-template flow; a blank
column carries no fill member, and its key derives through the
runtime's ONE derivation rule, because a key the sheet already has
refuses an AI column that would land there and two derivation rules
would make that refusal unpredictable), the column-scoped fill-prompt
edit, and reordering. Account-scoped like every lists service."""

from __future__ import annotations

from django.db import transaction

from agents.runtime.answer import reserved_output_key
from agents.services import AgentService
from openbower_schema.agents import AgentConfig
from openbower_schema.lists import derive_column_key

from ..constants import MAX_LIST_COLUMNS, FillErrorCode
from ..models import List
from .fill_admission import FillColumnNotFound, ProviderRetiredRefusal
from .lists import ListNotFound


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
                target = List.objects.select_for_update().get(id=target_list_id, account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(target_list_id) from e
            key = derive_column_key(label)
            if not key or reserved_output_key(key):
                raise ReservedColumnKey(label=label)
            if key in {column["key"] for column in target.columns}:
                raise ColumnExists(key=key)
            if len(target.columns) >= MAX_LIST_COLUMNS:
                raise ColumnsFull()
            target.columns = [*target.columns, {"key": key, "label": label, "type": column_type}]
            target.save(update_fields=["columns", "updated_at"])
        return target

    def reorder(self, target_list_id: str, *, keys: list[str]) -> List:
        """Rewrite the columns array in the given order and return the
        updated list.

        The key SET must be unchanged, which is what keeps this from
        being a mutation door: every other columns writer decides what
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
                target = List.objects.select_for_update().get(id=target_list_id, account_id=self.account_id)
            except List.DoesNotExist as e:
                raise ListNotFound(target_list_id) from e
            by_key = {column["key"]: column for column in target.columns}
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
            if len(keys) != len(target.columns) or set(keys) != set(by_key):
                raise ColumnOrderStale()
            target.columns = [by_key[key] for key in keys]
            target.save(update_fields=["columns", "updated_at"])
        return target

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
            target = List.objects.get(id=target_list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(target_list_id) from e
        fill = next(
            (column.get("fill") for column in target.columns if column["key"] == column_key and column.get("fill")),
            None,
        )
        if fill is None:
            raise FillColumnNotFound(column_key)
        return agents.get_for_fill(str(fill.get("agent_id", "")))
