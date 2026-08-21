from django.db import models
from django.utils.translation import gettext_lazy as _

from .fields import ULIDField


class BaseModel(models.Model):
    """Abstract base: ULID primary key + created_at/updated_at.

    ULIDs are time-sortable: order by `-id`, paginate with a ULID cursor.
    Cross-model refs are `CharField(max_length=26)` pointers named
    `<thing>_id`, never ForeignKeys; the owning service resolves them.

    This base sets NO `Meta.ordering` (an abstract default would force a
    sort on every query, including ones that do not want it). Newest-first
    ordering is the concrete model's responsibility: set
    `ordering = ["-id"]` (or order per query), since the ULID cursor
    pagination assumes a descending-id page.
    """

    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class AccountScopedModel(BaseModel):
    """Abstract base for tenant rows: `account_id` is the TENANCY
    BOUNDARY (every read and write filters on it, enforced by the
    owning service, never by this base). FIELDS ONLY: inheriting models
    declare the indexes their own read patterns earn."""

    account_id = models.CharField(_("account id"), max_length=26)

    class Meta:
        abstract = True


class UserScopedModel(AccountScopedModel):
    """AccountScopedModel plus `user_id`, which is ATTRIBUTION (who
    created the row), never an access filter: a service "fixed" to
    filter by user would break account-shared visibility."""

    user_id = models.CharField(_("user id"), max_length=26)

    class Meta:
        abstract = True
