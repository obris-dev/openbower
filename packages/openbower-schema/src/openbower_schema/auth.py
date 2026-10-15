"""Auth wire shapes shared by the backend and the web client."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    """The authenticated user the app's /v1/auth/me returns (and the identity
    the IdP resolves). Char-pointer ULIDs, so plain strings on the wire."""

    id: str = Field(description="The user's cloud-issued ULID.")
    email: str = Field(description="The user's email address.")
    account_id: str = Field(description="The user's primary account ULID.")
    hosted_access: bool = Field(
        default=False,
        description="Whether the account may sign into the hosted app. Checked by the hosted "
        "deployment at login only; a self-hosted install ignores it. Defaulted so an identity "
        "service that predates the field still validates.",
    )
