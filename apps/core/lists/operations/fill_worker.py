"""What the fill worker DOES, apart from being a process.

The management command owns the process (signals, the claim loop, the
poison-fill guard); everything a fill or a row actually needs happens
here. Split because a 480-line management command is not testable the
way a module is: the AIMD window and the across-row breakers below are
pure and now importable on their own, where before they were reachable
only through a command."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from functools import partial
from typing import NamedTuple

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connections

from agents.constants import AgentTool, SearchProvider, ToolStatus
from agents.providers import ModelUnavailable, model_for, source_config
from agents.runtime.cell import run_cell
from agents.runtime.tools import CellDeps
from openbower_kernel.adaptive import ConcurrencyController
from openbower_kernel.provider_config import MAX_FILL_CONCURRENCY
from openbower_schema.agents import AgentConfig
from openbower_schema.fills import CellRunResult

from ..constants import (
    CONSECUTIVE_TRANSIENT_LIMIT,
    FILL_CONCURRENCY_HOSTED_START,
    FILL_HEARTBEAT_REFRESH_SECONDS,
    FILL_RETRY_BACKOFF_SECONDS,
    FILL_WORKER_IDLE_SECONDS,
    RETRY_CAUSES,
    FillFailureCode,
    StoredCellState,
)
from ..models import Fill, FillTask, List, ListRow
from ..services.fill_queue import FillQueueService
from ..services.landing import land_row
from ..services.lists import ListNotFound, RowNotFound

logger = logging.getLogger(__name__)


# Connection hygiene, by hand. Django recycles connections at REQUEST
# boundaries (close_old_connections runs on request_started and
# request_finished), and this process has no requests: one supervisor
# thread and a pool of row threads, each given its own thread-local
# connection the first time it touches the ORM, none of them ever
# closed unless the code does it. Two distinct needs, two names, so a
# call site says which one it is:
#
# _recover_connection: a DatabaseError reached us, which in a process
# this long-lived means the connection is dead (a Postgres restart, a
# stale socket after an idle stretch), not that the query was wrong.
# Drop it so the next statement opens a fresh one, and go on; the
# only process draining the queue must never die over a bounce.
#
# _release_connection: this thread is about to spend a long time
# needing no database (a row's model call and searches), or is done
# with a row for good. Give the session back now, so held connections
# scale with rows in their write phase, never with the concurrency
# ceiling or the pool's lifetime.


def _recover_connection() -> None:
    close_old_connections()


def _release_connection() -> None:
    connections.close_all()


def paid_search() -> bool:
    """Whether fills run their searches through the METERED door.
    Public: the command narrates it at startup."""
    return bool(settings.DATAFORSEO_LOGIN and settings.DATAFORSEO_PASSWORD)


class _Window(NamedTuple):
    """The band a fill's AIMD controller moves inside. Both are row
    counts, so only their names tell them apart."""

    start: int
    ceiling: int


def _concurrency_window(provider: str, source: str, job_override: int) -> _Window:
    """The window for the fill's AIMD controller. Canonical vendor
    sources start at FILL_CONCURRENCY_HOSTED_START and may climb to
    the ceiling; self-hosted sources start at 1 and never probe past
    their DECLARED ceiling (only the operator can see that box; an
    undeclared self-hosted source stays at 1). The fill's own override
    only ever narrows. Windows are PER PROCESS: one fill_worker per
    deploy is the supported topology, since a second worker would run
    its own controller and multiply the effective width against a
    declared ceiling."""
    config = source_config(provider, source)
    canonical, declared = config["canonical"], config["concurrency"]
    ceiling = min(declared, MAX_FILL_CONCURRENCY) if declared else (MAX_FILL_CONCURRENCY if canonical else 1)
    if job_override:
        ceiling = min(ceiling, job_override)
    return _Window(start=min(FILL_CONCURRENCY_HOSTED_START, ceiling) if canonical else 1, ceiling=ceiling)


_STOPPED = "it stopped rather than blanking the column. Filled cells are kept."


class _Breakers:
    """The ONE across-row breaker, shared by a fill's row threads:
    consecutive rows parked for retry mean a dead or throttling door,
    whichever door it is (the model's, or a tool's search door), and
    trip a config-tier fill failure that NAMES the door from the
    cause that tripped it. Per-row attempts are patience for flaky
    moments; this is the detection that the moment is not passing."""

    def __init__(self, *, search_provider: str) -> None:
        self._lock = threading.Lock()
        self._transients = 0
        self._search_provider = search_provider
        self.tripped: tuple[str, str] | None = None

    def row_finished(self, *, retry_cause: str, tools: dict[str, str]) -> None:
        """`retry_cause` is the RETRY_CAUSES value that parked the row,
        "" for a row that finished terminally; `tools` the run's
        per-tool door statuses (what a tool_unavailable cause names)."""
        with self._lock:
            if self.tripped is not None:
                # First trip wins: the failed fill's error must name
                # the breaker that actually stopped the spend.
                return
            self._transients = self._transients + 1 if retry_cause else 0
            if self._transients >= CONSECUTIVE_TRANSIENT_LIMIT:
                self.tripped = self._attribute(retry_cause, tools)

    def _attribute(self, retry_cause: str, tools: dict[str, str]) -> tuple[str, str]:
        """The failed fill's error, tier 1 (server-authored, rendered
        verbatim): which tool, what its door said, which door, and the
        remedy where one exists. Named from the cause that tripped the
        streak: a mixed streak reports its latest evidence."""
        if retry_cause == StoredCellState.TOOL_UNAVAILABLE:
            for tool in AgentTool:
                status = tools.get(tool.value, ToolStatus.OPEN)
                if status != ToolStatus.OPEN:
                    return FillFailureCode.SEARCH_THROTTLED, self._tool_message(tool, status)
        return (
            FillFailureCode.PROVIDER_THROTTLED,
            f"The model provider is throttling this fill; {_STOPPED}",
        )

    def _tool_message(self, tool: AgentTool, status: str) -> str:
        name = "Web search" if tool is AgentTool.WEB_SEARCH else "Finding contacts"
        said = _STATUS_PHRASE.get(status, "is failing on")
        free = tool is AgentTool.WEB_SEARCH and self._search_provider != SearchProvider.DATAFORSEO
        door = "the free search provider" if free else "DataForSEO"
        remedy = " Connect DataForSEO for metered search." if free else ""
        return f"{name} {said} {door}; {_STOPPED}{remedy}"


# What a door's status reads as in the breaker's sentence.
_STATUS_PHRASE = {
    ToolStatus.RATE_LIMITED: "is being rate-limited by",
    ToolStatus.UNREACHABLE: "cannot reach",
    ToolStatus.ERROR: "is failing on",
}


class _FillState:
    """One live fill's per-process state, held ACROSS supervisor passes.
    The loop interleaves fills rather than running one to completion,
    so none of this can live in a stack frame: the frozen config, the
    AIMD controller, the
    across-row breakers, the rows this process has in flight, and the
    per-row death counts. Evicted when the fill leaves the live states.

    Per FILL, deliberately, not per process: a provider throttling one
    fill must not fail another, which may be on a different source
    entirely and is certainly a different consent."""

    def __init__(
        self,
        fill: Fill,
        config: AgentConfig,
        controller: ConcurrencyController,
        breakers: _Breakers,
        ceiling: int,
    ) -> None:
        self.fill = fill
        self.config = config
        self.controller = controller
        self.breakers = breakers
        self.ceiling = ceiling
        self.in_flight: dict[Future, FillTask] = {}
        self.deaths = 0
        self.last_beat = time.monotonic()
        # The source this fill's rows hit, so the pool can honour a
        # DECLARED per-source ceiling across every fill using it.
        self.source = (config.provider, config.source)


class FillWorkerOperation:
    """The worker's work: EVERY live fill, interleaved. One pass claims
    a slice from each fill in rotation, runs those rows on one
    process-wide pool, renews their leases, and harvests what finished.

    Rotation is what keeps a wide fill from holding the worker for the
    length of its own run, since the fill beside it belongs to a
    different account.

    The pool is the process-wide bound (MAX_FILL_CONCURRENCY), so the
    sum of every fill's operating point is capped by the slots actually
    free; a fill's own AIMD point caps its share of them, and its
    source's declared ceiling caps every fill hitting that source."""

    def __init__(self, *, worker_id: str, stop: threading.Event) -> None:
        # The claimant's stamp (hostname:pid in production): every lease
        # CAS the queue makes filters on it, so the queue is THIS
        # worker's, built here from its identity rather than handed in.
        self.worker_id = worker_id
        self.queue = FillQueueService(worker_id=worker_id)
        self.stop = stop
        self._states: dict[str, _FillState] = {}
        # Whether the pass in progress found anything to do; the loop
        # idles and `--once` stops on this rather than on liveness.
        self._claimed = False
        # Rotates the fill that gets first pick of the free slots, so a
        # wide fill cannot take every slot every pass simply by being
        # oldest.
        self._turn = 0

    def run(self, *, once: bool = False) -> None:
        with ThreadPoolExecutor(max_workers=MAX_FILL_CONCURRENCY) as pool:
            while not self.stop.is_set():
                try:
                    worked = self._one_pass(pool)
                except DatabaseError:
                    # A DB restart mid-pass must idle and retry, never
                    # kill the process (row threads do this per row).
                    _recover_connection()
                    if self.stop.wait(FILL_WORKER_IDLE_SECONDS):
                        break
                    continue
                if not worked:
                    if once:
                        break
                    if self.stop.wait(FILL_WORKER_IDLE_SECONDS):
                        break
        # The pool exit drained the rows still running, and their
        # terminal writes landed during that drain: one more completion
        # attempt per fill, or a fill strands live with nothing
        # claimable. No-ops unless every row is terminal.
        for fill_id in list(self._states):
            try:
                self.queue.try_finish(fill_id)
            except DatabaseError:
                _recover_connection()
        # The supervisor's own session, on the way out.
        _release_connection()

    def _one_pass(self, pool: ThreadPoolExecutor) -> bool:
        """Claim, renew, harvest. Returns whether this pass FOUND WORK:
        anything claimed, or anything still running here.

        Not "is any fill live". A fill whose tasks are all parked on a
        backoff is live and has nothing claimable, so liveness would
        spin this loop against a clock and never let `--once` stop."""
        self._claimed = False
        fills = self.queue.live_fills()
        self._evict({str(fill.id) for fill in fills})
        for fill in self._rotated(fills):
            # A poisoned fill fails ALONE. Unhandled here the process
            # exits, and every other account's fill dies with it.
            try:
                self._process(fill, pool)
            except DatabaseError:
                raise
            except Exception:
                logger.exception("fill_worker: fill %s failed unhandled", fill.id)
                self.queue.fail_fill(
                    str(fill.id),
                    code=FillFailureCode.FILL_UNRUNNABLE,
                    message="This fill stopped on an internal error; start a new fill. Filled cells are kept.",
                )
                self._states.pop(str(fill.id), None)
        self._renew_and_beat()
        self._harvest()
        return self._claimed or any(state.in_flight for state in self._states.values())

    def _rotated(self, fills: list[Fill]) -> list[Fill]:
        if not fills:
            return fills
        self._turn = (self._turn + 1) % len(fills)
        return fills[self._turn :] + fills[: self._turn]

    def _evict(self, live_ids: set[str]) -> None:
        """Forget a fill the moment it leaves the live states. Rows of
        it still in flight keep running (their spend is sunk) and land
        through the CAS, which refuses a terminal fill's rows anyway."""
        for fill_id in list(self._states):
            if fill_id not in live_ids and not self._states[fill_id].in_flight:
                del self._states[fill_id]

    def _free_slots(self) -> int:
        return MAX_FILL_CONCURRENCY - sum(len(s.in_flight) for s in self._states.values())

    def _source_free_slots(self, state: _FillState) -> int:
        """What this fill may still take WITHOUT breaking its source's
        DECLARED ceiling, counting every fill in this process that hits
        the same source.

        The ceiling is a promise about a box, not about a fill: two live
        fills on one self-hosted source, each with its own controller
        capped at the declared 1, would otherwise put 2 in flight
        against something that said it handles 1."""
        in_flight = sum(len(s.in_flight) for s in self._states.values() if s.source == state.source)
        return state.ceiling - in_flight

    def _process(self, fill: Fill, pool: ThreadPoolExecutor) -> None:
        state = self._states.get(str(fill.id)) or self._admit(fill)
        if state is None:
            return
        state.fill = fill
        if state.breakers.tripped is not None:
            code, message = state.breakers.tripped
            self.queue.fail_fill(str(fill.id), code=code, message=message)
            return
        if state.deaths:
            # A death means claiming is the wrong move (the likely
            # cause, an exhausted DB pool, only worsens under more
            # claims): sit this fill out one pass, loudly.
            logger.warning("fill_worker: pausing claims on %s after %d dead row threads", fill.id, state.deaths)
            state.deaths = 0
            return
        if self.stop.is_set():
            # A stop request stops BUYING, and this is the line where
            # buying starts: the claim below stamps an attempt before any
            # row runs, so a shutdown that outran it would spend both a
            # metered call and one of the row's retries. It also sits
            # above the claim-time roster resolve, which is a live network
            # probe charged per fill per pass: a pass that will buy
            # nothing has no use for it while the drain is waiting.
            # Rows already in flight still finish, which is what the drain
            # is for. Best effort by nature: stop can arrive after this
            # check, and that pass buys one more batch.
            return
        # Claim-time model resolution is AUTHORITATIVE (a stale reclaim
        # hours later re-resolves against the current world); a refusal
        # is a config-tier fill failure. The resolved object is
        # DISCARDED: rows resolve their own, since a run closes its
        # model's client and a shared instance dies under whichever row
        # finishes second.
        try:
            model_for(state.config.provider, state.config.source, state.config.model)
        except ModelUnavailable as e:
            self.queue.fail_fill(str(fill.id), code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
            self._states.pop(str(fill.id), None)
            return
        share = min(
            state.controller.current() - len(state.in_flight),
            self._free_slots(),
            self._source_free_slots(state),
        )
        batch = self.queue.claim_batch(fill, free_slots=share)
        ran = False
        for task in batch.tasks:
            # Set for EVERY claimed task, the give-up branch included: a
            # pass that turned a task terminal did work, so the loop
            # must not idle or --once stop as though it found nothing.
            self._claimed = True
            if self.queue.exhausted(task):
                # Its patience is spent. Decided HERE, from the attempt
                # the claim just stamped, so a task that exhausted its
                # retries and one that died mid-run at the cap resolve
                # by the same rule and neither can strand its fill.
                self._give_up(state, task)
                continue
            ran = True
            future = pool.submit(self._run_row, fill, state.config, task, state.controller, state.breakers)
            state.in_flight[future] = task
        if not ran and not state.in_flight:
            # Nothing claimable and nothing running HERE: complete, or
            # (stale-leased rows still cooling) leave it for a later
            # pass.
            self.queue.try_finish(str(fill.id))

    def _admit(self, fill: Fill) -> _FillState | None:
        config = AgentConfig(**fill.config_snapshot)
        try:
            window = _concurrency_window(config.provider, config.source, fill.concurrency)
        except ModelUnavailable as e:
            self.queue.fail_fill(str(fill.id), code=FillFailureCode.SOURCE_GONE, message=str(e))
            return None
        # RESUME the climb across restarts: the operating point's last
        # value is stored on the fill, so a bounced worker starts where
        # the last one left off instead of re-earning every slot (a
        # point now too high self-corrects on the first 429).
        stored_point = fill.concurrency_point
        start = max(window.start, min(stored_point, window.ceiling)) if stored_point else window.start
        state = _FillState(
            fill,
            config,
            ConcurrencyController(start=start, ceiling=window.ceiling),
            _Breakers(search_provider=settings.SEARCH_PROVIDER),
            window.ceiling,
        )
        self._states[str(fill.id)] = state
        return state

    def _renew_and_beat(self) -> None:
        """The SUPERVISOR renews every in-flight lease each pass (one
        UPDATE on this thread's one connection): the window then
        measures PROCESS death, which is what staleness claims to
        mean."""
        rows = [row for state in self._states.values() for row in state.in_flight.values()]
        if rows:
            self.queue.renew_leases(rows)
        for fill_id, state in self._states.items():
            if not state.in_flight or time.monotonic() - state.last_beat < FILL_HEARTBEAT_REFRESH_SECONDS:
                continue
            # The heartbeat for the UI's warning, and the SATURATION
            # narration: how full the point is, and where it sits under
            # its ceiling (a point at ceiling with every slot full says
            # raise the ceiling; slots going unfilled say the queue or
            # the claims lag).
            logger.info(
                "fill_worker: fill %s | %d/%d rows in flight | point %d of ceiling %d | %d fills live | %d threads",
                fill_id,
                len(state.in_flight),
                state.controller.current(),
                state.controller.current(),
                state.ceiling,
                len(self._states),
                threading.active_count(),
            )
            self.queue.set_concurrency_point(fill_id, state.controller.current())
            state.last_beat = time.monotonic()

    def _harvest(self) -> None:
        pending = {f: state for state in self._states.values() for f in state.in_flight}
        if not pending:
            return
        done, _running = wait(set(pending), timeout=FILL_WORKER_IDLE_SECONDS, return_when=FIRST_COMPLETED)
        for future in done:
            state = pending[future]
            task = state.in_flight.pop(future)
            exc = future.exception()
            if exc is None:
                continue
            state.deaths += 1
            logger.warning(
                "fill_worker: row thread died on row %s (%s): %s; releasing its lease",
                task.row_id,
                type(exc).__name__,
                exc,
            )
            try:
                # Just release it. The attempt was counted AT CLAIM, so
                # a task that dies deterministically walks to the cap and
                # is given up on there, and it does so across process
                # restarts, which an in-memory death count could not.
                self.queue.release_lease(task)
            except DatabaseError:
                _recover_connection()

    def _give_up(self, state: _FillState, task: FillTask) -> None:
        """A task past its attempt cap, closed WITHOUT spending: every
        column it owed carries the RETRY cause its last park recorded
        (a park stores its run, so the cell can say whose door refused:
        the model's, or a tool's search door), which is retryable, so a
        later refill re-targets the row under a fresh consent.

        A retry cause rather than model_error because attempts only
        climb through parks and lost leases, and a park only happens on
        the infrastructure tier: reaching the cap means a door kept
        refusing, not that the model answered badly. A task that never
        parked (its thread died every pass) has no stored cause and
        lands as transient. This is the ONLY writer of the terminal
        retry blanks; the stored run stays as the audit."""
        run = CellRunResult(**task.result) if isinstance(task.result, dict) and task.result else CellRunResult()
        if run.declined_cause not in RETRY_CAUSES:
            run = run.model_copy(
                update={"blank_cause": StoredCellState.TRANSIENT, "declined_cause": StoredCellState.TRANSIENT}
            )
        landed = land_row(state.fill, task.row_id, run, close=partial(self.queue.complete_task, task))
        if landed is not None:
            # Same flag the other terminal writer reads. `attempts > 1`
            # could never be false here (this path is reached only past
            # the attempt cap), so it decremented unconditionally, and
            # a row whose thread died every pass never parked at all.
            deltas = landed.deltas(was_parked=task.parked)
            self.queue.bump(str(state.fill.id), **deltas)

    def _run_row(
        self,
        fill: Fill,
        config: AgentConfig,
        task: FillTask,
        controller: ConcurrencyController,
        breakers: _Breakers,
    ) -> None:
        try:
            self._run_row_inner(fill, config, task, controller, breakers)
        except DatabaseError:
            _recover_connection()
            raise
        finally:
            # This pool THREAD is done with the row: pool threads are
            # reused, so a session left open here lives as long as the
            # process (the model call dominates a row, so the reconnect
            # is noise; leaked connections are not).
            _release_connection()

    def _run_row_inner(
        self,
        fill: Fill,
        config: AgentConfig,
        task: FillTask,
        controller: ConcurrencyController,
        breakers: _Breakers,
    ) -> None:
        # The congestion epoch this row STARTS in, handed back with its
        # result. A provider burst refuses every row in flight at once,
        # and without this each refusal shed the width again: one event
        # collapsed the point to 1 instead of halving it.
        generation = controller.generation()
        # Pre-spend liveness: cancellation granularity is between rows
        # (in-flight spend is sunk cost, stated openly).
        if not self.queue.fill_is_live(str(task.fill_id)):
            return
        row = ListRow.objects.filter(id=task.row_id, list_id=fill.list_id).first()
        if row is None:
            if not List.objects.filter(id=fill.list_id).exists():
                # The whole list went away mid-walk: resolve the fill
                # CANCELLED, a user deletion is never a failure story.
                # ListService.delete purges the fill in its own txn;
                # this is the racing walker noticing before that
                # commit lands.
                self.queue.cancel_fill(str(fill.id))
                return
            # The row alone is gone: this task closes as ROW_MISSING (no
            # cell to diagnose, nothing a resume could owe) and the fill
            # goes on with the rows that still exist.
            if self.queue.mark_row_missing(task) and task.parked:
                self.queue.bump(str(fill.id), transient=-1)
            return
        # No DB in deps callbacks: they execute on the framework's
        # ephemeral executor threads, where a connection opened is a
        # connection leaked (the supervisor loop renews leases).
        deps = CellDeps()
        # Before the long IO (the model call + searches): held
        # connections must not scale with the concurrency ceiling. The
        # post-run writes reopen lazily.
        _release_connection()
        # The pace figures: row wall seconds (measured here) vs seconds
        # parked on search (the runtime counts them on deps), so the UI
        # and the logs can say WHAT is slow instead of a bare ETA.
        row_started = time.monotonic()
        # Whether a park has already counted this task into the gauge.
        # The wire's `transient` counts rows CURRENTLY parked in retry,
        # so a park bumps it once, a re-park holds it, and every
        # terminal write for a parked row releases it: this one, the
        # give-up path, and the abandon sweep a stop performs.
        # A STORED flag, not `attempts` (which climbs at claim, so a
        # released lease raises it with no park behind it) and not
        # `not_before` (which the next claim clears, so a task that
        # parked and then lost its worker reads as never parked).
        was_parked = task.parked
        # Per-row model resolution ON PURPOSE: a run closes its model's
        # client, so concurrent rows must never share one (the first
        # finisher would kill every sibling's completion).
        try:
            run = run_cell(config, row.data, deps=deps)
        except ModelUnavailable as e:
            self.queue.fail_fill(str(fill.id), code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
            return
        # What the run PRODUCED, through the contract model, so this
        # writer and the bench's cannot drift: a seeded row and a run
        # row land the same shape in the same column.
        result = CellRunResult(
            cells=dict(run.cells),
            evidence=list(run.evidence),
            searches=[o.wire() for o in run.searches],
            assessments=dict(run.assessments),
            blank_cause=run.blank_cause,
            declined_cause=run.declined_cause,
            tools=dict(run.tools),
        )
        if run.blank_cause in RETRY_CAUSES:
            # A park diagnoses NOTHING on the sheet: nothing terminal
            # happened, the cell is still owed, and it still shimmers
            # because its task is still queued. The run is STORED
            # though: the searches that refused are the audit, and the
            # give-up path reads the cause off it. Exhaustion is not
            # decided here either; the next claim sees the attempt
            # count and gives up.
            parked = self.queue.park_task(
                task,
                backoff_seconds=FILL_RETRY_BACKOFF_SECONDS * task.attempts,
                result=result.model_dump(),
            )
            if parked and not was_parked:
                self.queue.bump(str(fill.id), transient=1)
            controller.record_throttle(generation)
            breakers.row_finished(retry_cause=run.blank_cause, tools=run.tools)
            return
        # The three terminal writes (value, cell truth, close) are ONE
        # landing (services/landing.py); a reclaimed lease lands nothing.
        try:
            landed = land_row(fill, str(row.id), result, close=partial(self.queue.complete_task, task))
        except (ListNotFound, RowNotFound):
            # Same as the missing-row leg above: a user deletion
            # resolves cancelled, never failed.
            self.queue.cancel_fill(str(fill.id))
            return

        if landed is not None:
            deltas = landed.deltas(was_parked=was_parked)
            row_seconds = time.monotonic() - row_started
            deltas["row_seconds"] = round(row_seconds)
            deltas["search_wait_seconds"] = round(deps.search_seconds)
            self.queue.bump(str(fill.id), **deltas)
            self.queue.set_concurrency_point(str(fill.id), controller.current())
            # The pace narration: one line per row, the same facts the
            # wire carries, so `make logs` answers "what is slow".
            logger.info(
                "fill_worker: row %s %s in %.0fs (%.0fs waiting on search) | concurrency %d",
                task.row_id,
                "filled" if landed.answered else landed.declined,
                row_seconds,
                deps.search_seconds,
                controller.current(),
            )
        # A FAILED search is a RATE signal, not a clean completion, and
        # so is one that only succeeded after the seam backed off (the
        # door refused at least once; the seam's retry hides that from
        # the outcome's flag, not from its attempt count): the door is
        # saying we are asking too fast, and counting it as clean
        # climbed the point straight into the ban the breaker then had
        # to kill the fill over. Backing off is the response; stopping
        # is what happens when backing off runs out of room.
        if any(outcome.failed or outcome.attempts > 1 for outcome in run.searches):
            controller.record_throttle(generation)
        else:
            controller.record_success(generation)
        breakers.row_finished(retry_cause="", tools=run.tools)
