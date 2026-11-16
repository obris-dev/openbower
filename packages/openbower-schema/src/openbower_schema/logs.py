"""Signals on the wire: a signal is a sheet of kind log fed by its sources
(a hosted signal's listener on the signals service, a custom signal's
webhook). The sheet half is the sheets contract's; these shapes add what
feeds it."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .sheets import SheetSummary
from .signals import ListenerWire

_SIGNAL = "The signal it logs, as the catalog names it (`SignalsCatalog.signals[].signal`)."

# Where a log stands, derived on every read: listener_gone (final; delete
# the log), reconnect (the account has no working sync key), full (the
# sheet holds as many rows as it may), waiting (a custom signal whose sheet
# holds no row), else active.
SignalStatus = Literal["active", "listener_gone", "reconnect", "full", "waiting"]
_STATUS = (
    "Where it stands on the app's side: what fills it next, or why nothing will until someone acts. "
    "The listener's own state on the signals service (retrying, held) is `listener.status` on the detail."
)
_LAST_POLLED = (
    "When a source the app reads (a hosted signal's listener) was last read through (ISO); "
    "null before the first read, and for a signal nothing is read for."
)
_SOURCES = "What feeds it, at most one of each kind, each carrying its own kind's facts."

# What can feed a signal.
SourceKind = Literal["hosted", "webhook"]


class HostedSourceSummary(BaseModel):
    """A hosted signal: a listener on the signals service, which the app reads."""

    kind: Literal["hosted"]
    signal: str = Field(description=_SIGNAL)
    listener_id: str = Field(description="The listener whose matches become the log's rows.")


class WebhookSourceSummary(BaseModel):
    """A custom signal's webhook: a sender pushes rows to it."""

    kind: Literal["webhook"]


SourceSummary = Annotated[HostedSourceSummary | WebhookSourceSummary, Field(discriminator="kind")]


class SignalSummary(BaseModel):
    """One log as the logs index shows it: no signals call behind it."""

    sheet: SheetSummary
    sources: list[SourceSummary] = Field(description=_SOURCES)
    status: SignalStatus = Field(description=_STATUS)
    last_polled_at: str | None = Field(description=_LAST_POLLED)


class SignalsPage(BaseModel):
    """`GET /v1/signals`: newest first."""

    items: list[SignalSummary]
    next_cursor: str | None = Field(default=None, description="Hand back as ?after= for the next page.")


class SignalDetail(BaseModel):
    """One signal, with a hosted source's listener as the signals service
    has it now."""

    sheet: SheetSummary
    sources: list[SourceSummary] = Field(description=_SOURCES)
    listener: ListenerWire | None = Field(
        description=(
            "Its hosted source's listener, read live from the signals service; null for a signal with no "
            "hosted source, and once the listener is gone (status listener_gone)."
        )
    )
    status: SignalStatus = Field(description=_STATUS)
    last_polled_at: str | None = Field(description=_LAST_POLLED)


class SyncKeyReconnected(BaseModel):
    """`POST /v1/signals/reconnect`: the account's logs fill again from where
    they stopped. The replaced key is revoked when the person reconnecting
    minted it and the revoke succeeds; otherwise it stays live in its
    minter's API keys list until they revoke it. A key still working is
    left as it is, and both fields are empty."""

    left_live_key_id: str = Field(
        description="The replaced key while it is still live (another member minted it, or its revoke failed)."
    )
    left_live_user_id: str = Field(description="Who minted the key left live; empty when none was.")
