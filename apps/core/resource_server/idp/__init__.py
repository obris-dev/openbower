"""The identity provider as an upstream: urls + transport (wire calls
with failures normalized). The DRF authentication layer consumes this and
owns caching and claims semantics."""

from .transport import verify_token

__all__ = ["verify_token"]
