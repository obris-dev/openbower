"""The hosted waitlist: one idempotent signup.

Account-less on purpose: a signup is captured before any account
exists, so the service carries no tenant and its one operation is a
static method. Nothing is sent from here: the signup itself is the
acknowledgement the form shows."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import WaitlistSignup


@dataclass(frozen=True)
class Signup:
    signup: WaitlistSignup
    created: bool


class WaitlistService:
    @staticmethod
    def signup(*, email: str, source: str = "") -> Signup:
        """Add an address to the list; an address already on it is left
        as it was, its original source included."""
        normalized = email.strip().lower()
        signup, created = WaitlistSignup.objects.get_or_create(email=normalized, defaults={"source": source})
        return Signup(signup=signup, created=created)
