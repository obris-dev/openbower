from django.db import models
from django.utils.translation import gettext_lazy as _

from auth_client.fields import EncryptedTextField
from openbower_kernel.models import BaseModel


class AppSession(BaseModel):
    """One browser login: the server-side record behind the session cookie.

    The cookie carries only a random opaque token; this row maps its hash
    to the user and holds that user's IdP OAuth tokens, so the browser
    never sees a token. There is no local user table: the identity fields
    are a cached projection of the IdP's /me (ULID char pointers, display
    email), not authority, and may lag it.

    A session dies by revocation (logout, or an upstream refresh failing),
    never by a local timer: lifetime is delegated to the IdP's rotating
    refresh token, so there is one authority for how long logins last.
    """

    # SHA-256 hex of the cookie token: lookups only ever need equality, so
    # the original is never stored (nothing in the db can be replayed as a
    # cookie). Contrast the OAuth tokens below, which must be recoverable.
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True)

    user_id = models.CharField(_("user id"), max_length=26, db_index=True)
    account_id = models.CharField(_("account id"), max_length=26)
    email = models.EmailField(_("email"))

    # Presented upstream verbatim, so unlike token_hash they cannot be
    # hashed; encrypted at rest instead.
    access_token = EncryptedTextField(_("access token"))
    refresh_token = EncryptedTextField(_("refresh token"))
    # Enables proactive refresh (rotate just before expiry) instead of
    # discovering expiry by an upstream 401.
    access_expires_at = models.DateTimeField(_("access token expires at"))

    # Set means dead. A timestamp, not a boolean: null/not-null already
    # answers "is it live" and this also records when it ended.
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    class Meta:
        verbose_name = _("app session")
        verbose_name_plural = _("app sessions")

    def __str__(self) -> str:
        return f"{self.email} ({self.id})"
