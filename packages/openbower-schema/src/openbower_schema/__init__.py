"""OpenBower shared schema.

Pydantic-first contract: every wire shape the web consumes is defined here
and codegen'd to zod for the web workspace, so both sides validate against
one definition. Shapes land with the phase that serves them.
"""

from .auth import AuthUser

__all__ = ["AuthUser"]
