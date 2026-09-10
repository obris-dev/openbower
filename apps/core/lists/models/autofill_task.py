from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel


class AutofillTask(UserScopedModel):
    """The autofill queue: one row per pushed sheet row that is owed an
    automatic fill of its AI columns, enqueued in the same transaction
    that appends the row so a row can never land without its work
    queued.

    The queue is the work list, not a derivation of state: a task means
    "this row was just added, so it is empty, so fill it", which is why
    it carries no per-column or due-ness detail. The worker resolves the
    sheet's fillable columns at processing time and deletes the task
    when done. Enqueue is allowed to be liberal (a duplicate is cheap)
    because processing is idempotent; there is deliberately no claim
    state on the row (in-flight coordination is a cache lock the worker
    holds, not a column here that a crash could strand).

    UserScopedModel: `account_id` is the tenancy boundary the worker
    resolves the list under, and `user_id` is the pushing key's owner,
    the attribution a run acts as. Both are DENORMALIZED onto the task
    so the worker (a trusted process serving every account) has what it
    needs without a parent to read them from."""

    list_id = models.CharField(_("list id"), max_length=26)
    row_id = models.CharField(_("row id"), max_length=26)

    class Meta:
        verbose_name = _("autofill task")
        verbose_name_plural = _("autofill tasks")
        # The worker drains oldest-first by the ULID pk, which is already
        # indexed; no read here filters by account or list, so no member
        # earns a secondary index.

    def __str__(self) -> str:
        return f"autofill row {self.row_id} (list {self.list_id})"
