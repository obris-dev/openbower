"""Waitlist vocabulary: the person's lifecycle on the list. A StrEnum
with `choices` OFF the column, so a new member never forces a
migration."""

from enum import StrEnum

from openbower_schema.waitlist import WAITLIST_SOURCE_MAX_LENGTH as WAITLIST_SOURCE_MAX_LENGTH


class WaitlistState(StrEnum):
    """The person's standing on the list: invite-eligibility, never
    email delivery. PENDING -> INVITED is a deliberate rollout gesture,
    never inferred from anything sent."""

    PENDING = "pending"
    INVITED = "invited"
    UNSUBSCRIBED = "unsubscribed"


# The IP throttle's cache scope for the one public endpoint.
WAITLIST_THROTTLE_SCOPE = "waitlist"
