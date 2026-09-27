"""The column machinery of the AI column create and of a fill:
module-level functions over the sheet and the config, the create's and
the fill's ONE columns write each (append_columns, point_columns) and
the checks around them, two of which read the account's open fills.

The create turns an agent's outputs into columns (resolve, cap, append,
under the List lock). A fill never adds a column: an agent's output set
is fixed while its columns exist (an agent save refuses a change), so a
fill writes the columns the create made. It checks that they are free
and the account has room, once unlocked before a queue is built and
again under the lock, then points them at itself."""

from __future__ import annotations

from agents.runtime.answer import reserved_output_key
from openbower_schema.agents import AgentConfig
from openbower_schema.lists import COLUMN_LABEL_MAX_LENGTH, AiColumn, ListColumn

from ...constants import MAX_ACTIVE_FILLS, MAX_LIST_COLUMNS, RESERVED_COLUMN_KEYS
from ...models import List
from .. import fill_progress
from .errors import (
    AccountFillsFull,
    ColumnCollision,
    ColumnsFull,
    DerivedKeyCollision,
    FillColumnNotFound,
    ReservedColumnKey,
    SameColumnFillActive,
)


def resolve_columns(target_list: List, *, config: AgentConfig) -> list[str]:
    """The columns the create will add. Each output's OWN key IS its
    column key, single and multi alike (the outputs ARE the columns),
    which is why this returns a LIST and not a mapping. A key the sheet
    already holds refuses, whether or not it has values in it."""
    keys: list[str] = []
    claimed: dict[str, str] = {}
    for output in config.outputs:
        key = output.key
        if not key or reserved_output_key(key) or key in RESERVED_COLUMN_KEYS:
            raise ReservedColumnKey(label=output.label)
        if key in claimed:
            raise DerivedKeyCollision(first=claimed[key], second=output.label)
        claimed[key] = output.label
        keys.append(key)
    ai_keys = {column.key for column in target_list.columns if isinstance(column, AiColumn)}
    existing = {column.key for column in target_list.columns}
    # A key an open fill still writes counts as held too: its runs land
    # answers under the key until the fill ends, so a column added under
    # it now would receive them.
    under_fill = keys_under_fill(target_list)
    for key in keys:
        if key in existing or key in under_fill:
            raise ColumnCollision(key=key, filled=key in ai_keys or key in under_fill)
    return keys


def check_column_cap(target_list: List, *, column_keys: list[str]) -> None:
    """The column cap, for the keys the create is about to add."""
    if len(target_list.columns) + len(column_keys) > MAX_LIST_COLUMNS:
        raise ColumnsFull()


def append_columns(target_list: List, *, column_keys: list[str], config: AgentConfig, node_id: str) -> None:
    """The create's one columns write: a new AI column per key, bound to
    the node with its output's type, never run (no fill speaks for it
    yet). Type is fixed from here on: land_rows gates every value
    through it."""
    outputs_by_key = {output.key: output for output in config.outputs}
    columns: list[ListColumn] = list(target_list.columns)
    for key in column_keys:
        output = outputs_by_key[key]
        columns.append(
            AiColumn(key=key, label=output.label[:COLUMN_LABEL_MAX_LENGTH], type=output.type, node_id=node_id)
        )
    target_list.columns = columns
    target_list.save(update_fields=["columns", "updated_at"])


def require_fill_column(target_list: List, column_key: str) -> AiColumn:
    """The named AI column, or the 404-shaped refusal (a column the sheet
    does not have, a plain one, or a Send webhook column, which its
    barrier fills, is not a fill target). Whether a fill may START at
    its node is the path's question, asked by the caller."""
    column = next((column for column in target_list.columns if column.key == column_key), None)
    if not isinstance(column, AiColumn):
        raise FillColumnNotFound(column_key)
    return column


def keys_under_fill(target_list: List) -> set[str]:
    """Every column key an OPEN fill on this sheet owns. What a fill may
    not start on twice, and what no columns writer may add a column
    under: a fill's runs write its consent's keys until it ends, so a
    column re-added under one would receive a stale fill's answers."""
    taken: set[str] = set()
    open_here = fill_progress.open_fills().filter(target_id=str(target_list.id))
    for _fill_run_id, consent in fill_progress.iter_consents(open_here):
        taken.update(consent.column_keys)
    return taken


def check_columns_free(target_list: List, *, column_keys: list[str]) -> None:
    """No open fill on this sheet writes any of these columns. Asked
    before this request's own fill is opened, so it never refuses the
    request to itself."""
    taken = keys_under_fill(target_list)
    if any(key in taken for key in column_keys):
        raise SameColumnFillActive()


def check_account_cap(account_id: str) -> None:
    """The account cap: a plain count of the account's open fill jobs (a
    courtesy cap, not a ledger, so it takes no row locks and a burst of
    simultaneous fills at the boundary can overshoot by the burst).
    Asked before this request's own fill is opened."""
    account_live = fill_progress.open_fills().filter(account_id=account_id).count()
    if account_live >= MAX_ACTIVE_FILLS:
        raise AccountFillsFull()


def point_columns(target_list: List, *, column_keys: list[str], fill_run_id: str) -> None:
    """A fill's one columns write, under the List lock: each column it
    writes learns which fill now speaks for it. Stored rather than
    derived per read, which keeps the four second poll off a walk of
    every fill the sheet has had. Nothing else about a column changes."""
    columns: list[ListColumn] = []
    for column in target_list.columns:
        if column.key in column_keys and isinstance(column, AiColumn):
            column = column.model_copy(update={"current_fill_id": fill_run_id})
        columns.append(column)
    target_list.columns = columns
    target_list.save(update_fields=["columns", "updated_at"])
