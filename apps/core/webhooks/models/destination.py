from django.db import models
from django.utils.translation import gettext_lazy as _

from auth_client.fields import EncryptedTextField
from openbower_kernel.models import UserScopedModel

from ..constants import LABEL_MAX_LENGTH, WEBHOOK_URL_MAX_LENGTH


class WebhookDestination(UserScopedModel):
    """A place deliveries go: a URL with the headers it needs and the
    secret deliveries are signed with, configured once for the account
    and reused by every sheet.

    `headers` (a JSON object as text) and `signing_secret` are
    encrypted at rest and never returned; the wire shows header names
    only. The secret is recoverable rather than hashed because the
    server replays it to sign every delivery. Ciphertext is
    non-deterministic, so neither column is ever filtered on.

    No health columns: the newest WebhookDelivery IS the health, so two
    concurrent deliveries cannot overwrite each other's outcome.

    No cascades: WebhookDestinationService.delete removes the row and
    then its deliveries, in one transaction."""

    label = models.CharField(_("label"), max_length=LABEL_MAX_LENGTH)
    url = models.CharField(_("url"), max_length=WEBHOOK_URL_MAX_LENGTH)
    headers = EncryptedTextField(_("headers"), blank=True, default="{}")
    signing_secret = EncryptedTextField(_("signing secret"))
    # Gates the automatic lane (a flush never posts to a paused
    # destination); a test delivery is an explicit gesture and ignores it.
    enabled = models.BooleanField(_("enabled"), default=True)

    class Meta:
        verbose_name = _("webhook destination")
        verbose_name_plural = _("webhook destinations")
        indexes = [
            # The roster and the cap's count, both by account.
            models.Index(fields=["account_id", "id"], name="webhook_dest_account_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.id})"
