"""A log's lifecycle, as the person who asks: one listener per log, created,
edited and deleted with it. The listener lives on the signals service (as
the person, through their session); the sheet and its feed live here. No
signals call runs inside a transaction. Create and delete order themselves
so a failure leaves nothing half made: create makes the listener first and
deletes it if the local write fails; delete removes the listener first and
keeps everything local if it cannot. An edit writes the listener first and
has no undo: a failed local write leaves the two labels apart until the
edit is retried."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from django.db import DatabaseError

from auth_client.downstream import DownstreamTokenRejected
from auth_client.models import AppSession
from openbower_schema import ListenerWire

from ..log_columns import LOG_SHAPES
from ..services.logs import LocalLog, LogService
from ..services.signals_client import (
    SessionSignalsClient,
    SignalsClientError,
    SignalsClientService,
    setup_payload,
)
from ..services.sync_key import SyncKeyService

logger = logging.getLogger(__name__)


class UnknownSignal(Exception):
    """A signal this app has no log columns for: it cannot be logged yet."""

    def __init__(self, signal: str) -> None:
        super().__init__(f"{signal!r} cannot be logged; this app logs: {', '.join(sorted(LOG_SHAPES))}")
        self.signal = signal


@dataclass(frozen=True)
class Log:
    """A log whole: its sheet and feed, and its listener as the service has it."""

    local: LocalLog
    listener: ListenerWire


class _LogOperation:
    """What every log operation acts through: the person's session, the
    log's local half, the account's sync key, and the signals client that
    acts as them."""

    session: AppSession

    @cached_property
    def logs(self) -> LogService:
        return LogService(account_id=self.session.account_id)

    @cached_property
    def keys(self) -> SyncKeyService:
        return SyncKeyService(account_id=self.session.account_id)

    @cached_property
    def signals(self) -> SessionSignalsClient:
        return SignalsClientService.for_session(self.session)


class CreateLogOperation(_LogOperation):
    def __init__(
        self,
        session: AppSession,
        *,
        label: str,
        signal: str,
        filter: dict[str, Any],
        prompt: str | None = None,
        min_confidence: float | None = None,
        autofill: bool = True,
    ) -> None:
        self.session = session
        self.label = label
        self.signal = signal
        self.setup = setup_payload(filter=filter, prompt=prompt, min_confidence=min_confidence)
        self.autofill = autofill

    def run(self) -> Log:
        """The account's sync key first (a refused mint stops the create
        before anything exists), then the listener, then the sheet and its
        feed in one transaction; a local failure deletes the listener."""
        if self.signal not in LOG_SHAPES:
            raise UnknownSignal(self.signal)
        session = self.session
        self.keys.ensure(session)
        listener = self.signals.create_listener(payload={"label": self.label, "signal": self.signal, **self.setup})
        try:
            local = self.logs.create(
                owner_id=session.user_id,
                label=self.label,
                signal=self.signal,
                listener_id=listener.id,
                autofill=self.autofill,
            )
        except DatabaseError:
            _delete_listener(self.signals, listener.id, why="its log could not be created")
            raise
        return Log(local=local, listener=listener)


class EditLogOperation(_LogOperation):
    def __init__(
        self,
        session: AppSession,
        sheet_id: str,
        *,
        label: str | None = None,
        filter: dict[str, Any] | None = None,
        prompt: str | None = None,
        min_confidence: float | None = None,
        autofill: bool | None = None,
    ) -> None:
        self.session = session
        self.sheet_id = sheet_id
        self.label = label
        self.setup = (
            None if filter is None else setup_payload(filter=filter, prompt=prompt, min_confidence=min_confidence)
        )
        self.autofill = autofill

    def run(self) -> Log:
        """The label and the setup go to the listener (the label to the sheet
        too); autofill is the feed's alone. The listener is written first, so
        a failed local write leaves its label ahead of the sheet's until the
        edit is retried."""
        local = self.logs.get(self.sheet_id)
        payload: dict[str, Any] = {**(self.setup or {})}
        if self.label is not None:
            payload["label"] = self.label
        if payload:
            listener = self.signals.edit_listener(listener_id=local.feed.listener_id, payload=payload)
        else:
            listener = self.signals.listener(listener_id=local.feed.listener_id)
        edited = self.logs.edit(local, label=self.label, autofill=self.autofill)
        return Log(local=edited, listener=listener)


class DeleteLogOperation(_LogOperation):
    def __init__(self, session: AppSession, sheet_id: str) -> None:
        self.session = session
        self.sheet_id = sheet_id

    def run(self) -> None:
        """The listener first (the client counts one already gone as
        deleted), then the sheet and its feed in one transaction. A signals failure
        deletes nothing here, so the person can try again."""
        local = self.logs.get(self.sheet_id)
        self.signals.delete_listener(listener_id=local.feed.listener_id)
        self.logs.delete(local)


def _delete_listener(client, listener_id: str, *, why: str) -> None:
    """Best effort: a listener left behind is logged by id, never silent."""
    try:
        client.delete_listener(listener_id=listener_id)
    except (SignalsClientError, DownstreamTokenRejected):
        logger.warning("listener %s left on the signals service: %s, and its delete failed", listener_id, why)
