"""Auth wire shapes shared by the backend and the web client."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    """The authenticated user the app's /v1/auth/me returns (and the identity
    the IdP resolves). Char-pointer ULIDs, so plain strings on the wire."""

    id: str = Field(description="The user's cloud-issued ULID.")
    email: str = Field(description="The user's email address.")
    account_id: str = Field(description="The user's primary account ULID.")


class PatSummary(BaseModel):
    """One personal access token as the owner's list shows it: the
    raw is unrecoverable by design, so display leans on name and
    last_four."""

    id: str
    name: str
    last_four: str = Field(description="The raw token's last four characters, display only.")
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = None


class PatMinted(BaseModel):
    """The mint receipt: the ONE appearance of the raw token. It is
    shown here and never again; only its hash is stored."""

    token: str = Field(description="The raw bearer token (obw_ prefixed). Shown once, unrecoverable after.")
    pat: PatSummary


class PatList(BaseModel):
    tokens: list[PatSummary]
