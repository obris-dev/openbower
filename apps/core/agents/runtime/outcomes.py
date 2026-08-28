"""What a tool call reports back: the runtime's record of one call,
typed by the tool's OWN status vocabulary.

`ToolOutcome` is the base every tool shares (which tool, what its
door said, which provider served it, how many tries the seam made).
A tool specializes it with what it returned (`SearchOutcome` adds the
query and the hits) and PINS the status enum it draws from, so a
status of the wrong vocabulary is a type error at construction and a
bare string never gets in at runtime. The statuses share a base
(agents.constants.ToolStatus) that the sheet reasons about; a tool
may add modes of its own without touching the base, another tool, or
the cell vocabulary. The outcomes ride the dependency channel out of
the run as DATA, never as exceptions: the fill operation reads them
and sets the task's statuses from them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from openbower_schema.agents import TestSearch

from ..constants import AgentTool, SearchStatus, ToolStatus
from ..search import SearchHit


@dataclass(frozen=True, slots=True)
class ToolOutcome[StatusT: StrEnum]:
    tool: AgentTool
    status: StatusT
    provider: str
    attempts: int

    def __post_init__(self) -> None:
        # The type checker holds the vocabulary; this holds the shape
        # at runtime, where a plain string would otherwise pass.
        if not isinstance(self.status, StrEnum):
            raise TypeError(f"{type(self).__name__}.status must be a status enum member, not {self.status!r}")

    @property
    def failed(self) -> bool:
        return self.status != ToolStatus.OPEN

    @property
    def base(self) -> ToolStatus:
        """The base code the sheet reasons about (a tool-specific mode
        declares its base on its enum)."""
        return getattr(self.status, "base", ToolStatus(self.status.value))


@dataclass(frozen=True, slots=True)
class SearchOutcome(ToolOutcome[SearchStatus]):
    """One search query's outcome: `hits` is what the door returned on
    an `open` status (empty is an honest zero-hit answer), nothing on
    any other."""

    query: str
    hits: list[SearchHit]

    def wire(self) -> TestSearch:
        """The ONE constructor for the stored and served diagnosis, so
        the bench's writer and the fill worker's cannot drift."""
        return TestSearch(
            query=self.query,
            hits=len(self.hits),
            status=self.status,
            provider=self.provider,
            attempts=self.attempts,
            tool=self.tool,
        )
