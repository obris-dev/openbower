"""A log's lifecycle, as the person who asks. A hosted signal's log has one
listener, created, edited and deleted with it; a custom signal's log is
fed by its webhook and touches the signals service at no step. The
listener lives on the signals service (as the person, through their
session); the sheet and its sources live here. No signals call runs
inside a transaction. Create and delete order themselves so a failure
leaves nothing half made: create makes the listener first and deletes it
if the local write fails; delete tears each source down first and keeps
everything local if it cannot. An edit writes the listener first and has
no undo: a failed local write leaves the two labels apart until the edit
is retried."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from django.db import DatabaseError, transaction

from auth_client.downstream import DownstreamTokenRejected
from auth_client.models import AppSession
from openbower_schema import CUSTOM_SIGNAL_TYPE, ListenerWire

from ..constants import LogStatus
from ..log_columns import LOG_SHAPES, hosted_types
from ..services.logs import DeclaredColumn, LocalLog, LogService
from ..services.signals_client import (
    SessionSignalsClient,
    SignalsClientError,
    SignalsClientService,
    SignalsNotFound,
    listener_payload,
    setup_payload,
)
from ..services.sync_key import SyncKeyReplaced, SyncKeyService
from ..services.webhook_tokens import WebhookTokenService
from ..sources.hosted import HostedSource
from ..sources.webhook import WebhookSource

logger = logging.getLogger(__name__)


class UnknownSignal(Exception):
    """A signal type this app has no log columns for, refused before the
    signals service is called; `takes` is what the refusing route accepts."""

    def __init__(self, signal_type: str, takes: list[str]) -> None:
        types = ", ".join(takes)
        super().__init__(f"{signal_type!r} is not a type this app takes here; it takes: {types}")
        self.signal_type = signal_type


def require_hosted_type(signal_type: str) -> None:
    """A preview or a type's events: only a hosted type this app can log
    reaches the signals service, so a refusal is in the app's words."""
    if signal_type not in LOG_SHAPES:
        raise UnknownSignal(signal_type, hosted_types())


class NoWebhook(Exception):
    """A webhook rotate on a signal no webhook feeds: a hosted signal's rows
    are its listener's, so it has no URL to replace."""

    def __init__(self, sheet_id: str) -> None:
        super().__init__("this signal has no webhook URL to rotate; only a custom signal does")
        self.sheet_id = sheet_id


class SetupOnCustomSignal(Exception):
    """A filter, prompt or threshold sent to a signal with no hosted source:
    those set a listener, and it has none."""

    def __init__(self, sheet_id: str) -> None:
        super().__init__("this signal has no listener setup to edit; send a label only")
        self.sheet_id = sheet_id


class ListenerGone(Exception):
    """The log's listener no longer exists on the signals service: the log
    can be read and deleted, not edited."""

    def __init__(self, sheet_id: str) -> None:
        super().__init__("this log's listener no longer exists; delete the log")
        self.sheet_id = sheet_id


@dataclass(frozen=True)
class Log:
    """A log whole: its sheet and sources, and its listener as the service has
    it (None once the listener is gone)."""

    local_log: LocalLog
    listener: ListenerWire | None


@dataclass(frozen=True)
class IssuedWebhook:
    """A custom signal with its webhook URL's token, the one time the token
    is in hand: at the create and at each rotate."""

    log: Log
    token: str


class _LogOperation:
    """What every log operation acts through: the person's session, the
    log's local half, the account's sync key, the signals client that acts
    as them, and a custom signal's webhook tokens."""

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

    @cached_property
    def tokens(self) -> WebhookTokenService:
        return WebhookTokenService(account_id=self.session.account_id)


class CreateLogOperation(_LogOperation):
    def __init__(
        self,
        session: AppSession,
        *,
        label: str,
        signal_type: str,
        filter: dict[str, Any],
        prompt: str | None = None,
        min_confidence: float | None = None,
        autofill: bool = True,
    ) -> None:
        self.session = session
        self.label = label
        self.signal_type = signal_type
        self.setup = setup_payload(filter=filter, prompt=prompt, min_confidence=min_confidence)
        self.autofill = autofill

    def run(self) -> Log:
        """The account's sync key first (a refused mint stops the create
        before anything exists), then the listener, then the sheet and its
        hosted source in one transaction; a local failure deletes the listener."""
        if self.signal_type not in LOG_SHAPES:
            takes = hosted_types()
            takes.append(CUSTOM_SIGNAL_TYPE)
            raise UnknownSignal(self.signal_type, takes)
        session = self.session
        self.keys.ensure(session)
        payload = listener_payload(label=self.label, signal_type=self.signal_type, setup=self.setup)
        listener = self.signals.create_listener(payload=payload)
        try:
            local_log = self.logs.create(
                owner_id=session.user_id,
                label=self.label,
                signal_type=self.signal_type,
                listener_id=listener.id,
                autofill=self.autofill,
            )
        except DatabaseError:
            _delete_listener(self.signals, listener.id, why="its log could not be created")
            raise
        return Log(local_log=local_log, listener=listener)


class CreateCustomSignalOperation(_LogOperation):
    def __init__(
        self, session: AppSession, *, label: str, columns: list[DeclaredColumn], autofill: bool = True
    ) -> None:
        self.session = session
        self.label = label
        self.columns = columns
        self.autofill = autofill

    def run(self) -> IssuedWebhook:
        """The sheet, its webhook source and the first token of its URL, in
        one transaction. No sync key and no listener: nothing reads the
        signals service for it."""
        with transaction.atomic():
            local_log = self.logs.create_custom(
                owner_id=self.session.user_id, label=self.label, columns=self.columns, autofill=self.autofill
            )
            source, _ = local_log.find(WebhookSource)
            token = self.tokens.mint(source, user_id=self.session.user_id)
        return IssuedWebhook(log=Log(local_log=local_log, listener=None), token=token)


class RotateWebhookOperation(_LogOperation):
    def __init__(self, session: AppSession, sheet_id: str) -> None:
        self.session = session
        self.sheet_id = sheet_id

    def run(self) -> IssuedWebhook:
        """A new token for a custom signal's webhook URL; the old URL stops
        working at once. Raises NoWebhook for a signal no webhook
        feeds."""
        local_log = self.logs.get(self.sheet_id)
        found = local_log.find(WebhookSource)
        if found is None:
            raise NoWebhook(self.sheet_id)
        source, _ = found
        token = self.tokens.mint(source, user_id=self.session.user_id)
        return IssuedWebhook(log=Log(local_log=local_log, listener=None), token=token)


class ReconnectLogsOperation(_LogOperation):
    def __init__(self, session: AppSession) -> None:
        self.session = session

    def run(self) -> SyncKeyReplaced:
        """A fresh sync key through this person in place of the account's
        refused (or missing) one, then the account's logs due at once: they
        were parked waiting for it, and resume from their cursors."""
        replaced = self.keys.reconnect(self.session)
        self.logs.wake()
        return replaced


class ReadLogOperation(_LogOperation):
    def __init__(self, session: AppSession, sheet_id: str) -> None:
        self.session = session
        self.sheet_id = sheet_id

    def run(self) -> Log:
        """The log with its listener read live. A listener the service no
        longer has is recorded as gone (the log stops polling) and answered
        as None, so the log still opens to say so."""
        local_log = self.logs.get(self.sheet_id)
        found = local_log.find(HostedSource)
        if found is None or local_log.status == LogStatus.LISTENER_GONE:
            return Log(local_log=local_log, listener=None)
        source, hosted = found
        try:
            listener = self.signals.listener(listener_id=hosted.listener_id)
        except SignalsNotFound:
            self.logs.mark_gone(source)
            gone = self.logs.get(self.sheet_id)
            return Log(local_log=gone, listener=None)
        return Log(local_log=local_log, listener=listener)


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
    ) -> None:
        self.session = session
        self.sheet_id = sheet_id
        self.label = label
        self.setup = (
            None if filter is None else setup_payload(filter=filter, prompt=prompt, min_confidence=min_confidence)
        )

    def run(self) -> Log:
        """The label and the setup go to the listener (the label to the sheet
        too). The listener is written first, so
        a failed local write leaves its label ahead of the sheet's until the
        edit is retried. Raises ListenerGone for a log whose listener the
        service no longer has (recorded the first time it says so)."""
        local_log = self.logs.get(self.sheet_id)
        found = local_log.find(HostedSource)
        if found is None:
            if self.setup is not None:
                raise SetupOnCustomSignal(self.sheet_id)
            edited = self.logs.edit(local_log, label=self.label)
            return Log(local_log=edited, listener=None)
        if local_log.status == LogStatus.LISTENER_GONE:
            raise ListenerGone(self.sheet_id)
        source, hosted = found
        listener_id = hosted.listener_id
        payload: dict[str, Any] = {**(self.setup or {})}
        if self.label is not None:
            payload["label"] = self.label
        try:
            if payload:
                listener = self.signals.edit_listener(listener_id=listener_id, payload=payload)
            else:
                listener = self.signals.listener(listener_id=listener_id)
        except SignalsNotFound:
            self.logs.mark_gone(source)
            raise ListenerGone(self.sheet_id) from None
        edited = self.logs.edit(local_log, label=self.label)
        return Log(local_log=edited, listener=listener)


class DeleteLogOperation(_LogOperation):
    def __init__(self, session: AppSession, sheet_id: str) -> None:
        self.session = session
        self.sheet_id = sheet_id

    def run(self) -> None:
        """What each source holds elsewhere first (a hosted source's
        listener), then the sheet and its sources in one transaction. A
        signals failure deletes nothing here, so the person can try again."""
        local_log = self.logs.get(self.sheet_id)
        for source in local_log.sources:
            source.config.tear_down(self.signals)
        self.logs.delete(local_log)


def _delete_listener(client, listener_id: str, *, why: str) -> None:
    """Best effort: a listener left behind is logged by id, never silent."""
    try:
        client.delete_listener(listener_id=listener_id)
    except (SignalsClientError, DownstreamTokenRejected):
        logger.warning("listener %s left on the signals service: %s, and its delete failed", listener_id, why)
