"""OpenBower shared schema.

Pydantic-first contract: every wire shape the web consumes is defined here
and codegen'd to zod for the web workspace, so both sides validate against
one definition.
"""

from .auth import AuthUser
from .discover import (
    Company,
    LookalikeGroup,
    LookalikeItem,
    LookalikeListResponse,
)

__all__ = [
    "AuthUser",
    "Company",
    "LookalikeGroup",
    "LookalikeItem",
    "LookalikeListResponse",
]
