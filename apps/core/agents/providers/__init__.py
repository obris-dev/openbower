from .anthropic_compatible import TIMEOUT_EXCEPTION as _ANTHROPIC_TIMEOUT
from .openai_compatible import TIMEOUT_EXCEPTION as _OPENAI_TIMEOUT
from .registry import ModelUnavailable, catalog_entries, model_for, source_config

# Every type a model call can raise for running out of time, gathered
# from the doors so a new door declares its own beside itself rather
# than leaving this list to be discovered later. Only the SDK types:
# no door calls the transport during a completion, so httpx's own
# timeout has no writer here, and carrying it would be a member whose
# comment names a path that does not exist.
MODEL_TIMEOUT_EXCEPTIONS: tuple[type[Exception], ...] = (_OPENAI_TIMEOUT, _ANTHROPIC_TIMEOUT)

__all__ = [
    "MODEL_TIMEOUT_EXCEPTIONS",
    "ModelUnavailable",
    "catalog_entries",
    "model_for",
    "source_config",
]
