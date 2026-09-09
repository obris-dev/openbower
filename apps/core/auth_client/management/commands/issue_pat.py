"""Mint a personal access token from the command line: the headless
cold start for a box with no browser. Core owns no user table, so the
owner's identity comes from a prior login (their AppSession row's
cached projection of the IdP identity); a user who has never logged in
here cannot be minted for.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ...models import AppSession
from ...services.pats import PatService


class Command(BaseCommand):
    help = "Mint a personal access token for a user who has logged in at least once."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--expires-in-days", type=int, default=0)

    def handle(self, *args, **options) -> None:
        session = AppSession.objects.filter(email=options["email"]).order_by("-id").first()
        if session is None:
            raise CommandError(f"no login on record for {options['email']!r}; the owner must sign in once first")
        _, raw = PatService(account_id=session.account_id, user_id=session.user_id).mint(
            email=session.email, name=options["name"], expires_in_days=options["expires_in_days"]
        )
        # The raw token, printed once: this is the only time it exists
        # outside its own hash.
        self.stdout.write(raw)
