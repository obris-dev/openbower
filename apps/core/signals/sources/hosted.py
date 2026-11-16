"""The hosted kind: the signals service's listener, which the log poller
reads through the account's sync key, its matches becoming the log's
rows. Its polling state lives on the source row beside the config
(models.SignalSource)."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, ClassVar

from openbower_schema import HostedSourceSummary

from ..constants import LogStatus
from .base import SourceConfig, StatusContext
from .registry import HOSTED, register

if TYPE_CHECKING:
    from ..models import SignalSource
    from ..operations.poll_logs import PollOutcome
    from ..services.signals_client import SessionSignalsClient


class HostedSource(SourceConfig):
    """The listener whose matches become the log's rows, and the signal it
    follows, as the catalog names it (what chose the sheet's base columns
    and reads each match into a row)."""

    KIND: ClassVar[str] = HOSTED
    POLLED: ClassVar[bool] = True

    signal: str
    listener_id: str

    def status(self, source: SignalSource, context: StatusContext) -> LogStatus | None:
        if source.gone_at is not None:
            return LogStatus.LISTENER_GONE
        if not context.has_working_key:
            return LogStatus.RECONNECT
        return None

    def summary(self, source: SignalSource) -> HostedSourceSummary:
        return HostedSourceSummary(kind=self.KIND, signal=self.signal, listener_id=self.listener_id)

    def tear_down(self, signals: SessionSignalsClient) -> None:
        # The client counts a listener already gone as deleted.
        signals.delete_listener(listener_id=self.listener_id)

    def poll(self, source: SignalSource, now: datetime.datetime) -> PollOutcome:
        # Imported here: the poll operation reads the log service, which
        # imports this module.
        from ..operations.poll_logs import PollLogOperation

        operation = PollLogOperation(source=source, now=now)
        return operation.run()


register(HostedSource)
