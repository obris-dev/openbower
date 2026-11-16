"""The webhook kind: a custom signal's own endpoint, which a sender pushes
rows to. Nothing polls it and nothing lives elsewhere for it; a token a
push carries is a child row of the source, never its config."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from openbower_schema import CUSTOM_SIGNAL_TYPE, WebhookSourceSummary

from ..constants import LogStatus
from .base import SourceConfig, StatusContext
from .registry import WEBHOOK, register

if TYPE_CHECKING:
    from ..models import SignalSource
    from ..services.signals_client import SessionSignalsClient


class WebhookSource(SourceConfig):
    """A custom signal's webhook: rows arrive by push to it."""

    KIND: ClassVar[str] = WEBHOOK
    POLLED: ClassVar[bool] = False

    def signal_type(self) -> str:
        return CUSTOM_SIGNAL_TYPE

    def status(self, source: SignalSource, context: StatusContext) -> LogStatus | None:
        return None if context.has_rows else LogStatus.WAITING

    def summary(self, source: SignalSource) -> WebhookSourceSummary:
        return WebhookSourceSummary(kind=self.KIND)

    def tear_down(self, signals: SessionSignalsClient) -> None:
        # Nothing lives on another service for a webhook.
        return None


register(WebhookSource)
