from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import (
    CELL_SOURCE_MAX_LENGTH,
    CELL_STATE_MAX_LENGTH,
    COLUMN_KEY_MAX_LENGTH,
    CellSource,
    StoredCellState,
)


class ListCellState(AccountScopedModel):
    """What was made of one AI cell: FILLED, or the cause it is blank,
    and who made it (`source`: a fill today; a hand-typed value once the
    grid can be edited). One row per (list, row, column), upserted.

    ABSENCE means exactly one thing: never attempted. Nothing is
    written at admission, and nothing is written for a cell no writer
    has reached.

    Recording FILLED rather than reading it off the sheet is a QUERY
    decision. A value on the row does
    identify a filled cell, but counting that way is a sequential scan
    of every row in the sheet, and it stays O(rows) however few cells
    the column ever touched. Counting a record is an indexed read
    bounded by the cells actually filled. The per-column poll asks for
    that count every four seconds, so the scan is the wrong side of the
    trade: one narrow row per answered cell buys it back.

    PENDING is deliberately absent. A queued NodeRun on a live fill IS
    a pending cell, which is what lets admission write nothing to the
    sheet and leaves a stopped fill with nothing to sweep.

    A settled blank is history, not a gate: a refill targets every
    blank in its columns, and the user's click is the consent to
    re-spend on one."""

    list_id = models.CharField(_("list id"), max_length=26)
    row_id = models.CharField(_("row id"), max_length=26)
    column_key = models.CharField(_("column key"), max_length=COLUMN_KEY_MAX_LENGTH)
    state = models.CharField(_("state"), max_length=CELL_STATE_MAX_LENGTH, default=StoredCellState.NO_EVIDENCE)
    source = models.CharField(_("source"), max_length=CELL_SOURCE_MAX_LENGTH, default=CellSource.FILL)
    # The fill run that wrote it: the row drawer's link to that run's
    # NodeRun.result, which holds what the model actually said. NULL on
    # the automatic path (autofill), which has no fill run; the drawer
    # follows the writing task by (row, column) instead.
    fill_run_id = models.CharField(_("fill run id"), max_length=26, null=True, blank=True)
    # tool -> the status code it reported for the run that wrote
    # this cell, filled or blank alike ("open" for a tool that served).
    # The one place a FILLED cell can say a tool was degraded, and the
    # detail behind a blank cell's tool_* state. Empty for a run before
    # tools reported statuses.
    tools = models.JSONField(_("tool statuses"), default=dict, blank=True)

    class Meta:
        verbose_name = _("list cell state")
        verbose_name_plural = _("list cell states")
        constraints = [
            # One truth per cell; every write is an upsert against it.
            models.UniqueConstraint(fields=["list_id", "row_id", "column_key"], name="cell_state_cell_uniq"),
        ]
        indexes = [
            # THE poll's index: filled and attempted per column come off
            # ONE grouped read of this, with no sheet scan anywhere.
            # Also refill targeting (settled under a config) and
            # selective refill (re-run everything that failed with X).
            models.Index(fields=["list_id", "column_key", "state"], name="cell_state_column_idx"),
            # Sheet-wide by state ("show me every unverified cell"). A
            # distinct index because the one above leads with
            # column_key and cannot serve a query that names no column.
            models.Index(fields=["list_id", "state"], name="cell_state_state_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.list_id}/{self.row_id}/{self.column_key} ({self.state})"
