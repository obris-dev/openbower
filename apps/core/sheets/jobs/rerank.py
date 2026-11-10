"""Re-space a sheet's ranks (SheetService.respace): queued once by a
move that split a gap past RANK_REBALANCE_LENGTH, run as one slice.
While the sheet has an open fill the slice waits: a walk's cursor holds
a key of the old spacing, and a fill lasts minutes at the most."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from jobs.kinds.base import JobKind, JobWaiting
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import RERANK_WAIT_SECONDS
from ..services.sheets import FillsOpen, SheetNotFound, SheetService


class RerankProgress(BaseModel):
    """No cursor: the re-space is one slice under the sheet lock, so a
    reclaimed job redoes it whole."""


class RerankJob(JobKind[RerankProgress]):
    KIND: ClassVar[str] = "rerank"
    Progress = RerankProgress
    sheet_id: str

    def run(self, job: Job, progress: RerankProgress) -> RerankProgress | None:
        try:
            SheetService(account_id=job.account_id).respace(self.sheet_id)
        except FillsOpen:
            raise JobWaiting(RERANK_WAIT_SECONDS) from None
        except SheetNotFound:
            return None
        return None


register(RerankJob)
