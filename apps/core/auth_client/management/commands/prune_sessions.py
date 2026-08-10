"""Delete AppSession rows that are dead weight.

Two populations: rows revoked long enough ago (logout, or an upstream
refresh definitively rejected), and LIVE rows older than the cookie's
fixed max-age, which no browser can present anymore but which would
otherwise warehouse encrypted refresh tokens forever. Run on a schedule
(cron / periodic job).
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
        revoked = AppSessionService.Global.prune_revoked(older_than_days=days)
        unreachable = AppSessionService.Global.prune_unreachable()
        self.stdout.write(
            f"Pruned {revoked} session row(s) revoked more than {days} day(s) ago"
            f" and {unreachable} live row(s) past the cookie max-age"
        )
