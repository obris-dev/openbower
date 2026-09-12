"""Fill progress and lifecycle: what happens to the FILL row as its
rows resolve. The status machine (complete when no non-terminal task
remains; cancelled or failed through the ONE terminal transition the
user's Stop also takes).

Progress counters are NOT written here: the wire derives them from the
task rows and cell states at read time (services.fills.derive_counters).

Split from the task state machine on purpose: fill_tasks.py is TASK
lifecycle (claim, settle, park), this is the fill's. Plain functions,
because none of this holds state: a fill id in, one UPDATE out. The
consumer reports here after each settle (try_finish), never through an
in-memory object.

Not account-scoped: the consumer is a trusted process serving every
account's fills, and the user-facing service resolves its fill
account-scoped before it calls stop_fill.
"""

from __future__ import annotations

from collections.abc import Iterator

from django.db import transaction
from django.utils import timezone

from ..constants import LIVE_FILL_STATUSES, NON_TERMINAL_FILL_TASK_STATES, FillStatus, FillTaskStatus
from ..models import Fill, FillTask


def iter_live_fills(kinds: tuple[str, ...] = ()) -> Iterator[Fill]:
    """Every live fill, oldest first, LAZILY (single-pass); `kinds`
    narrows to the named operating modes (empty = all). The manual
    provisioner iterates these and publishes each fill's READY tasks, so
    per-fill depth is the fairness point (a wide fill cannot flood the
    bus). Streamed via .iterator() so a growing number of live fills
    never materializes as one list."""
    qs = Fill.objects.filter(status__in=LIVE_FILL_STATUSES)
    if kinds:
        qs = qs.filter(kind__in=kinds)
    yield from qs.order_by("id").iterator()


def cancel(fill_run_id: str) -> bool:
    """The worker's list-gone resolution: a user deletion reads as
    CANCELLED, never failed (failed is config-tier and carries an
    error the UI dresses as a failure story). Same transition as the
    user cancel; a fill already terminal stays put."""
    return stop_fill(fill_run_id, FillStatus.CANCELLED)


def fail(fill_run_id: str, *, code: str, message: str) -> bool:
    """The breaker path (config-tier: a dead or throttling provider
    fails the whole fill loudly). From live states only; a cancel
    that already landed stays cancelled."""
    return stop_fill(fill_run_id, FillStatus.FAILED, code=code, message=message)


def live_fill_count(account_id: str) -> int:
    """The account's fills that are still live, EVERY kind: a test run
    is a fill, so it counts against the same metered cap by
    construction (the one rule that used to need a cross-app import to
    enforce)."""
    return Fill.objects.filter(account_id=account_id, status__in=LIVE_FILL_STATUSES).count()


def try_finish(fill_run_id: str) -> bool:
    """THE completion rule, run by the consumer after each settle
    (opportunistic empty-check): a fill flips COMPLETE when no
    NON-TERMINAL task remains (READY, QUEUED, or PROCESSING).

    Monotonic by construction, because nothing creates tasks after
    admission: the set only ever shrinks, so the check cannot go stale
    between reading and flipping. A PROCESSING task (a consumer owns it)
    or a READY one (published or not) is still owed, so a crashed
    claimant never fakes completion.

    Module level rather than a consumer method because it reads no
    worker identity: a fill is finished or it is not, whoever is
    asking."""
    with transaction.atomic():
        fill = Fill.objects.select_for_update().filter(id=fill_run_id, status__in=LIVE_FILL_STATUSES).first()
        if fill is None:
            return False
        if FillTask.objects.filter(fill_run_id=fill_run_id, status__in=NON_TERMINAL_FILL_TASK_STATES).exists():
            return False
        fill.status = FillStatus.COMPLETE
        fill.save(update_fields=["status", "updated_at"])
        return True


def stop_fill(fill_run_id: str, status: FillStatus, *, code: str = "", message: str = "") -> bool:
    """THE terminal transition, shared by the worker's paths and the
    user's cancel, so the two cannot order their writes differently.

    The QUEUE IS SWEPT FIRST, then the fill flips. That order is
    load-bearing: the terminal write path takes FillTask before Fill,
    so flipping the fill first would invert it and deadlock. A consumer
    that claims a task in the window between the two is harmless,
    because its terminal CAS finds the task abandoned.

    Nothing on the sheet is touched. Every cell this fill would have
    reached was pending only because a non-terminal task said so, so
    abandoning the READY/QUEUED tasks is what stops the shimmer, and
    there is no state to sweep back.
    """
    with transaction.atomic():
        if not Fill.objects.filter(id=fill_run_id, status__in=LIVE_FILL_STATUSES).exists():
            return False
        _abandon_queued(fill_run_id)
        flipped = Fill.objects.filter(id=fill_run_id, status__in=LIVE_FILL_STATUSES).update(
            status=status,
            error_code=code,
            error_message=message,
            updated_at=timezone.now(),
        )
    return flipped == 1


def _abandon_queued(fill_run_id: str) -> None:
    """Consent granted and not spent, recorded rather than deleted: it
    is the only honest answer to what a stopped fill still owed, and a
    later resume reads it instead of reconstructing it.

    Sweeps the READY and QUEUED tasks to ABANDONED, deliberately NOT
    PROCESSING: a task a consumer already owns runs to its own terminal
    CAS and LANDS its cell (the settle keys on the task's status, not the
    fill's), so in-flight spend is sunk cost, cancel granularity is
    between tasks. Leaving PROCESSING untouched also keeps the
    queue-before-fill lock order the caller depends on. The transient
    count is DERIVED now, so nothing is released here."""
    FillTask.objects.filter(fill_run_id=fill_run_id, status__in=(FillTaskStatus.READY, FillTaskStatus.QUEUED)).update(
        status=FillTaskStatus.ABANDONED, updated_at=timezone.now()
    )
