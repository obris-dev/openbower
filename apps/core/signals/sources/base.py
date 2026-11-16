"""What a source kind IS, and the pattern for adding one.

A kind is one module in this package exporting one class: a SourceConfig
subclass whose class-level KIND names it and whose POLLED says whether
the log poller reads it (the row's cursor, lease and due time are a
polled kind's alone; a source of any other kind is never due). Its
fields are the kind's config at rest. The class is registered at the
module's bottom (`register(HostedSource)`). A reader that knows its kind
constructs or validates the class itself; a stored config goes through
the registry (`parse_source_config`), which picks the class by the kind
the config carries.

A config is read once its row is loaded, never queried into: nothing
filters on a key inside it. Anything a source is looked up by (a
webhook's token, a listener id if one is ever matched) is a column or a
child table with its own index, and a secret is never config at all
(it is kept encrypted in its own row, as the sync key is).

A kind ANSWERS every seam where a log's sources are walked without
regard to kind: what type it gives its signal, what it says about its
log's status, how it reads on the wire, what a log's delete undoes
elsewhere, and (a polled kind only) one read by the poller. Each default
raises, and `register()` refuses a kind that leaves one at its default,
so a new kind decides each seam in its own module, a no-op included,
before it can boot. A feature about one kind alone (a hosted listener's
setup) asks the log for that kind's config (`LocalLog.find`) instead of
growing a seam.

To add a kind: a module here, a SourceConfig subclass answering every
seam, `register(...)` at the bottom, its name in the wire's SourceKind
literal (the parity pin fails until it is), and its create operation.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, computed_field

from openbower_schema import SourceSummary

from ..constants import LogStatus

if TYPE_CHECKING:
    from ..models import SignalSource
    from ..operations.poll_logs import PollOutcome
    from ..services.signals_client import SessionSignalsClient


class StatusContext(Protocol):
    """What a kind's status reads beyond its own row: its log's and its
    account's facts."""

    @property
    def has_working_key(self) -> bool:
        """Whether the account holds a sync key the signals service has not
        refused (read at most once per log service)."""
        ...

    @property
    def has_rows(self) -> bool:
        """Whether the source's log holds any row now (rows a person
        deleted count as never held)."""
        ...


# The seams every kind answers, and the one only a polled kind answers.
SEAMS: tuple[str, ...] = ("signal_type", "status", "summary", "tear_down")
POLLED_SEAM = "poll"


class SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    KIND: ClassVar[str]
    POLLED: ClassVar[bool]

    # The kind rides in the dump, so a stored config parses without its
    # row's kind column (a JSON field reads its value alone).
    @computed_field
    @property
    def kind(self) -> str:
        return self.KIND

    def signal_type(self) -> str:
        """The type this source gives its signal: a hosted type's key, or
        custom for a kind a sender feeds. The log service holds every source
        of a signal to one type (`signal_type_of`)."""
        raise NotImplementedError

    def status(self, source: SignalSource, context: StatusContext) -> LogStatus | None:
        """What this source says about its log's status, or None when it
        says nothing (the log service ranks every source's word and the
        sheet's own)."""
        raise NotImplementedError

    def summary(self, source: SignalSource) -> SourceSummary:
        """This source as the wire shows it."""
        raise NotImplementedError

    def tear_down(self, signals: SessionSignalsClient) -> None:
        """Undo what another service holds for this source, as the person
        deleting the log, before its rows here go. A failure raises and the
        delete keeps everything local."""
        raise NotImplementedError

    def poll(self, source: SignalSource, now: datetime.datetime) -> PollOutcome:
        """One read of a claimed source, for a polled kind only."""
        raise NotImplementedError
