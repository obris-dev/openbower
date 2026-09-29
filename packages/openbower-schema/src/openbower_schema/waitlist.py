"""Wire contract for the hosted waitlist: the one public endpoint the
marketing site posts to before any account exists. The server answers
the same shape whether the address was new or already on the list, so
the endpoint never says who is on it."""

from __future__ import annotations

from pydantic import BaseModel

# Bound on where a signup came from (a form id), binary by house rule.
WAITLIST_SOURCE_MAX_LENGTH = 64


class WaitlistSignupWire(BaseModel):
    """What a signup post answers: the address as stored (trimmed,
    lowercased), and nothing that distinguishes new from known."""

    email: str
