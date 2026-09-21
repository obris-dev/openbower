from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import (
    LEASED_BY_MAX_LENGTH,
    NODE_KIND_MAX_LENGTH,
    NODE_RUN_STATUS_MAX_LENGTH,
    NON_TERMINAL_NODE_RUN_STATES,
    RANK_MAX_LENGTH,
    NodeRunStatus,
)
from ..nodes.registry import WEBHOOK


class NodeRun(AccountScopedModel):
    """One run of one node for one row, AND the queue itself: the
    per-row ledger of every node kind.

    One task per sheet row, materialized at admission, so the queue is
    simultaneously the work list, the pending signal the sheet renders
    from, and the durable record of consent. That third fill is why the
    rows survive their fill: a cancelled fill's ABANDONED tasks are the
    only honest answer to "what did this still owe", which a queue
    holding only what a planner had reached could not give.

    Every task carries its `node_id` and the node's `kind` from birth.
    A column_agent run is an agent run, fill-backed or automatic (a
    fill-backed task's node is the column_agent node for its fill's
    agent on the sheet, and `fill_run_id` is the fill JOB's id). A task
    with NO fill run (`fill_run_id` NULL) is the automatic path:
    autofill rides the same queue and the same worker, minus the
    consent a fill job records, resolving its list and user from its
    row; a webhook run is DEFERRED at birth and claimed
    by the flush at its window, never by the worker. A BENCH run (the
    agent builder's one-row diagnostic) is an automatic run of the
    account's bench node that OWNS ITS INPUT (`input`): no row, no
    sheet, its result lands on itself.

    `status` speaks about the WORK and never about the answer; the
    answer is diagnosed per cell on ListCellState. No word appears in
    both vocabularies, which is what keeps them from reading as copies
    of each other.

    AccountScopedModel, so `account_id` is DENORMALIZED from the owning
    fill. Every read still resolves that fill account-scoped, so this
    is defence in depth rather than the primary guard: scoping by
    convention holds only while every call site remembers, and one did
    not (a resume leg read another account's rows by a request-supplied
    id). Carrying the account makes an unscoped query a thing you have
    to write on purpose."""

    # NULL on the automatic path (autofill): a task with no fill run has
    # no fill job to read its list or user off, so it resolves them from
    # its row. A fill-backed task sets this to its fill job's id.
    fill_run_id = models.CharField(_("fill run id"), max_length=26, null=True, blank=True)
    # NULL on a bench run: its row rides `input` and no ListRow exists
    # for it. NULLs are distinct under the open-run key, so an
    # account's bench runs never collide with each other.
    row_id = models.CharField(_("row id"), max_length=26, null=True, blank=True)
    # The list this task's row lives in, DENORMALIZED from the fill
    # (fill-backed) or the target sheet (autofill), so per-list
    # distribution and account scoping never need a join back to find it.
    # BLANK for a bench run, which points at no sheet by construction.
    list_id = models.CharField(_("list id"), max_length=26, blank=True, default="")
    # What the run was GIVEN, for a run that owns its input (the bench):
    # {"row": the hand-fed values, "config": the drafted AgentConfig},
    # bounded at the endpoint by the contract's bench caps. Empty for
    # every run whose row and config live elsewhere (a sheet row, an
    # agent). `result` beside it is what the run PRODUCED.
    input = models.JSONField(_("input"), default=dict)
    # Who started a run that owns its input (the bench's supersede rule
    # tells your own live run from a teammate's); "" for every run a
    # sheet or a fill produced, whose starter is the fill's.
    started_by = models.CharField(_("started by"), max_length=26, blank=True, default="")
    # The node this task is a run of, set on every lane at birth. A
    # column_agent node runs its agent's whole column set in ONE run (an
    # agent produces all its outputs together), so one task per
    # (row, node) is the grain.
    node_id = models.CharField(_("node id"), max_length=26)
    # The node's kind, DENORMALIZED so a lane filters without a join
    # (the flush claims webhook runs by kind; the reclaim returns a
    # stale webhook run to DEFERRED and an agent run to READY). No
    # default: a CharField stores "" when a writer forgets it, and the
    # check constraint below turns that into a failed insert.
    kind = models.CharField(_("kind"), max_length=NODE_KIND_MAX_LENGTH)
    # WHERE this task's row sits: the row's rank as it was when the run
    # was queued, so claims ordered by it march TOP TO BOTTOM down the
    # sheet the user is watching (a move after that reorders nothing
    # already queued; the next fill reads the new order). Blank for a
    # bench run, which has no sheet. The C collation, as the row's.
    rank = models.CharField(_("rank"), max_length=RANK_MAX_LENGTH, blank=True, default="", db_collation="C")
    status = models.CharField(_("status"), max_length=NODE_RUN_STATUS_MAX_LENGTH, default=NodeRunStatus.QUEUED)
    # Incremented AT CLAIM, not at completion, so a row that kills its
    # worker thread still exhausts across process restarts. Counting
    # completions instead bounds nothing a crash can reach.
    attempts = models.IntegerField(_("attempts"), default=0)
    # When a parked task becomes claimable again: real backoff, rather
    # than waiting out a lease the task never held.
    not_before = models.DateTimeField(_("not before"), null=True, blank=True)
    # Whether a park has ever counted this task into its fill's derived
    # TRANSIENT count. STORED, because both proxies for it are wrong
    # in opposite directions: `attempts` climbs at CLAIM, so a released
    # lease or a stale reclaim raises it with no park behind it, and
    # `not_before` is cleared by the next claim, so a task that parked
    # and then lost its worker reads as never parked. One is a gauge
    # that goes negative, the other one that never comes back down.
    # Set once, never cleared: it means counted, not currently waiting.
    parked = models.BooleanField(_("parked"), default=False)
    # The claiming consumer's id, stamped at claim: the terminal CAS
    # (settle / park) matches on it, so only the owner closes a task and a
    # reclaimed task's original consumer loses the CAS silently. Liveness
    # is last_state_change_at + PROCESSING_STALE_SECONDS, not a held lease.
    leased_by = models.CharField(_("leased by"), max_length=LEASED_BY_MAX_LENGTH, blank=True, default="")
    # The serialized CellRun the runtime returned, verbatim: cells the
    # model answered, the evidence it saw, each search and whether it
    # failed, and per output its confidence and stated reason. ONE
    # column because the runtime returns ONE object and a run's record is
    # one document; splitting it here
    # is what left the model's ANSWERS with no home and pushed them
    # into the assessment dict.
    #
    # This is what the run PRODUCED. What LANDED is the sheet row plus
    # its diagnoses, and the difference between them is the audit story
    # (a value write-if-blank refused, an answer the floor dropped).
    result = models.JSONField(_("result"), default=dict)
    # The state machine's timestamps. The per-state stamps are the
    # HISTORY (when the task entered each state, null until it does);
    # `last_state_change_at` is the UNIFIED cursor, bumped on EVERY
    # transition, so it is what "stuck in a state too long" reads. It is
    # deliberately not `updated_at`, which a result stash also bumps and
    # so would blur stuck-detection.
    queued_at = models.DateTimeField(_("queued at"), null=True, blank=True)
    processing_at = models.DateTimeField(_("processing at"), null=True, blank=True)
    settled_at = models.DateTimeField(_("settled at"), null=True, blank=True)
    last_state_change_at = models.DateTimeField(_("last state change at"), null=True, blank=True)

    class Meta:
        verbose_name = _("node run")
        verbose_name_plural = _("node runs")
        constraints = [
            # The idempotency key: enqueueing the same row twice is a
            # no-op. Also the row drawer's lookup. NULL fill_run_ids are
            # distinct in SQL, so this only binds fill-backed tasks; the
            # automatic path is deduped by its own key below.
            models.UniqueConstraint(fields=["fill_run_id", "row_id"], name="node_run_fill_row_uniq"),
            # The automatic path's idempotency: one OPEN run per (row,
            # node) (one run fills a node's whole column set), so
            # re-enqueueing a row's autofill or re-completing a row for
            # its webhook while a run is pending is a no-op. A settled
            # run is history: a row that completes again after its
            # webhook run sent gets a new run.
            models.UniqueConstraint(
                fields=["row_id", "node_id"],
                condition=models.Q(fill_run_id__isnull=True, status__in=NON_TERMINAL_NODE_RUN_STATES),
                name="node_run_open_uniq",
            ),
            models.CheckConstraint(condition=~models.Q(kind=""), name="node_run_kind_named"),
        ]
        indexes = [
            # The provisioner's READY pick, SPLIT by lane: a fill-backed
            # pick filters fill_run_id by equality, but the autofill
            # firehose filters it IS NULL, and IS NULL cannot give a btree
            # the ordering pathkey an equality does (it sorts the whole
            # READY set instead of stopping at the LIMIT). So each lane
            # gets a partial index holding only its rows. In both, status
            # leads (one equality opens it); the rank/id tail lets the
            # LIMIT stop early; not_before rides the leaf (INCLUDE) so a
            # parked task is rejected without a heap fetch, never a seek
            # key (below the ordering columns it cannot be one).
            #
            # Fill-backed: fill_run_id = :f seeks the fill's tasks. A fill
            # is single-list, so list_id earns no place here.
            models.Index(
                fields=["status", "fill_run_id", "rank", "id"],
                include=["not_before"],
                name="node_run_fill_idx",
                condition=models.Q(fill_run_id__isnull=False),
            ),
            # Autofill firehose: partial on the null-run rows, so status
            # leads straight into the (list_id, rank, id) order with no
            # IS NULL in the key. list_id sits BEFORE the sort columns, so a
            # per-list or set-sharded pick (list_id = ANY(...)) SEEKS its
            # lists rather than scanning. `kind` rides the leaf beside
            # not_before so the pick's lane filter stays index-only.
            models.Index(
                fields=["status", "list_id", "rank", "id"],
                include=["not_before", "kind"],
                name="node_run_autofill_idx",
                condition=models.Q(fill_run_id__isnull=True),
            ),
            # The flush's claim: a webhook node's DEFERRED runs due at or
            # before now, in (rank, id) order. Partial on the kind so
            # the agent lanes' rows never widen it.
            models.Index(
                fields=["node_id", "status", "not_before", "rank", "id"],
                name="node_run_webhook_due_idx",
                condition=models.Q(kind=WEBHOOK),
            ),
            # The rows page's cell word: the NEWEST run per (row, webhook
            # node) for one page of rows. The due index leads with the
            # node and would walk its whole history to answer for a page.
            models.Index(
                fields=["row_id", "node_id", "-id"],
                name="node_run_webhook_cell_idx",
                condition=models.Q(kind=WEBHOOK),
            ),
            # The reclaim scan's access path: find tasks stuck in a
            # non-terminal state too long, oldest first. PARTIAL on the
            # non-terminal states so it SHRINKS as tasks settle, holding
            # only the in-flight tail rather than the settled history.
            models.Index(
                fields=["status", "last_state_change_at"],
                name="node_run_reclaim_idx",
                condition=models.Q(status__in=NON_TERMINAL_NODE_RUN_STATES),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.fill_run_id}/{self.row_id} ({self.status})"
