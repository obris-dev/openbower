"""Logs on the wire: a log is a sheet of kind log fed by one listener on the
signals service. The sheet half is the sheets contract's; these shapes add
what feeds it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .sheets import SheetSummary
from .signals import ListenerWire

_SIGNAL = "The signal it logs, as the catalog names it (`SignalsCatalog.signals[].signal`)."

# Where a log stands, derived on every read: listener_gone (final; delete
# the log), reconnect (the account has no working sync key), full (the
# sheet holds as many rows as it may), else active.
LogStatus = Literal["active", "listener_gone", "reconnect", "full"]
_STATUS = (
    "Where it stands on the app's side: what fills it next, or why nothing will until someone acts. "
    "The listener's own state on the signals service (retrying, held) is `listener.status` on the detail."
)
_LAST_POLLED = "When its listener's matches were last read through (ISO); null before the first read."


class LogSummary(BaseModel):
    """One log as the logs index shows it: no signals call behind it."""

    sheet: SheetSummary
    listener_id: str = Field(description="The listener whose matches become the log's rows.")
    signal: str = Field(description=_SIGNAL)
    status: LogStatus = Field(description=_STATUS)
    last_polled_at: str | None = Field(description=_LAST_POLLED)


class LogsPage(BaseModel):
    """`GET /v1/logs`: newest first."""

    items: list[LogSummary]
    next_cursor: str | None = Field(default=None, description="Hand back as ?after= for the next page.")


class LogDetail(BaseModel):
    """One log with its listener as the signals service has it now."""

    sheet: SheetSummary
    listener: ListenerWire | None = Field(
        description="Read live from the signals service; null once the listener is gone (status listener_gone)."
    )
    signal: str = Field(description=_SIGNAL)
    status: LogStatus = Field(description=_STATUS)
    last_polled_at: str | None = Field(description=_LAST_POLLED)


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
