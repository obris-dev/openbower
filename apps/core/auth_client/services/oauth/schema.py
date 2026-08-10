"""Wire shapes this client parses. App-local: these are OUR parse of the
standard OAuth responses, not domain contracts we own (the shared identity
contract lives in openbower-schema)."""

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """The IdP token-endpoint shape (code exchange + refresh). Pydantic
    does the validation (required fields, expires_in numeric)
    declaratively."""

    access_token: str
    refresh_token: str
    expires_in: int
