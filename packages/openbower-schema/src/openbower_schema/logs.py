"""Logs on the wire: a log is a sheet of kind log fed by a hosted signal's
listener on the signals service, or by pushes to a custom signal's webhook.
The sheet half is the sheets contract's; these shapes add what feeds it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .sheets import SheetSummary
from .signals import ListenerWire

_SIGNAL = (
    "The hosted signal it logs, as the catalog names it (`SignalsCatalog.signals[].signal`); null for a custom signal."
)
_SOURCE = "What feeds it: hosted (a signal's listener, which the app reads) or webhook (a custom signal's pushes)."
_LAST_RECEIVED = "When its newest row arrived (ISO); null before the first."

# What feeds a log.
LogSource = Literal["hosted", "webhook"]
# Where a log stands, derived on every read. A hosted log: listener_gone
# (final; delete the log), reconnect (the account has no working sync key),
# full (the sheet holds as many rows as it may), else active. A webhook
# log: waiting (no push has reached it yet), full, else active.
LogStatus = Literal["active", "listener_gone", "reconnect", "full", "waiting"]
_STATUS = (
    "Where it stands on the app's side: what fills it next, or why nothing will until someone acts. "
    "The listener's own state on the signals service (retrying, held) is `listener.status` on the detail."
)
_LAST_POLLED = (
    "When its listener's matches were last read through (ISO); null before the first read, "
    "and always for a custom signal, which nothing polls."
)


class LogSummary(BaseModel):
    """One log as the logs index shows it: no signals call behind it."""

    sheet: SheetSummary
    source: LogSource = Field(description=_SOURCE)
    listener_id: str | None = Field(
        description="The listener whose matches become the log's rows; null for a custom signal."
    )
    signal: str | None = Field(description=_SIGNAL)
    status: LogStatus = Field(description=_STATUS)
    last_polled_at: str | None = Field(description=_LAST_POLLED)
    last_received_at: str | None = Field(description=_LAST_RECEIVED)


class LogsPage(BaseModel):
    """`GET /v1/logs`: newest first."""

    items: list[LogSummary]
    next_cursor: str | None = Field(default=None, description="Hand back as ?after= for the next page.")


class LogDetail(BaseModel):
    """One log with its listener as the signals service has it now."""

    sheet: SheetSummary
    source: LogSource = Field(description=_SOURCE)
    listener: ListenerWire | None = Field(
        description=(
            "Read live from the signals service; null once the listener is gone (status listener_gone), "
            "and for a custom signal."
        )
    )
    signal: str | None = Field(description=_SIGNAL)
    status: LogStatus = Field(description=_STATUS)
    last_polled_at: str | None = Field(description=_LAST_POLLED)
    last_received_at: str | None = Field(description=_LAST_RECEIVED)


class SyncKeyReconnected(BaseModel):
    """`POST /v1/logs/reconnect`: the account's logs fill again from where
    they stopped. The replaced key is revoked when the person reconnecting
    minted it and the revoke succeeds; otherwise it stays live in its
    minter's API keys list until they revoke it. A key still working is
    left as it is, and both fields are empty."""

    left_live_key_id: str = Field(
        description="The replaced key while it is still live (another member minted it, or its revoke failed)."
    )
    left_live_user_id: str = Field(description="Who minted the key left live; empty when none was.")
