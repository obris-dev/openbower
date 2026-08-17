"""Wire-format helpers for the data service's cursors."""

from __future__ import annotations


def run_cursor(run_id: str, after_rank: int = 0) -> str:
    """The run-results cursor ("<run id>:<last rank>"). Owned HERE so no
    caller forges the shape inline."""
    return f"{run_id}:{after_rank}"
