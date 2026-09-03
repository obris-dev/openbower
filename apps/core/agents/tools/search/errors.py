"""The search family's FAILURE DECLARATIONS: one class per failure
code, each carrying its code and its mode as the single source (the
spec's failure_modes view derives from these, and the machinery raises
them typed), so a failure exists exactly once: as the exception a tool
raises."""

from __future__ import annotations

from ...constants import SearchStatus
from ..base import FailureMode, ToolError, family_errors


class SearchToolError(ToolError):
    """The FAMILY base: every search-tool failure inherits it, so the
    hierarchy reads contract (ToolError) -> family -> member, and the
    family payload rides here: which provider failed and how many
    tries the provider harness made before deciding (the audit record's
    facts, raised from the decision point). The harness still catches
    only the contract base; this level exists for the payload and for
    legibility, never for a second catch site."""

    def __init__(self, note: str = "", *, provider: str = "", attempts: int = 1) -> None:
        super().__init__(note)
        self.provider = provider
        self.attempts = attempts


class SearchNotConfigured(SearchToolError):
    """The provider has no credentials on this deploy: fatal, closed
    for the run, settled on the sheet until it is set up."""

    code = SearchStatus.NOT_CONFIGURED
    mode = FailureMode.FATAL


class SearchRateLimited(SearchToolError):
    """The provider said slow down (the seam already retried with
    backoff): believe it, closed for this run, worth re-buying later."""

    code = SearchStatus.RATE_LIMITED
    mode = FailureMode.TRANSIENT


class SearchUnreachable(SearchToolError):
    """One call could not reach the provider: a per-query hazard, the
    tool stays callable."""

    code = SearchStatus.UNREACHABLE
    mode = FailureMode.HAZARD


class SearchErrored(SearchToolError):
    """The provider answered wrongly for this query (bad payload,
    drained balance, unexpected status): a per-query hazard."""

    code = SearchStatus.ERROR
    mode = FailureMode.HAZARD


# The family's declared errors, DERIVED from inheritance (subclassing
# SearchToolError above IS the membership; nothing to remember here).
SEARCH_ERRORS = family_errors(SearchToolError)
