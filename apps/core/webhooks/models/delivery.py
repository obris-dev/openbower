from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import (
    STATUS_MAX_LENGTH,
    TYPE_MAX_LENGTH,
    WEBHOOK_ERROR_MAX_LENGTH,
    WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH,
)


class WebhookDelivery(AccountScopedModel):
    """One attempt to POST to a destination, whatever it answered: the
    log, and by its newest row per destination, the health.

    The row's ULID is the `webhook-id` header the request carried, so a
    receiver's log and this one meet on one value. One row per attempt.
    `type` and `test` are the envelope's own two facts (what the data
    was, and whether a Test button sent it), recorded as sent.

    `type` and `status` are bare CharFields over the constants' enums
    (choices= would make every added member a migration for no DB-side
    enforcement). `error` is a sentence for the user, never an
    exception name. `response_excerpt` is kept ONLY when the receiver
    did not answer 2xx, and only after every configured header value
    and the signature are masked out of it: an echo receiver returns
    the request, and a stored echo would defeat the write-only headers.

    Pruned by age (the prune_webhook_deliveries cron); deleted with its
    destination by the service."""

    destination_id = models.CharField(_("destination id"), max_length=26)
    type = models.CharField(_("type"), max_length=TYPE_MAX_LENGTH)
    test = models.BooleanField(_("test"), default=False)
    status = models.CharField(_("status"), max_length=STATUS_MAX_LENGTH)
    http_status = models.IntegerField(_("http status"), null=True, blank=True)
    duration_ms = models.IntegerField(_("duration ms"), default=0)
    error = models.CharField(_("error"), max_length=WEBHOOK_ERROR_MAX_LENGTH, blank=True, default="")
    response_excerpt = models.CharField(
        _("response excerpt"), max_length=WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH, blank=True, default=""
    )

    class Meta:
        verbose_name = _("webhook delivery")
        verbose_name_plural = _("webhook deliveries")
        indexes = [
            # The keyset page by -id, the newest-per-destination read,
            # and the owner's delete.
            models.Index(fields=["destination_id", "id"], name="webhook_delivery_dest_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.type} {self.status} -> {self.destination_id} ({self.id})"
