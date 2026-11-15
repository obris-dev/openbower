"""An arriving row: its cells, and the sender's id for it when one was
sent. One shape from every door to the insert, so a row and its id
cannot come apart on the way."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# A pushed row's envelope fields, {external_id?, cells}. The drawer's
# command (add-rows-command.ts) writes the same keys.
ENVELOPE_CELLS = "cells"
ENVELOPE_EXTERNAL_ID = "external_id"
ENVELOPE_KEYS = frozenset({ENVELOPE_CELLS, ENVELOPE_EXTERNAL_ID})


@dataclass(frozen=True)
class IncomingRow:
    # Column key to cell text.
    cells: dict[str, str]
    # Blank when the sender named none: such a row always lands.
    external_id: str = ""


def rows_without_external_ids(cell_maps: Iterable[Mapping[str, str]]) -> list[IncomingRow]:
    """Rows with no sender external_id, one per row's cell map."""
    return [IncomingRow(cells=dict(row_cells)) for row_cells in cell_maps]


def is_envelope(given: Mapping[str, Any]) -> bool:
    """Whether a pushed row is an envelope: its `cells` is an object. A
    plain row's values are text or numbers, never an object, so a column
    keyed "cells" never reads as one."""
    return isinstance(given.get(ENVELOPE_CELLS), dict)
