"""The column half of a NORMAL admission: how a set of outputs becomes
columns a fill may write, plus the account cap both sequences judge.
Pure functions over the sheet and the config, sequenced twice:
`preview_columns` unlocked (the deterministic refusals, before a
queue is built) and `claim_columns` under the List lock (the
judgement that counts). The cap lives HERE, both halves: the preview
already read its lock-free half, and a guard split across two files
is how the passes drift."""

from __future__ import annotations

from agents.runtime.answer import reserved_output_key
from openbower_schema.agents import AgentConfig
from openbower_schema.lists import COLUMN_LABEL_MAX_LENGTH, AiColumn, ListColumn

from ...constants import LIVE_FILL_STATUSES, MAX_ACTIVE_FILLS, MAX_LIST_COLUMNS
from ...models import Fill, List
from ..fill_progress import live_fill_count
from .errors import (
    AccountFillsFull,
    ColumnCollision,
    ColumnsFull,
    ColumnTypeChanged,
    DerivedKeyCollision,
    FillColumnNotFound,
    ReservedColumnKey,
    SameColumnFillActive,
)


def claim_columns(
    target_list: List,
    *,
    config: AgentConfig,
    node_id: str,
    fill_run_id: str,
    account_id: str,
    owned: frozenset[str] = frozenset(),
) -> list[str]:
    """Resolve, guard, append: the ONE sequence that turns a set of
    outputs into columns a fill may write, run by BOTH normal admission
    paths because both open a fill that writes cells.

    Refill used to take the keys straight off the config and skip
    all three. A roster agent's outputs can change between fills
    (the snapshot is re-derived on purpose so edits apply), so a
    refill could open a fill owning a column the sheet did not
    have: every row spent a completion, the value landed in
    ListRow.data under a key no surface renders, and the cell
    records were filtered out of every count. It skipped the
    reserved-key and collision refusals for the same reason.

    `owned` names the keys the caller has already established it
    may write; see the collision rule below."""
    # The LIVE-FILL refusal goes first, before existence. A second
    # admit on a column a fill is already writing is exactly that,
    # and saying "this sheet already has that column" instead would
    # be true and useless: the column is there because the fill the
    # user just started put it there.
    # The fill being opened is one value wearing two roles: the id
    # the guards must EXCLUDE (it is already live) and the id the
    # claimed columns record as current. One REQUIRED parameter,
    # because a default here would stamp current_fill_id="" (the
    # contract's "column predates the write") silently.
    check_columns_free(target_list, column_keys=[output.key for output in config.outputs], opening=fill_run_id)
    column_keys = resolve_columns(target_list, config=config, owned=owned)
    # The account cap before the column arithmetic (an account at both
    # caps must hear fills_full, the refusal waiting fixes); both run
    # AGAIN here under the lock because the preview judged an unlocked
    # read, and this is the judgement that counts.
    check_account_cap(account_id, opening=fill_run_id)
    check_column_cap(target_list, column_keys=column_keys)
    # No retype set and no occupancy probe: a column that exists
    # keeps the type it was created with, and resolution above has
    # already refused both an existing key we do not own and an
    # owned one whose output changed shape.
    append_columns(target_list, column_keys=column_keys, config=config, node_id=node_id, fill_run_id=fill_run_id)
    return column_keys


def preview_columns(
    target_list: List, *, config: AgentConfig, account_id: str, owned: frozenset[str] = frozenset()
) -> list[str]:
    """The keys this fill will own, judged WITHOUT taking a lock.

    The keys themselves come from the OUTPUTS, never from the
    sheet, so this cannot disagree with what the locked claim
    decides; the sheet only decides whether to REFUSE, and this
    runs the refusals that cost nothing so an admission doomed by
    an existing column does not build a queue first.

    It mirrors the claim's order (live fills before existence, so
    a second admit on a column a fill is writing hears that, not
    the useless "this sheet already has that column"), and it runs
    every DETERMINISTIC refusal: an account at its fill cap or a
    sheet at its column cap would otherwise build up to 50,000
    NodeRun rows before hearing a "no" that was knowable up
    front, every time rather than rarely. The cap is read through
    live_fill_count, the lock-free count;
    the select_for_update half of the cap stays locked-only,
    because taking fill-row locks here would invert the
    List-then-Fill order its serialization depends on. The claim,
    under the List lock at the end of admission, is the judgement
    that counts."""
    check_columns_free(target_list, column_keys=[output.key for output in config.outputs])
    keys = resolve_columns(target_list, config=config, owned=owned)
    # The cap BEFORE the column arithmetic, mirroring the claim:
    # an account at both caps must hear fills_full (a 409, "wait
    # for one to finish") and not columns_full (a 400, "change the
    # request"), because waiting actually fixes the first and the
    # second would send them off to delete columns for nothing.
    if live_fill_count(account_id) >= MAX_ACTIVE_FILLS:
        raise AccountFillsFull()
    check_column_cap(target_list, column_keys=keys)
    return keys


def check_account_cap(account_id: str, *, opening: str = "") -> None:
    """The locked half of the account cap. NORMAL admissions only: the
    bench must always answer, so the test kind never runs this
    (supersede bounds that lane at one live test), while live tests
    still COUNT here because they spend like any fill.

    The cap must serialize ACROSS lists (the List lock only covers
    same-list admits): lock the account's live fill rows in id order
    so concurrent admits at the cap boundary queue here, then count in
    a fresh statement, which sees fills committed while this one
    waited on the locks. Lock order is List row first (the caller's
    own lock), then fill rows; the worker's fill-row locks run in
    their own transactions with no List lock held, so the order cannot
    invert. A burst of first-ever admits on an idle account has
    nothing to lock and can still overshoot, bounded by the
    simultaneous requests. `opening` is the admission's own fill,
    excluded from the locks and the count (it is already live by the
    time the cap is judged; counting it would refuse one fill early)."""
    list(
        Fill.objects.select_for_update()
        .filter(account_id=account_id, status__in=LIVE_FILL_STATUSES)
        .exclude(id=opening)
        .order_by("id")
        .only("id")
    )
    account_live = Fill.objects.filter(account_id=account_id, status__in=LIVE_FILL_STATUSES).exclude(id=opening).count()
    if account_live >= MAX_ACTIVE_FILLS:
        raise AccountFillsFull()


def require_fill_column(target_list: List, column_key: str) -> AiColumn:
    """The named AI column, or the 404-shaped refusal (a column the
    sheet does not have, or a plain one, is not a refill target)."""
    column = next((column for column in target_list.columns if column.key == column_key), None)
    if not isinstance(column, AiColumn):
        raise FillColumnNotFound(column_key)
    return column


def resolve_columns(target_list: List, *, config: AgentConfig, owned: frozenset[str] = frozenset()) -> list[str]:
    """The columns this fill will own. Each output's OWN key IS its
    column key, single and multi alike (the outputs ARE the
    columns), which is why this returns a LIST and not a mapping.
    Each key must be one this fill already owns or one no column
    holds; any other existing key refuses, whether or not it has
    values in it. Absent keys become new columns."""
    keys: list[str] = []
    claimed: dict[str, str] = {}
    for output in config.outputs:
        key = output.key
        if not key or reserved_output_key(key):
            raise ReservedColumnKey(label=output.label)
        if key in claimed:
            raise DerivedKeyCollision(first=claimed[key], second=output.label)
        claimed[key] = output.label
        keys.append(key)
    stored_types = {column.key: column.type for column in target_list.columns}
    ai_keys = {column.key for column in target_list.columns if isinstance(column, AiColumn)}
    outputs_by_key = {output.key: output for output in config.outputs}
    for key in keys:
        # `owned` is what the caller has already established it may
        # write: a refill's own columns exist BECAUSE it made them,
        # so the existence rule would otherwise refuse every refill.
        if key not in owned:
            if key in stored_types:
                raise ColumnCollision(key=key, filled=key in ai_keys)
            continue
        wanted = outputs_by_key[key].type
        if stored_types.get(key, wanted) != wanted:
            raise ColumnTypeChanged(key=key, stored=stored_types[key], wanted=wanted)
    return keys


def check_column_cap(target_list: List, *, column_keys: list[str]) -> None:
    """ONE spelling of the column-cap arithmetic, called by the
    preview and the locked claim: two hand-spelled copies are how
    the passes drift, and this file has the receipts."""
    new_keys = set(column_keys) - {column.key for column in target_list.columns}
    if len(target_list.columns) + len(new_keys) > MAX_LIST_COLUMNS:
        raise ColumnsFull()


def check_columns_free(target_list: List, *, column_keys: list[str], opening: str = "") -> None:
    """`opening` is the fill this admission just created, if the
    claim runs after it. Admission opens the fill BEFORE taking the
    List lock, so without this the guard finds our own live fill on
    our own column and refuses the admission to itself."""
    live = Fill.objects.filter(
        list_id=str(target_list.id),
        status__in=LIVE_FILL_STATUSES,
    ).exclude(id=opening)
    taken = {key for fill in live for key in fill.column_keys or ()}
    if taken & set(column_keys):
        raise SameColumnFillActive()


def append_columns(
    target_list: List, *, column_keys: list[str], config: AgentConfig, node_id: str, fill_run_id: str
) -> None:
    """THE one columns write of an admission: new AI columns append
    bound to the node with the output's type, an existing AI column of
    this node is re-pointed, and every claimed column learns which
    fill now speaks for it.

    Both facts ride ONE write because the fill row already exists
    when this runs: admission opens the fill and its queue before
    taking the List lock, so there is nothing left to fill in
    afterwards. Storing `current_fill_id` here rather than
    re-deriving it per read is what keeps the four second poll off
    a walk of every fill the sheet has ever had, newest first.

    A column NEVER changes type here. Type is not display-only at
    the write seam, where write_cells gates every value through the
    column's validator, so a retype under existing answers makes
    every later one TYPE_MISMATCH. Resolution refuses the case
    rather than choosing which way to be wrong."""
    outputs_by_key = {output.key: output for output in config.outputs}
    columns: list[ListColumn] = []
    existing = {column.key for column in target_list.columns}
    for column in target_list.columns:
        # An existing key reaches here only when this node already
        # fills it (resolve_columns admits nothing else), so the write
        # re-points an AI column at this run and never changes a kind.
        if column.key in column_keys and isinstance(column, AiColumn):
            column = column.model_copy(update={"node_id": node_id, "current_fill_id": fill_run_id})
        columns.append(column)
    for key in column_keys:
        if key in existing:
            continue
        # Marks the key appended, so one key can never land twice
        # in a single call (column resolution refuses collisions
        # upstream; this is the write-side backstop).
        existing.add(key)
        output = outputs_by_key[key]
        columns.append(
            AiColumn(
                key=key,
                label=output.label[:COLUMN_LABEL_MAX_LENGTH],
                type=output.type,
                node_id=node_id,
                current_fill_id=fill_run_id,
            )
        )
    target_list.columns = columns
    target_list.save(update_fields=["columns", "updated_at"])
