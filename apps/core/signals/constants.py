"""Wire-level constants for the signals client, the sync key, a log's
sources, and the log poller."""

from __future__ import annotations

from enum import StrEnum

# What the account's sync key may do beyond profile: read listeners and
# their matches, never change them (a person does that as themselves).
SYNC_KEY_SCOPES = ("signals:read",)


# The signals service's 409 codes the client tells apart: each is its own
# error type, so a caller never compares the raw code. Its 400/404 bodies
# pass through with their codes untouched.
class SignalsConflictCode(StrEnum):
    LIMIT_REACHED = "limit_reached"  # the account is at its listener cap
    ALREADY_RUNNING = "already_running"  # the account has a preview open


# The app's own codes on the {error, detail} envelope its /v1/signals,
# /v1/signal-types and /v1/signal-previews routes answer with; the signals service's own 400/404/409 codes pass
# through untouched beside them.
class SignalsErrorCode(StrEnum):
    SIGNALS_UNAVAILABLE = "signals_unavailable"  # 503: the service is down or answered nonsense
    UNKNOWN_SIGNAL = "unknown_signal"  # 400: a signal this app cannot log
    KEY_LIMIT_REACHED = "key_limit_reached"  # 409: the sync key would exceed the person's key limit
    LISTENER_GONE = "listener_gone"  # 409: the log's listener no longer exists; delete the log
    SETUP_ON_CUSTOM_SIGNAL = "setup_on_custom_signal"  # 409: a listener setup sent to a signal with no listener


# A source kind's name: the SignalSource.kind column bound and the
# registry's name check (signals.sources.registry).
SOURCE_KIND_MAX_LENGTH = 16
# Where a polled source's next read starts, as its kind reads it (a hosted
# listener's last match id; a sender's page token or URL, opaque to us).
SOURCE_CURSOR_MAX_LENGTH = 512


# Where a log stands, derived on every read and never stored: from its
# sources (a hosted listener gone, no working sync key for it, a webhook
# whose sheet holds no row) and its sheet (at the row cap), ranked by the log
# service. The app's side only: the listener's own state on the signals
# service (retrying, held) is the listener's `status`, read live on the
# log's detail.
class LogStatus(StrEnum):
    ACTIVE = "active"
    LISTENER_GONE = "listener_gone"  # the signals service has no such listener: final
    RECONNECT = "reconnect"  # the account has no working sync key: a person reconnects
    FULL = "full"  # the sheet holds MAX_SHEET_ROWS rows: nothing more arrives
    WAITING = "waiting"  # a custom signal whose sheet holds no row


# The log poller (signals.operations.log_poller). Each polled source is due
# at its own next_poll_at and the due list is served longest-waiting first,
# so any number of pollers share the work by claim and a busy source never
# starves a quiet one.
#
# A page is the signals service's cap on one read of a listener's matches.
LOG_POLL_PAGE = 200
# A source whose last read came back short is due again after this; one
# whose page was full is due at once (behind every source already waiting).
LOG_POLL_INTERVAL_SECONDS = 32
# A source whose read failed upstream (the service down, a body off the
# contract, a cursor it refused) waits this long, so a down service is not
# hammered.
LOG_POLL_BACKOFF_SECONDS = 64
# How long a claim holds a source: well past one read and one append, so a
# live poller never loses it, and short enough that a dead one's lapses
# without a reclaim pass.
LOG_POLL_LEASE_SECONDS = 64
# A step tries at most this many due sources before it reports none: past the
# few another poller claimed in the same instant.
LOG_POLL_CLAIM_CANDIDATES = 8
# A step that found no source due is followed by this idle.
LOG_POLL_IDLE_SECONDS = 4
# A source that cannot fill until someone acts (its account's key refused,
# or its log is full) is looked at again only this often; a reconnect makes
# the account's polled sources due at once.
LOG_POLL_PARKED_SECONDS = 1024


# The copy a key-limit refusal answers with: the person's next step, in the
# app's words (the identity service's detail speaks of keys in general).
# The signals service names the open preview by id; a person reads why
# their Test waits and what ends the wait.
PREVIEW_RUNNING_DETAIL = "A test is already running on this account; try again when it finishes."

KEY_LIMIT_DETAIL = (
    "This account holds as many API keys as it may. Revoke one under Settings, API keys, to connect a log."
)
