"""The agent kind's processor: the three rules for which rows a
column_agent node owes a run, one per walk mode, in ONE place.

FRESH (a new fill): a row the prompt can act on, meaning at least one
variable it references renders non-blank (a prompt with no variables
asks the same question everywhere, so every row qualifies). Born READY
under the fill.

REMAINING (a refill): judged across the fill's whole column set, a row
is done only when EVERY column is settled under THIS config (a
diagnosis the same config would just reproduce) or already holds a
value (a user's or a prior fill's, which write-if-blank would refuse);
a FILLED cell settles unconditionally. Infrastructure-tier states and
never-attempted rows re-run. A resume additionally offers only the rows
the stopped fill still owed (its ABANDONED runs, read rather than
reconstructed). Then the prompt must be able to act on it. Born READY
under the fill.

PUSHED (rows a push appended): the node runs unless the push filled
every column it owns (write-if-blank would keep those values, so the
run would only buy a skip); if ANY is blank the node runs and
write-if-blank protects the sent ones. Born READY, no fill.

Memory is bounded by one page: the settled and owed sets are asked per
page against the ids in hand and dropped when the page is done."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from typing import ClassVar, NamedTuple

from agents.runtime.prompts import prompt_variables
from openbower_schema.agents import AgentConfig

from ..constants import FILL_SCAN_CHUNK, NodeRunStatus
from ..models import Fill, List, ListRow, NodeRun
from ..nodes.registry import COLUMN_AGENT
from ..services.cell_states import CellStateService
from ..services.workflows import columns_for_node
from .base import NodeProcessor, WalkMode
from .factory import register


def row_is_eligible(data: dict, variables: set[str]) -> bool:
    """Whether the prompt can ACT on this row: at least one referenced
    variable renders non-blank. ONE definition for every walk."""
    if not variables:
        return True
    return any(str(data.get(variable, "")).strip() for variable in variables)


class Probe(NamedTuple):
    """What a scan for the FIRST qualifying row found: whether one
    exists, and whether any row was dropped because the prompt could
    not act on it (meaningful only when none was found, where "the
    column is done" and "your prompt reads columns these rows have not
    got" need different next steps)."""

    found: bool
    dropped_any: bool


class AIColumnProcessor(NodeProcessor):
    KIND: ClassVar[str] = COLUMN_AGENT

    @cached_property
    def fill(self) -> Fill:
        """The fill a FRESH or REMAINING walk queues under: its frozen
        config is the prompt and the fingerprint the judgement uses."""
        return Fill.objects.get(id=self.scope.fill_run_id, account_id=self.account_id)

    @cached_property
    def variables(self) -> set[str]:
        return prompt_variables(AgentConfig(**self.fill.config_snapshot).prompt)

    def enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime) -> int:
        if not rows:
            return 0
        facts = self._page_facts(rows)
        owed = [row for row in rows if self._qualifies(target_list, row, facts)]
        if not owed:
            return 0
        fill_run_id = self.scope.fill_run_id or None
        runs = [
            NodeRun(
                account_id=self.account_id,
                fill_run_id=fill_run_id,
                node_id=str(self.node.id),
                kind=COLUMN_AGENT,
                row_id=str(row.id),
                list_id=str(target_list.id),
                position=row.position,
                status=NodeRunStatus.READY,
                last_state_change_at=now,
            )
            for row in owed
        ]
        NodeRun.objects.bulk_create(runs, ignore_conflicts=True)
        return len(runs)

    def probe(self, target_list: List, *, until_position: int = 0) -> Probe:
        """Scan for the FIRST row this walk would queue, in sheet order
        within the range, without queuing anything: admission's zero
        check. Pages exactly as the walk does and stops at the first
        hit, so a sheet with work near the top costs one page."""
        after = 0
        dropped_any = False
        while True:
            rows = ListRow.objects.filter(list_id=str(target_list.id), position__gt=after)
            if until_position:
                rows = rows.filter(position__lte=until_position)
            page = list(rows.order_by("position").only("id", "position", "data")[:FILL_SCAN_CHUNK])
            if not page:
                return Probe(found=False, dropped_any=dropped_any)
            facts = self._page_facts(page)
            for row in page:
                verdict = self._judge(target_list, row, facts)
                if verdict is _Verdict.OWED:
                    return Probe(found=True, dropped_any=dropped_any)
                if verdict is _Verdict.DROPPED:
                    dropped_any = True
            after = page[-1].position

    # The judgement.

    def _qualifies(self, target_list: List, row: ListRow, facts: _PageFacts) -> bool:
        return self._judge(target_list, row, facts) is _Verdict.OWED

    def _judge(self, target_list: List, row: ListRow, facts: _PageFacts) -> _Verdict:
        mode = self.scope.mode
        if mode is WalkMode.PUSHED:
            keys = columns_for_node(target_list, str(self.node.id))
            if keys and all((row.data.get(key) or "").strip() for key in keys):
                return _Verdict.DONE
            return _Verdict.OWED
        if mode is WalkMode.REMAINING:
            row_id = str(row.id)
            # The resume bound goes FIRST: a row the stopped fill never
            # consented to is not its remaining work, and counting it as
            # dropped would blame the prompt for a row the scope excluded.
            if facts.owed is not None and row_id not in facts.owed:
                return _Verdict.DONE
            done = facts.settled.get(row_id, frozenset())
            if all(key in done or str(row.data.get(key, "") or "").strip() for key in self.fill.column_keys):
                return _Verdict.DONE
        if mode in (WalkMode.FRESH, WalkMode.REMAINING):
            return _Verdict.OWED if row_is_eligible(row.data, self.variables) else _Verdict.DROPPED
        # A structural backfill has no meaning for an agent node.
        return _Verdict.DONE

    def _page_facts(self, rows: Sequence[ListRow]) -> _PageFacts:
        """The two membership sets a REMAINING walk asks per page: which
        of these rows the resumed fill still owed, and which of the
        fill's columns each row already has settled under this config."""
        if self.scope.mode is not WalkMode.REMAINING:
            return _PageFacts(owed=None, settled={})
        ids = [str(row.id) for row in rows]
        owed = None
        if self.scope.owed_by:
            owed = {
                str(row_id)
                for row_id in NodeRun.objects.filter(
                    account_id=self.account_id,
                    fill_run_id=self.scope.owed_by,
                    status=NodeRunStatus.ABANDONED,
                    row_id__in=ids,
                ).values_list("row_id", flat=True)
            }
        settled: dict[str, set[str]] = {}
        cell_states = CellStateService(account_id=self.account_id)
        for row_id, column_key in cell_states.iter_settled(
            str(self.fill.list_id),
            row_ids=ids,
            column_keys=self.fill.column_keys,
            fingerprint=self.fill.config_fingerprint,
        ):
            settled.setdefault(str(row_id), set()).add(column_key)
        return _PageFacts(owed=owed, settled=settled)


class _Verdict(StrEnum):
    OWED = "owed"
    DONE = "done"
    # An owed row the prompt cannot act on.
    DROPPED = "dropped"


class _PageFacts(NamedTuple):
    owed: set[str] | None
    settled: dict[str, set[str]]


register(AIColumnProcessor)
