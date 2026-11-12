"""A log's base columns, per signal, and the cells a match fills them
with. The columns are immutable plain columns (a person never edits what
the signal brought); a log gets them at create, and its feed appends the
row `LOG_SHAPES[signal].cells(match)` gives for every match. Keyed by signal
kind, so a second signal adds its own entry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from openbower_schema import MatchWire
from openbower_schema.sheets import PlainColumn


def _column(key: str, label: str, column_type: str) -> PlainColumn:
    return PlainColumn(key=key, label=label, type=column_type, immutable=True)


FUNDING_COLUMNS: tuple[PlainColumn, ...] = (
    _column("company", "Company", "text"),
    _column("domain", "Domain", "url"),
    _column("stage", "Stage", "text"),
    _column("amount", "Amount", "currency"),
    _column("currency", "Currency", "text"),
    _column("lead_investors", "Lead investors", "text"),
    _column("other_investors", "Other investors", "text"),
    _column("hq", "HQ", "text"),
    _column("source", "Source", "url"),
    _column("published", "Published", "date"),
    _column("confidence", "Confidence", "number"),
    _column("reason", "Reason", "text"),
)


def _amount(value: float | None) -> str:
    """Digits as the sheet stores a number: no trailing .0 on a whole one."""
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def funding_cells(match: MatchWire) -> dict[str, str]:
    """A funding match as its log row: every base column, blank where the
    article stated nothing."""
    event = match.event.data
    source = match.event.source
    return {
        "company": event.company.name,
        "domain": event.company.domain or "",
        "stage": event.stage or "",
        "amount": _amount(event.amount),
        "currency": event.currency or "",
        "lead_investors": ", ".join(event.lead_investors),
        "other_investors": ", ".join(event.other_investors),
        "hq": event.company.hq or "",
        "source": source.url,
        "published": (source.published or "")[:10],
        "confidence": "" if match.confidence is None else str(match.confidence),
        "reason": match.reason,
    }


@dataclass(frozen=True)
class LogShape:
    """What a signal's log is made of: its base columns, and the row a
    match fills them with."""

    columns: tuple[PlainColumn, ...]
    cells: Callable[[MatchWire], dict[str, str]]


# Each signal this app can log, by its catalog name.
LOG_SHAPES: dict[str, LogShape] = {"funding": LogShape(columns=FUNDING_COLUMNS, cells=funding_cells)}
