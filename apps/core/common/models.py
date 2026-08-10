from django.db import models
from django.utils.translation import gettext_lazy as _

from .fields import ULIDField


class BaseModel(models.Model):
    """Abstract base: ULID primary key + created_at/updated_at.

    ULIDs are time-sortable: order by `-id`, paginate with a ULID cursor.
    Cross-model refs are `CharField(max_length=26)` pointers named
    `<thing>_id`, never ForeignKeys; the owning service resolves them.
    """

    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True
