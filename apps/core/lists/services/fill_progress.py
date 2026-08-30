"""Fill progress and lifecycle: what happens to the FILL row as its
rows resolve. The counters the tray polls and the stored concurrency
point (per-row deltas, written with F() expressions so 64 threads can
report without meeting on the fill's hottest row), and the status
machine (complete when no queued task remains; cancelled or failed
through the ONE terminal transition the user's Stop also takes).

Split from the queue on purpose: the queue is TASK lifecycle (leases,
claims, closes), this is the fill's. Plain functions, because none of
this holds state: a fill id in, one UPDATE out. A worker's row thread
reports here through its fill's in-process state
(operations/fill_worker.py), never directly.

Not account-scoped: the worker is a trusted process serving every
account's fills, and the user-facing service resolves its fill
account-scoped before it calls stop_fill.
"""

from __future__ import annotations

from django.db import models, transaction
from django.utils import timezone

from ..constants import LIVE_FILL_STATUSES, FillStatus, FillTaskStatus
from ..models import Fill, FillTask


def live_fills() -> list[Fill]:
    """Every live fill, oldest first. A READ, not a claim: the
    task-level skip_locked claim is what arbitrates between
    workers, and the supervisor interleaves these rather than
    working one to completion.

    Deliberately unfiltered by claimable work. A fill whose tasks
    are all leased has nothing claimable but is very much running,
    and the supervisor needs it in hand to renew those leases;
    deciding a fill is drained is the supervisor's call, since only
    it knows what this process still has in flight."""
    return list(Fill.objects.filter(status__in=LIVE_FILL_STATUSES).order_by("id"))


def is_live(fill_id: str) -> bool:
    """The worker's pre-task liveness check (cancel granularity is
    between tasks; in-flight spend is sunk cost, stated openly)."""
    return Fill.objects.filter(id=fill_id, status__in=LIVE_FILL_STATUSES).exists()


def bump(fill_id: str, **deltas: int) -> None:
    """Per-task progress plus the heartbeat stamp: ONE unlocked
    UPDATE with F() expressions. Counters are integer columns
    precisely so 64 threads can increment them without meeting on
    this row; a JSON dict would need select_for_update and a
    read-modify-write, which serializes the whole pool on the
    fill's hottest row.

    Delta keys: attempted, filled, blank, transient, row_seconds,
    search_wait_seconds."""
    Fill.objects.filter(id=fill_id).update(
        heartbeat_at=timezone.now(),
        updated_at=timezone.now(),
        **{key: models.F(key) + delta for key, delta in deltas.items()},
    )


def set_concurrency_point(fill_id: str, point: int) -> None:
    """The AIMD gauge OVERWRITES (it is the operating point right
    now, not a sum), so it cannot ride the F() bump above."""
    Fill.objects.filter(id=fill_id).update(concurrency_point=point, heartbeat_at=timezone.now())


def cancel(fill_id: str) -> bool:
    """The worker's list-gone resolution: a user deletion reads as
    CANCELLED, never failed (failed is config-tier and carries an
    error the UI dresses as a failure story). Same transition as the
    user cancel; a fill already terminal stays put."""
    return stop_fill(fill_id, FillStatus.CANCELLED)


def fail(fill_id: str, *, code: str, message: str) -> bool:
    """The breaker path (config-tier: a dead or throttling provider
    fails the whole fill loudly). From live states only; a cancel
    that already landed stays cancelled."""
    return stop_fill(fill_id, FillStatus.FAILED, code=code, message=message)


def live_fill_count(account_id: str) -> int:
    """The account's fills that are still live. Public because the
    BENCH lane shares this account cap: it must not reach into the
    fills domain to count them, nor re-spell which statuses count as
    live. It lives here rather than in fill_admission because that
    module imports agents, and agents importing it back would close a
    cycle."""
    return Fill.objects.filter(account_id=account_id, status__in=LIVE_FILL_STATUSES).count()


def try_finish(fill_id: str) -> bool:
    """THE completion rule, shared by the worker's drain and admission,
    so the two cannot disagree about when a fill is done: a fill flips
    COMPLETE when no QUEUED task remains.

    Monotonic by construction, because nothing creates tasks after
    admission: the set only ever shrinks, so the check cannot go stale
    between reading and flipping. A stale-leased task is still queued,
    so a crashed claimant never fakes completion.

    Admission needs it because a fill can be born drained: every row it
    consented to was answered on the bench, so nothing is claimable and
    no worker would ever visit it. That is the same question the worker
    asks after its last row, and asking it in two places is how the two
    answers drift.

    Module level rather than a queue method because it reads no worker
    identity: a fill is finished or it is not, whoever is asking."""
    with transaction.atomic():
        fill = Fill.objects.select_for_update().filter(id=fill_id, status__in=LIVE_FILL_STATUSES).first()
        if fill is None:
            return False
        if FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.QUEUED).exists():
            return False
        fill.status = FillStatus.COMPLETE
        fill.save(update_fields=["status", "updated_at"])
        return True


def stop_fill(fill_id: str, status: FillStatus, *, code: str = "", message: str = "") -> bool:
    """THE terminal transition, shared by the worker's paths and the
    user's cancel, so the two cannot order their writes differently.

    The QUEUE IS SWEPT FIRST, then the fill flips. That order is
    load-bearing: the terminal write path takes FillTask before Fill,
    so flipping the fill first would invert it and deadlock. A worker
    that claims a task in the window between the two is harmless,
    because its terminal CAS finds the task abandoned.

    Nothing on the sheet is touched. Every cell this fill would have
    reached was pending only because a QUEUED task said so, so
    abandoning the tasks is what stops the shimmer, and there is no
    state to sweep back.
    """
    with transaction.atomic():
        if not Fill.objects.filter(id=fill_id, status__in=LIVE_FILL_STATUSES).exists():
            return False
        released = _abandon_queued(fill_id)
        # The released rows ride the SAME update as the status, so the
        # queue-then-fill lock order the docstring above depends on is
        # one write per table, not two.
        flipped = Fill.objects.filter(id=fill_id, status__in=LIVE_FILL_STATUSES).update(
            status=status,
            error_code=code,
            error_message=message,
            updated_at=timezone.now(),
            transient=models.F("transient") - released,
        )
    return flipped == 1


def _abandon_queued(fill_id: str) -> int:
    """Consent granted and not spent, recorded rather than deleted: it
    is the only honest answer to what a stopped fill still owed, and a
    later resume reads it instead of reconstructing it.

    Returns how many PARKED rows it abandoned, so the caller can
    release them from the fill's transient gauge in the same update
    that flips the status. This is the THIRD terminal writer: a parked
    task leaves QUEUED either through complete_task, which decrements,
    or through here, and try_finish refuses to complete a fill while
    anything is still queued, so there is no other exit. It matters
    most on the fills likeliest to have parked rows: the throttle
    breaker fails a fill precisely when they are.

    Counted AFTER the sweep, over what the sweep produced. A row a
    worker completed in between reads DONE either way, so it is not in
    this set and its own terminal write already released it. And a park
    attempted after the sweep holds the row locks finds its
    status=QUEUED CAS matching nothing, returns False, and never bumps
    the gauge, so there is nothing counted here that was not
    incremented and nothing incremented that is not counted. Reading
    the ids BEFORE the sweep would not hold: a row parked between the
    read and the sweep would be abandoned without ever being
    released."""
    FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.QUEUED).update(
        status=FillTaskStatus.ABANDONED, leased_at=None, leased_by="", updated_at=timezone.now()
    )
    return FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.ABANDONED, parked=True).count()
