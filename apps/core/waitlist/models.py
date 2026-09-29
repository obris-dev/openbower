from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import BaseModel

from .constants import WAITLIST_SOURCE_MAX_LENGTH, WaitlistState


class WaitlistSignup(BaseModel):
    """A hosted-waitlist signup from the public marketing site.

    Pre-account, so no tenancy columns: nothing exists to scope it to.
    `email` is unique, which is what makes the public post idempotent.
    `state` is a bare CharField validated app-side (`waitlist.constants`),
    so a new member is never a migration.

    No cascades to worry about: the row owns nothing."""

    email = models.EmailField(_("email"), unique=True)
    state = models.CharField(_("state"), max_length=16, default=WaitlistState.PENDING.value)
    # Which form converted (the marketing form's id), never a URL.
    source = models.CharField(_("source"), max_length=WAITLIST_SOURCE_MAX_LENGTH, blank=True, default="")
    invited_at = models.DateTimeField(_("invited at"), null=True, blank=True)

    class Meta:
        verbose_name = _("waitlist signup")
        verbose_name_plural = _("waitlist signups")
        indexes = [
            # The rollout's read: who is still pending, oldest first.
            models.Index(fields=["state", "id"], name="waitlist_state_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.state})"
