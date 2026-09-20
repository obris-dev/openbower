"""What a node processor IS: the standardized contract every kind's
processor fulfils, so a walker can materialize runs for any node
without knowing its kind.

The two questions a walker asks, in order: `needs` names the columns
whose cell records the judgement reads (the walker fetches them for a
page of rows in one query); `materialize` judges each row against
those records and returns the run it is owed, unsaved and born in the
state the kind's lane expects, or nothing. A walker inserts what comes
back under the open-run key, so offering a row twice is a no-op
whichever walker offered it. The judgement is deliberately per page,
not per row: the record read is the expensive part and it batches.

A processor is constructed for ONE node of its kind, account-scoped,
so its methods take only what varies per call."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import ClassVar

from ..models import List, ListRow, Node, NodeRun

# One row's cell records over the columns a processor needs:
# column key -> (state, updated at).
CellRecords = Mapping[str, tuple[str, datetime]]


class NodeProcessor(ABC):
    KIND: ClassVar[str]

    def __init__(self, *, account_id: str, node: Node) -> None:
        self.account_id = account_id
        self.node = node

    @abstractmethod
    def needs(self, target_list: List) -> list[str]:
        """The column keys whose cell records `materialize` judges a
        row by, in sheet order. Empty means the node has nothing to
        judge against (its inputs are gone), and a walker offers no
        rows rather than treating every row as qualified."""

    @abstractmethod
    def materialize(
        self, target_list: List, rows: Sequence[ListRow], records: Mapping[str, CellRecords], *, now: datetime
    ) -> list[NodeRun]:
        """The runs the given rows are owed now, unsaved: one per row
        that qualifies, none for a row that does not. `records` is keyed
        by row id over the keys `needs` named; a row absent from it has
        no records at all."""
