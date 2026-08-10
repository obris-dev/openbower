from django.db import models
from django.utils.translation import gettext_lazy as _

from auth_client.fields import EncryptedTextField
from common.models import BaseModel


class AppSession(BaseModel):
    """One browser login: the server-side record behind the `bwr_session`
    cookie.

    The cookie carries only a random opaque token; this row maps its hash to
    the user and holds that user's IdP OAuth tokens (the browser never sees
    them). `user_id` / `account_id` are the CLOUD-issued ULIDs from the IdP:
    the cloud mints identities, the app borrows them, and the identity
    fields here are a cached projection of the IdP's /me, not authority.

    A session dies by revocation (logout, or an upstream refresh failing),
    not by a timer: `revoked_at` set means gone. Lifetime is governed by the
    IdP's rotating refresh token.
    """

    # SHA-256 hex of the cookie token. Only the hash is stored, so a leaked
    # db dump can't be replayed as cookies.
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True)

    user_id = models.CharField(_("user id"), max_length=26, db_index=True)
    account_id = models.CharField(_("account id"), max_length=26)
    email = models.EmailField(_("email"))

    # Encrypted at rest: these are replayable IdP credentials, so a leaked
    # dump must not surface live tokens (unlike token_hash, they can't be
    # hashed because the app has to present them upstream verbatim).
    access_token = EncryptedTextField(_("access token"))
    refresh_token = EncryptedTextField(_("refresh token"))
    access_expires_at = models.DateTimeField(_("access token expires at"))

    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    class Meta:
        verbose_name = _("app session")
        verbose_name_plural = _("app sessions")

    def __str__(self) -> str:
        return f"{self.email} ({self.id})"
