from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel


class PersonalAccessToken(UserScopedModel):
    """One long-lived machine credential: the row behind a `Bearer
    obw_...` token a webhook, script, or producer presents.

    Like the session cookie, the raw token is shown once at mint and
    only its hash is stored (lookups only ever need equality, so
    nothing in the db can be replayed as a credential). Unlike a
    session, a PAT has no upstream OAuth pair behind it: it is a
    core-local product credential, and revoking it here is the whole
    story.

    Dies by revocation or its own expiry, whichever comes first; a
    null expiry means the owner chose no timer.
    """

    # The owner's email, captured at mint: a cached projection of the
    # IdP identity (the AppSession pattern), so the principal a PAT
    # authenticates is a complete identity, not a session-shaped
    # partial. Display only, and frozen at mint, so it may lag /me.
    email = models.EmailField(_("email"))
    name = models.CharField(_("name"), max_length=80)
    # SHA-256 hex of the raw token; unique doubles as the lookup index.
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True)
    # Display only, for "which key is this" in a token list; the raw
    # is unrecoverable by design.
    last_four = models.CharField(_("last four"), max_length=4)

    last_used_at = models.DateTimeField(_("last used at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    # Set means dead. A timestamp, not a boolean: null/not-null already
    # answers "is it live" and this also records when it ended.
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    class Meta:
        verbose_name = _("personal access token")
        verbose_name_plural = _("personal access tokens")

    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"{self.name} (...{self.last_four})"
