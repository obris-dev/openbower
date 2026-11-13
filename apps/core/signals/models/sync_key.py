from django.db import models
from django.utils.translation import gettext_lazy as _

from auth_client.fields import EncryptedTextField
from openbower_kernel.models import UserScopedModel


class SignalsSyncKey(UserScopedModel):
    """The key the account's signals feeds read with when no person is
    present: an API key with signals:read, minted through a person's
    session (user_id is who minted it).

    `token` is encrypted at rest and recoverable, not hashed, because the
    feed replays it on every call; ciphertext is non-deterministic, so it
    is never filtered on. `key_id` is the identity service's id for it, so
    a reconnect can revoke it. `rejected_at` is set when the service
    refuses the key (revoked, expired): the account's feeds pause until a
    person reconnects, which mints a fresh key over this row."""

    key_id = models.CharField(_("key id"), max_length=26)
    token = EncryptedTextField(_("token"))
    rejected_at = models.DateTimeField(_("rejected at"), null=True, blank=True)

    class Meta:
        verbose_name = _("signals sync key")
        verbose_name_plural = _("signals sync keys")
        constraints = [models.UniqueConstraint(fields=["account_id"], name="signals_sync_key_account_uniq")]

    def __str__(self) -> str:
        return f"sync key {self.key_id} ({self.account_id})"
