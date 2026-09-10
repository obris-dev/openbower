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

from django.db import DatabaseError, close_old_connections, connections

from agents.constants import ToolStatus
from agents.providers import ModelUnavailable, model_for, source_config
from agents.tools import registry as tool_registry
from agents.tools.search.web_search import web_search_is_metered
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
    FillKind,
    StoredCellState,
)
from ..models import Fill, FillTask, List, ListRow
from ..services import fill_progress
from ..services.cell_run import run_cell
from ..services.fill_queue import FillQueueService
from ..services.landing import Landed, LandingContext, land_row
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
    """Whether fill web searches run through a METERED vendor: the
    web-search wiring read through the tool's own accessor, the same
    fact the admission budget reads (credentials alone route
    nothing). Public: the command narrates it at startup."""
    return web_search_is_metered()


class _Window(NamedTuple):
    """The band a fill's AIMD controller moves inside. Both are row
    counts, so only their names tell them apart."""

    start: int
    ceiling: int


def _concurrency_window(provider: str, source: str, run_override: int) -> _Window:
    """The window for the fill's AIMD controller. Canonical vendor
    sources start at FILL_CONCURRENCY_HOSTED_START and may climb to
    the ceiling; self-hosted sources start at 1 and never probe past
    their DECLARED ceiling (only the operator can see that box; an
    undeclared self-hosted source stays at 1). The fill's own override
    only ever narrows. Windows are PER PROCESS, and the deploy runs
    one fill_worker per KIND: each process runs its own controller,
    so a source can see more rows than its declared ceiling while
    test-kind rows overlap a normal fill. Each test FILL is one row
    wide, but the one-live-test rule is per account and advisory, so
    the overlap is bounded by the live test fills, not by one. A
    second worker of the SAME kind has no bound at all and is not a
    supported topology."""
    config = source_config(provider, source)
    canonical, declared = config["canonical"], config["concurrency"]
    ceiling = min(declared, MAX_FILL_CONCURRENCY) if declared else (MAX_FILL_CONCURRENCY if canonical else 1)
    if run_override:
        ceiling = min(ceiling, run_override)
    return _Window(start=min(FILL_CONCURRENCY_HOSTED_START, ceiling) if canonical else 1, ceiling=ceiling)


_STOPPED = "it stopped rather than blanking the column. Filled cells are kept."


class _Breakers:
    """The ONE across-row breaker, shared by a fill's row threads:
    consecutive rows parked for retry mean a dead or throttling provider,
    whichever provider it is (the model's, or a tool's search provider), and
    trip a config-tier fill failure that NAMES the provider from the
    cause that tripped it. Per-row attempts are patience for flaky
    moments; this is the detection that the moment is not passing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transients = 0
        self.tripped: tuple[str, str] | None = None

    def row_finished(self, *, retry_cause: str, tools: dict[str, str], blamed_tool: str = "") -> None:
        """`retry_cause` is the RETRY_CAUSES value that parked the row,
        "" for a row that finished terminally; `tools` the run's
        per-tool provider statuses; `blamed_tool` the runtime's own
        verdict on WHICH tool a tool_unavailable cause names ("" on
        records stored before the field)."""
        with self._lock:
            if self.tripped is not None:
                # First trip wins: the failed fill's error must name
                # the breaker that actually stopped the spend.
                return
            self._transients = self._transients + 1 if retry_cause else 0
            if self._transients >= CONSECUTIVE_TRANSIENT_LIMIT:
                self.tripped = self._attribute(retry_cause, tools, blamed_tool)

    def _attribute(self, retry_cause: str, tools: dict[str, str], blamed_tool: str) -> tuple[str, str]:
        """The failed fill's error, tier 1 (server-authored, rendered
        verbatim): the tool's OWN failure copy (each spec authors its
        problem and remedy, read at failure time so settings-dependent
        wording is current), composed with the fill-level stop
        sentence. Named from the cause that tripped the streak: a
        mixed streak reports its latest evidence. The BLAMED tool is
        the run's own verdict (its walk exempts tools that served,
        which the status map alone cannot show); the status walk is
        only the fallback for records stored without one, and it can
        name a tool that also served."""
        if retry_cause == StoredCellState.TOOL_UNAVAILABLE:
            for tool in self._blame_order(blamed_tool):
                status = tools.get(tool.name, ToolStatus.OPEN)
                if status != ToolStatus.OPEN:
                    copy = tool.failure_copy(status)
                    return FillFailureCode.SEARCH_THROTTLED, f"{copy.problem}; {_STOPPED}{copy.remedy}"
        return (
            FillFailureCode.PROVIDER_THROTTLED,
            f"The model provider is throttling this fill; {_STOPPED}",
        )

    @staticmethod
    def _blame_order(blamed_tool: str) -> list:
        """The blamed tool first when the record names one it can still
        resolve; registration order otherwise."""
        ordered = tool_registry.all_tools()
        blamed = [tool for tool in ordered if tool.name == blamed_tool]
        return blamed + [tool for tool in ordered if tool.name != blamed_tool]


class FillState:
    """One live fill's state: its lifecycle in one place. Its ENTRY
    is `admit` (the frozen config, the concurrency window and the controller
    resumed from the stored point, the breakers); its EXITS are
    `try_finish`, `cancel`, and `fail`, the three terminal transitions,
    each one line over fill_progress; between them, the per-row
    reactions (`row_*`) and the heartbeat. The supervisor and the row
    threads talk to the fill through this and nothing else.

    Held here rather than in a stack frame because the loop interleaves
    fills rather than running one to completion. Evicted when the fill
    leaves the live states.

    Per FILL, deliberately, not per process: a provider throttling one
    fill must not fail another, which may be on a different source
    entirely and is certainly a different consent."""

    @classmethod
    def admit(cls, fill: Fill) -> FillState | None:
        """The ENTRY: the worker takes the fill on. A source gone
        since consent is a config-tier refusal that fails the fill
        here, so the caller sees no state for it."""
        config = AgentConfig(**fill.config_snapshot)
        try:
            window = _concurrency_window(config.provider, config.source, fill.concurrency)
        except ModelUnavailable as e:
            fill_progress.fail(str(fill.id), code=FillFailureCode.SOURCE_GONE, message=str(e))
            return None
        # RESUME the climb across restarts: the operating point's last
        # value is stored on the fill, so a bounced worker starts where
        # the last one left off instead of re-earning every slot (a
        # point now too high self-corrects on the first 429).
        stored_point = fill.concurrency_point
        start = max(window.start, min(stored_point, window.ceiling)) if stored_point else window.start
        return cls(
            fill,
            config,
            ConcurrencyController(start=start, ceiling=window.ceiling),
            _Breakers(),
            window.ceiling,
        )

    def __init__(
        self,
        fill: Fill,
        config: AgentConfig,
        controller: ConcurrencyController,
        breakers: _Breakers,
        ceiling: int,
    ) -> None:
        # Read for its id and its frozen consent facts (columns, scope,
        # fingerprint) only; the live row (status, counters) is never
        # read off it, so the object admit saw is the one kept.
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

    # The EXITS: the three terminal transitions, and the liveness read
    # the row threads gate their spend on.

    def try_finish(self) -> bool:
        return fill_progress.try_finish(str(self.fill.id))

    def cancel(self) -> bool:
        """A user deletion reads as CANCELLED, never failed."""
        return fill_progress.cancel(str(self.fill.id))

    def fail(self, *, code: str, message: str) -> bool:
        """A config-tier stop with its tier-1 message."""
        return fill_progress.fail(str(self.fill.id), code=code, message=message)

    def is_live(self) -> bool:
        return fill_progress.is_live(str(self.fill.id))

    def beat(self) -> None:
        """The heartbeat the UI's staleness warning judges against,
        carrying the controller's point so a restart resumes it."""
        fill_progress.set_concurrency_point(str(self.fill.id), self.controller.current())
        self.last_beat = time.monotonic()

    # The fill's REACTIONS to a finished row: what the progress counters,
    # the concurrency controller, and the breaker are each told, in
    # order, for each way a row can end. The row thread reports the
    # facts; which collaborator hears which fact is the fill's knowledge,
    # kept here so a row never has to know it.

    def row_parked(self, run: CellRunResult, *, generation: int, newly_parked: bool) -> None:
        """A row parked for retry: the gauge counts it once (a re-park
        holds it), the controller sheds width in this row's epoch, and
        the breaker counts one more consecutive park. EVERY park beats
        (bump stamps the heartbeat): a fill whose only rows are parked
        has nothing in flight, so the renew loop skips it, and the
        retry backoffs sum past the staleness window; without this a
        healthy retrying one-row test reads as dead and a teammate's
        admission cancels it."""
        fill_progress.bump(str(self.fill.id), **({"transient": 1} if newly_parked else {}))
        self.controller.record_throttle(generation)
        self.breakers.row_finished(retry_cause=run.declined_cause, tools=run.tools, blamed_tool=run.blamed_tool)

    def row_landed(
        self,
        task: FillTask,
        landed: Landed | None,
        run: CellRunResult,
        *,
        generation: int,
        was_parked: bool,
        row_seconds: float,
        search_seconds: float,
    ) -> None:
        """A row ran to a terminal write. `landed` is None when the
        close missed (a reclaimed lease): nothing was written, so no
        counters move, but the rate signal still counts, since the
        provider was asked either way."""
        if landed is not None:
            deltas = landed.deltas(was_parked=was_parked)
            deltas["row_seconds"] = round(row_seconds)
            deltas["search_wait_seconds"] = round(search_seconds)
            fill_progress.bump(str(self.fill.id), **deltas)
            fill_progress.set_concurrency_point(str(self.fill.id), self.controller.current())
            # The pace narration: one line per row, the same facts the
            # wire carries, so `make logs` answers "what is slow".
            logger.info(
                "fill_worker: row %s %s in %.0fs (%.0fs waiting on search) | concurrency %d",
                task.row_id,
                "filled" if landed.answered else landed.declined,
                row_seconds,
                search_seconds,
                self.controller.current(),
            )
        # A FAILED search is a RATE signal, not a clean completion, and
        # so is one that only succeeded after the seam backed off (the
        # provider refused at least once; the seam's retry hides that from
        # the outcome's flag, not from its attempt count): the provider is
        # saying we are asking too fast, and counting it as clean
        # climbed the point straight into the ban the breaker then had
        # to kill the fill over. Backing off is the response; stopping
        # is what happens when backing off runs out of room.
        if any(call.status != ToolStatus.OPEN or call.attempts > 1 for call in run.tool_calls):
            self.controller.record_throttle(generation)
        else:
            self.controller.record_success(generation)
        self.breakers.row_finished(retry_cause="", tools=run.tools)

    def row_given_up(self, task: FillTask, landed: Landed | None) -> None:
        """A row closed past its attempt cap without a run: counters
        only (no provider was asked, so no rate signal and no streak).
        `task.parked` is the same flag every terminal writer reads: a
        row whose thread died every pass never parked at all."""
        if landed is not None:
            fill_progress.bump(str(self.fill.id), **landed.deltas(was_parked=task.parked))

    def row_landed_on_task(self, *, was_parked: bool) -> None:
        """A TEST row landed on its task (the caller's CAS closed it):
        only `attempted` moves (a parked task releases its gauge), and
        the drain check runs here because a one-task fill finishes on
        its only landing. One home for both task-borne landings (the
        run path and the give-up), so the counter story cannot fork."""
        fill_progress.bump(str(self.fill.id), attempted=1, **({"transient": -1} if was_parked else {}))
        self.try_finish()

    def row_missing(self, task: FillTask, closed: bool) -> None:
        """A row that no longer exists: its parked count, if any, is
        released; nothing else moves."""
        if closed and task.parked:
            fill_progress.bump(str(self.fill.id), transient=-1)


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

    def __init__(self, *, worker_id: str, stop: threading.Event, kinds: tuple[str, ...] = ()) -> None:
        # The claimant's stamp (hostname:pid in production): every lease
        # CAS the queue makes filters on it, so the queue is THIS
        # worker's, built here from its identity rather than handed in.
        self.worker_id = worker_id
        self.queue = FillQueueService(worker_id=worker_id)
        self.stop = stop
        # The kinds this instance serves ("" = all): the deploy's
        # isolation is the enumerate filter alone (live_fills), never
        # a second claim rule.
        self.kinds = kinds
        self._states: dict[str, FillState] = {}
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
        for state in list(self._states.values()):
            try:
                state.try_finish()
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
        fills = fill_progress.live_fills(self.kinds)
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
                fill_progress.fail(
                    str(fill.id),
                    code=FillFailureCode.FILL_UNRUNNABLE,
                    message="This run stopped on an internal error; start it again. Finished cells are kept.",
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
        for fill_run_id in list(self._states):
            if fill_run_id not in live_ids and not self._states[fill_run_id].in_flight:
                del self._states[fill_run_id]

    def _free_slots(self) -> int:
        return MAX_FILL_CONCURRENCY - sum(len(s.in_flight) for s in self._states.values())

    def _source_free_slots(self, state: FillState) -> int:
        """What this fill may still take WITHOUT breaking its source's
        DECLARED ceiling, counting every fill in this process that hits
        the same source.

        The ceiling is a promise about a box, not about a fill: two live
        fills on one self-hosted source, each with its own controller
        capped at the declared 1, would otherwise put 2 in flight
        against something that said it handles 1."""
        in_flight = sum(len(s.in_flight) for s in self._states.values() if s.source == state.source)
        return state.ceiling - in_flight

    def _admit(self, fill: Fill) -> FillState | None:
        state = FillState.admit(fill)
        if state is not None:
            self._states[str(fill.id)] = state
        return state

    def _process(self, fill: Fill, pool: ThreadPoolExecutor) -> None:
        state = self._states.get(str(fill.id)) or self._admit(fill)
        if state is None:
            return
        if state.breakers.tripped is not None:
            code, message = state.breakers.tripped
            state.fail(code=code, message=message)
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
            state.fail(code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
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
            future = pool.submit(self._run_row, fill, state, task)
            state.in_flight[future] = task
        if not ran and not state.in_flight:
            # Nothing claimable and nothing running HERE: complete, or
            # (stale-leased rows still cooling) leave it for a later
            # pass.
            state.try_finish()

    def _renew_and_beat(self) -> None:
        """The SUPERVISOR renews every in-flight lease each pass (one
        UPDATE on this thread's one connection): the window then
        measures PROCESS death, which is what staleness claims to
        mean."""
        rows = [row for state in self._states.values() for row in state.in_flight.values()]
        if rows:
            self.queue.renew_leases(rows)
        for fill_run_id, state in self._states.items():
            if not state.in_flight or time.monotonic() - state.last_beat < FILL_HEARTBEAT_REFRESH_SECONDS:
                continue
            # The heartbeat for the UI's warning, and the SATURATION
            # narration: how full the point is, and where it sits under
            # its ceiling (a point at ceiling with every slot full says
            # raise the ceiling; slots going unfilled say the queue or
            # the claims lag).
            logger.info(
                "fill_worker: fill %s | %d/%d rows in flight | point %d of ceiling %d | %d fills live | %d threads",
                fill_run_id,
                len(state.in_flight),
                state.controller.current(),
                state.controller.current(),
                state.ceiling,
                len(self._states),
                threading.active_count(),
            )
            state.beat()

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

    def _give_up(self, state: FillState, task: FillTask) -> None:
        """A task past its attempt cap, closed WITHOUT spending: every
        column it owed carries the RETRY cause its last park recorded
        (a park stores its run, so the cell can say whose provider refused:
        the model's, or a tool's search provider), which is retryable, so a
        later refill re-targets the row under a fresh consent.

        A retry cause rather than model_error because attempts only
        climb through parks and lost leases, and a park only happens on
        the infrastructure tier: reaching the cap means a provider kept
        refusing, not that the model answered badly. A task that never
        parked (its thread died every pass) has no stored cause and
        lands as transient. This is the ONLY writer of the terminal
        retry blanks; the give-up stamps its terminal cause over the
        park's, and the searches and evidence ride through in the
        contract's shape."""
        run = CellRunResult(**task.result) if isinstance(task.result, dict) and task.result else CellRunResult()
        if run.declined_cause not in RETRY_CAUSES:
            run = run.model_copy(update={"declined_cause": StoredCellState.TRANSIENT})
        if state.fill.kind == FillKind.TEST:
            # Same task-borne landing as the run path: the stamped
            # retry cause IS the bench's diagnosis.
            if self.queue.complete_task(task, run.model_dump()):
                state.row_landed_on_task(was_parked=task.parked)
            return
        landed = land_row(
            LandingContext.from_fill(state.fill), task.row_id, run, close=partial(self.queue.complete_task, task)
        )
        state.row_given_up(task, landed)

    def _run_row(self, fill: Fill, state: FillState, task: FillTask) -> None:
        try:
            self._run_row_inner(fill, state, task)
        except DatabaseError:
            _recover_connection()
            raise
        finally:
            # This pool THREAD is done with the row: pool threads are
            # reused, so a session left open here lives as long as the
            # process (the model call dominates a row, so the reconnect
            # is noise; leaked connections are not).
            _release_connection()

    def _run_row_inner(self, fill: Fill, state: FillState, task: FillTask) -> None:
        # The congestion epoch this row STARTS in, handed back with its
        # result. A provider burst refuses every row in flight at once,
        # and without this each refusal shed the width again: one event
        # collapsed the point to 1 instead of halving it.
        generation = state.controller.generation()
        # Pre-spend liveness: cancellation granularity is between rows
        # (in-flight spend is sunk cost, stated openly).
        if not state.is_live():
            return
        if fill.kind == FillKind.TEST:
            # A test run: its rows ride the fill itself (a list, each
            # task's position indexing its row); a test fill never
            # points at a sheet, so there is nothing to fetch.
            row_data = fill.row_data[task.position]
        else:
            row = ListRow.objects.filter(id=task.row_id, list_id=fill.list_id).first()
            if row is None:
                if not List.objects.filter(id=fill.list_id).exists():
                    # The whole list went away mid-walk: resolve the fill
                    # CANCELLED, a user deletion is never a failure story.
                    # ListService.delete purges the fill in its own txn;
                    # this is the racing walker noticing before that
                    # commit lands.
                    state.cancel()
                    return
                # The row alone is gone: this task closes as ROW_MISSING (no
                # cell to diagnose, nothing a resume could owe) and the fill
                # goes on with the rows that still exist.
                state.row_missing(task, self.queue.mark_row_missing(task))
                return
            row_data = row.data
        # Before the long IO (the model call + searches): held
        # connections must not scale with the concurrency ceiling. The
        # post-run writes reopen lazily.
        _release_connection()
        # The pace figures: row wall seconds (measured here) vs seconds
        # parked on tool calls (the run returns them per tool), so the UI
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
            run = run_cell(state.config, row_data)
        except ModelUnavailable as e:
            state.fail(code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
            return
        except tool_registry.UnknownTool as e:
            # Config tier like an unrunnable address: it fails every
            # row identically, so it fails the fill loudly instead of
            # burning attempts as anonymous thread deaths.
            state.fail(code=FillFailureCode.FILL_UNRUNNABLE, message=str(e))
            return
        # What the run PRODUCED, through the contract model, so the
        # sheet landing and the test lane's task-borne landing cannot
        # drift: both kinds store the one wire shape.
        result = CellRunResult(
            cells=dict(run.cells),
            evidence=list(run.evidence),
            tool_calls=[o.wire() for o in run.tool_calls],
            assessments=dict(run.assessments),
            declined_cause=run.declined_cause,
            blamed_tool=run.blamed_tool,
            tools=dict(run.tools),
        )
        # The row-level blank DERIVES: a partial answer lands (a park
        # would hold its filled cells hostage); only a fully blank row
        # with a retriable cause parks.
        if not run.cells and run.declined_cause in RETRY_CAUSES:
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
            state.row_parked(result, generation=generation, newly_parked=parked and not was_parked)
            return
        if fill.kind == FillKind.TEST:
            # A test run lands ON ITS TASK: no sheet write, no cell
            # truth (there may be no sheet at all).
            if self.queue.complete_task(task, result.model_dump()):
                state.row_landed_on_task(was_parked=was_parked)
            return
        # The three terminal writes (value, cell truth, close) are ONE
        # landing (services/landing.py); a reclaimed lease lands nothing.
        try:
            landed = land_row(
                LandingContext.from_fill(fill), task.row_id, result, close=partial(self.queue.complete_task, task)
            )
        except (ListNotFound, RowNotFound):
            # Same as the missing-row leg above: a user deletion
            # resolves cancelled, never failed.
            state.cancel()
            return

        row_seconds = time.monotonic() - row_started
        state.row_landed(
            task,
            landed,
            result,
            generation=generation,
            was_parked=was_parked,
            row_seconds=row_seconds,
            # Clamped to the row's wall time: sibling tool calls run on
            # parallel threads, so the per-tool sum can exceed it, and
            # the wire promises a SHARE of the row's seconds.
            search_seconds=min(sum(run.tool_call_seconds.values()), row_seconds),
        )
