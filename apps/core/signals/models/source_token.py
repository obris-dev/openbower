from django.db import models
from django.utils.translation import gettext_lazy as _

from auth_client.fields import EncryptedTextField
from openbower_kernel.models import UserScopedModel


class SourceToken(UserScopedModel):
    """The secret in a custom signal's webhook URL
    (`/v1/signals/{id}/ingest/{token}`): a push carrying it needs no session
    and no API key. It is kept twice: `token_hash`, which a push is looked up
    by (one indexed read), and `token`, encrypted at rest and never filtered
    on, which a signed-in person reads to copy the URL any time; a leaked
    one is replaced by rotating. One live token per source: a rotate
    revokes the last before minting the next, and keeps only the newest
    WEBHOOK_TOKENS_REVOKED_KEPT revoked. user_id is who minted it.

    `sheet_id` beside `source_id` lets a push resolve its sheet and the
    token in one read, by the id in its path."""

    sheet_id = models.CharField(_("sheet id"), max_length=26)
    source_id = models.CharField(_("source id"), max_length=26)
    token_hash = models.CharField(_("token hash"), max_length=64)
    token = EncryptedTextField(_("token"))
    last4 = models.CharField(_("last four"), max_length=4)
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    class Meta:
        verbose_name = _("source token")
        verbose_name_plural = _("source tokens")
        constraints = [
            # Every push's lookup (a constraint, not unique=True, which would
            # add a LIKE index nothing reads).
            models.UniqueConstraint(fields=["token_hash"], name="signals_source_token_hash_uniq"),
            models.UniqueConstraint(
                fields=["source_id"],
                condition=models.Q(revoked_at__isnull=True),
                name="signals_source_token_live_uniq",
            ),
        ]
        # A signal's tokens: its delete, and a rotate's prune of the revoked.
        indexes = [models.Index(fields=["account_id", "sheet_id"], name="signals_source_token_sheet_idx")]

    def __str__(self) -> str:
        return f"token ...{self.last4} (source {self.source_id})"
