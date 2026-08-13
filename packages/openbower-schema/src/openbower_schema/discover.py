"""Discover wire shapes shared by the backend and the web client.

The shapes the data service returns for look-alike queries; the app's
discover proxy validates upstream responses against them and the web
validates the proxy's responses via the codegen'd zod versions, so all
three surfaces hold one definition.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Company(BaseModel):
    """One company from the central universe (seeded from the free PDL
    Company Dataset, CC BY 4.0). Char-pointer ULID id."""

    id: str = Field(description="The company's ULID in the universe.")
    domain: str = Field(description="Canonical lowercased bare domain.")
    name: str = Field(description="Company display name.")
    industry: str = Field(description="Industry label; empty when unknown.")
    locality: str = Field(description="City/locality; empty when unknown.")
    region: str = Field(description="Region/state; empty when unknown.")
    country: str = Field(description="Country; empty when unknown.")
    linkedin_url: str = Field(description="LinkedIn company URL; empty when unknown.")
    size_band: str = Field(description="Coarse employee band, e.g. 1-10; empty when unknown.")
    founded_year: int | None = Field(default=None, description="Founding year when known.")
    source: str = Field(description="Provenance of the row, e.g. pdl_free.")
    snapshot_date: str | None = Field(default=None, description="ISO date the row's data was current.")


class LookalikeItem(BaseModel):
    """One ranked look-alike candidate."""

    company: Company
    score: float = Field(description="Similarity score in [0, 1].")
    rank: int = Field(description="1-based rank in this result set.")
    description: str = Field(
        default="",
        description="Site-derived blurb (homepage meta description or title); empty until the domain has a fetched snippet.",
    )
    group: str = Field(
        default="",
        description="Label of the seed group that surfaced this candidate; empty for single-group runs.",
    )


class LookalikeGroup(BaseModel):
    """One seed group of a run: heterogeneous cohorts split into distinct
    groups, each searched by its own centroid and labeled."""

    label: str = Field(default="", description="Human label; empty for the single homogeneous group.")
    seed_domains: list[str] = Field(default=[])
    size: int = Field(default=0, description="Results this group contributed to the run.")
    elbow_rank: int | None = Field(
        default=None,
        description="This group's own boundary: the knee of ITS score decay (null when too short/flat).",
    )


# A CLOSED union on purpose: codegen emits it as a zod enum, so an
# unknown or typo status is a parse error on the web (surfaced as an
# error state), never a value that polls forever or lands in a transient
# retry bucket.
RunStatus = Literal["pending", "running", "complete", "failed", "canceled"]


class LookalikeListResponse(BaseModel):
    """The look-alike envelope (data service and app proxy alike), for both
    the query POST and the run-status poll.

    `status` drives the async lifecycle: "complete" carries the page in
    `items`; "pending"/"running" mean poll the run (HTTP 202, empty items);
    "failed" carries `detail`; "canceled" is terminal by request (a fresh
    query revives the run). The engine computes in a worker, so a cold
    cohort answers pending first and is polled by `run_id`; a cached cohort
    answers complete inline.
    """

    engine: str = Field(description="Which engine ranked these, e.g. embedding_v1.")
    status: RunStatus = Field(
        default="complete",
        description="Run lifecycle. Terminal: complete | failed | canceled; keep polling: pending | running.",
    )
    run_id: str | None = Field(
        default=None, description="The run to poll; null only when no run exists (e.g. an empty cohort)."
    )
    items: list[LookalikeItem]
    next_cursor: str | None = Field(default=None, description="Opaque cursor for the next page.")
    result_count: int | None = Field(
        default=None,
        description="TOTAL results in the completed run (the full lead-list size, independent of page size).",
    )
    groups: list[LookalikeGroup] = Field(
        default=[],
        description="The run's seed-group breakdown; single unlabeled entry for homogeneous cohorts.",
    )
    outlier_domains: list[str] = Field(
        default=[],
        description="Seeds set aside by clustering as misfits (excluded from generation, shown to the user).",
    )
    unresolved_domains: list[str] = Field(
        default=[],
        description="Inline seed domains that are not in the universe.",
    )
    detail: str | None = Field(default=None, description="Failure detail when status is failed.")
