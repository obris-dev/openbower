"""The per-call harness: the one catch site between a tool's function
and the framework. `wrap(spec)` is what build_tools hands to
pydantic-ai, and it owns the call's boundary in both directions: a
closed tool is refused BEFORE the function runs (no spend past a
closer), and everything the function raises converts to data (the
status folded onto deps, a note returned to the model), because an
exception crossing into the framework would abort the whole run and
one tool's failure must never be fatal-for-the-run.

The folding is split by who KNOWS: a tool records "open" itself when a
call truly served (only it can tell a served call from a no-op return
like an empty query, and the served mark exempts a tool from blank
blame); the harness folds every FAILURE from the raise, so a tool
author writes `raise ToolError(code)` and nothing else."""

from __future__ import annotations

import functools
import logging

from pydantic_ai import RunContext

from ..constants import ToolStatus
from ..runtime.deps import CellDeps
from .base import ToolError, ToolSpec, closed_note, result_json

logger = logging.getLogger(__name__)

# The model-facing note for a failure whose tool authored none: names
# no cause (the status carries that) and leaves the next query the
# model's own decision, like every note in the base vocabulary.
NOTE_CALL_FAILED = "the call failed; the records already gathered are all you have for this query"


def wrap(spec: ToolSpec):
    """The callable the framework drives for one registered tool.
    functools.wraps is LOAD-BEARING: pydantic-ai reads the function's
    name, docstring, and signature to build the tool schema the model
    sees, so the wrapper must be transparent."""

    @functools.wraps(spec.function)
    def call(ctx: RunContext[CellDeps], *args, **kwargs) -> str:
        deps = ctx.deps
        if not deps.tool_open(spec.name, spec.closers):
            return closed_note(spec.name, deps.tool_status[spec.name])
        try:
            return spec.function(ctx, *args, **kwargs)
        except ToolError as failure:
            # The authored failure: fold the code (the spec's one map
            # gives it its mode downstream), close the tool if it is a
            # closer, and hand the model the note.
            logger.warning("tool %s failed (%s): %s", spec.name, failure.code, failure.__cause__ or failure.note or "")
            deps.record_tool_status(spec.name, failure.code, spec.closers)
            if failure.code in spec.closers:
                return closed_note(spec.name, failure.code)
            return result_json([], failure.note or NOTE_CALL_FAILED)
        except Exception:
            # A tool bug is a programming error, but it must not abort
            # the run (the other tools and the verdict still have
            # value). Folded as the tool's own error code,
            # unconditionally (registration requires every tool to
            # declare one); the traceback is the log's, never the
            # model's or the user's.
            logger.exception("tool %s crashed", spec.name)
            deps.record_tool_status(spec.name, ToolStatus.ERROR, spec.closers)
            return result_json([], NOTE_CALL_FAILED)

    return call
