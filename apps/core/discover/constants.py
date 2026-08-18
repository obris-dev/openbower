"""Wire-level constants for the discover proxy."""

from __future__ import annotations

from enum import StrEnum

# Request bounds. The data service re-validates against its own limits;
# these just stop oversized bodies at the proxy door. (`cursor` is not bounded
# here: it is opaque and data-issued, so the proxy must not reject a value data
# would accept.) These intentionally SHADOW the data service's own
# MAX_PAGE_LIMIT (1000) / MAX_INLINE_DOMAINS: a DoS door-stop, so
# they must never exceed the data bounds, else the proxy would reject values
# data would honor. Keep in step if the data limits ever rise.
MAX_INLINE_DOMAINS = 5000
# Upper bound for the ?limit= passthrough (poll pages, load-more);
# mirrors the data service's MAX_PAGE_LIMIT. The save-list drain pages
# by SAVE_LIST_PAGE below, not this.
MAX_LIMIT = 1000
# Mirrors the data service's MIN_SEEDS: cohorts are required (support
# ranking needs corroboration between seeds), so reject single-seed
# queries at the proxy door with the same rule the upstream enforces.
MIN_INLINE_DOMAINS = 2


# The run-lifecycle statuses the proxy branches on (from the shared
# contract's `status` field; pending/running need no names here, they are
# simply "not complete, not failed").
RUN_STATUS_COMPLETE = "complete"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELED = "canceled"


# Stable error codes for the proxy's own failure modes (the data service's
# 400/404 bodies pass through with THEIR codes untouched).
class DiscoverErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CONFLICT = "conflict"
    DATA_ACCESS_DENIED = "data_access_denied"
    DATA_UNAVAILABLE = "data_unavailable"
    DATA_ERROR = "data_error"


# How many raw column values the list-seeding read walks before giving
# up on finding more normalizable domains (a 25k-row sheet whose column
# is mostly junk must not be walked forever to fill a 5000 cap).
MAX_LIST_SEED_VALUES = 50_000

# The save-list snapshot's page size: coarser than the UI's poll pages
# (the drain is server-to-server), within the upstream's MAX_PAGE_LIMIT.
SAVE_LIST_PAGE = 200
