"""Logs on the wire: a log is a sheet of kind log fed by one listener on the
signals service. The sheet half is the sheets contract's; these shapes add
what feeds it."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .sheets import SheetSummary
from .signals import ListenerWire

_SIGNAL = "The signal it logs, as the catalog names it (`SignalsCatalog.signals[].signal`)."


class LogSummary(BaseModel):
    """One log as the logs index shows it: no signals call behind it."""

    sheet: SheetSummary
    listener_id: str = Field(description="The listener whose matches become the log's rows.")
    signal: str = Field(description=_SIGNAL)
    autofill: bool = Field(description="Whether arriving rows fill their AI columns.")


class LogsPage(BaseModel):
    """`GET /v1/logs`: newest first."""

    items: list[LogSummary]
    next_cursor: str | None = Field(default=None, description="Hand back as ?after= for the next page.")


class LogDetail(BaseModel):
    """One log with its listener as the signals service has it now."""

    sheet: SheetSummary
    listener: ListenerWire
    signal: str = Field(description=_SIGNAL)
    autofill: bool
