"""Auth wire shapes shared by the backend and the web client: the signed-in
user, and the machine API keys the app proxies to the identity service."""

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


# The bound the identity service enforces on create; the web's input stops
# at it so a name is never refused for length. Not a constraint on the read
# path: a key the service lists is valid whatever its name's length.
API_KEY_NAME_MAX_LENGTH = 80
# The roster is unpaged, so a bound must exist: the identity service
# refuses a mint past this many live keys per user, and lists live keys
# only. Read as an x-constant.
MAX_API_KEYS = 50


class ApiKeyWire(BaseModel):
    """One machine API key as its owner's list shows it. The raw token is
    unrecoverable by design, so a key is known by its name and the last
    four characters of the token."""

    id: str = Field(description="The key's ULID; a revoke names it.")
    name: str = Field(description="The owner's label for the script that uses the key.")
    last_four: str = Field(description="The token's last four characters, for recognition only.")
    created_at: str = Field(description="ISO 8601, when the key was created.")
    revoked_at: str | None = Field(default=None, description="When the key stopped working; null while it works.")


class ApiKeysList(BaseModel):
    """The owner's live keys, newest first; a revoked key is absent.
    Unpaged: MAX_API_KEYS bounds it."""

    keys: list[ApiKeyWire]


class ApiKeyMinted(BaseModel):
    """The mint response: the ONE time the token is on the wire. No later
    read returns it; a lost token is replaced by minting another."""

    token: str = Field(description="The bearer value a script sends as Authorization: Bearer; shown this once.")
    key: ApiKeyWire
