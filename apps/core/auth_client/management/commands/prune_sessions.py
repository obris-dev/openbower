"""Delete AppSession rows revoked long enough ago that they are dead weight.

A session is revoked on logout or when an upstream refresh is definitively
rejected; the row (and its encrypted tokens) then serves no purpose. Run on
a schedule (cron / periodic job) so revoked sessions don't accumulate
forever. Live (non-revoked) rows are never touched here; their lifetime is
governed by the IdP's rotating refresh token.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from auth_client.services import AppSessionService


class Command(BaseCommand):
    help = "Delete AppSession rows revoked more than --days days ago."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=settings.APP_SESSION_PRUNE_DAYS,
            help="Age (days since revocation) past which a revoked session is deleted.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        days = options["days"]
        deleted = AppSessionService.Global.prune_revoked(older_than_days=days)
        self.stdout.write(f"Pruned {deleted} session row(s) revoked more than {days} day(s) ago")
