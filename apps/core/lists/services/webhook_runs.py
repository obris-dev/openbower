"""A webhook node's runs on the ledger: what a run's stored result
says and the two purges its owners call. What its cell SAYS is on the
cell ledger like every other column's (SENT, FAILED, or pending off
an open run), written at the send's landing. How a row EARNS one is the WebhookProcessor's judgement,
offered the row by the workflow advance (services/advance.py) inside
every terminal landing. A webhook run is a NodeRun of kind webhook, born DEFERRED at the
next window of its node's cadence (the WebhookProcessor's judgement),
and claimed by the flush (operations/flush_deferred.py), never by the
agent worker.

The wait node is a barrier, not a ledger: when every column it waits
on is done for a row, the only effect is a run for each webhook node
behind it. Completion is re-read from the cells at claim time, so an
open run always sends the row's LATEST completion, and the open-run key
(one open automatic run per (row, node)) makes a re-completion while
one is pending a no-op.

Trusted-process module like node_runs.py: account ids are passed in.
"""

from __future__ import annotations

from django.db import transaction
from pydantic import BaseModel

from ..constants import WebhookRunOutcome
from ..models import NodeRun
from ..nodes.registry import WEBHOOK


class WebhookRunResult(BaseModel):
    """The shape of a webhook run's `result`, typed both ways: written
    with `model_dump()`, read with `model_validate`."""

    outcome: WebhookRunOutcome
    delivery_id: str = ""
    error: str = ""


def purge_for_node(node_id: str) -> int:
    """A webhook column's runs go with it: called by the column delete
    before the path, unconditionally (a gone node still has runs by
    id). Returns the count removed."""
    with transaction.atomic():
        removed, _by_model = NodeRun.objects.filter(kind=WEBHOOK, node_id=node_id).delete()
    return removed


def purge_for_list(list_id: str) -> int:
    """A sheet's webhook runs go with it, beside its fill-backed runs."""
    with transaction.atomic():
        removed, _by_model = NodeRun.objects.filter(kind=WEBHOOK, list_id=list_id).delete()
    return removed
