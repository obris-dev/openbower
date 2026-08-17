"""Use-time normalization of a chosen column's values into seed domains:
dedupe in first-seen order, drop what does not normalize, cap. Pure, so
the subtle cases (URL-dressed values, duplicates across dressings) test
without a database."""

from __future__ import annotations

from openbower_kernel.domains import normalize_domain


def normalize_seed_values(values: list[str], *, cap: int) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        domain = normalize_domain(value)
        if domain:
            seen.setdefault(domain, None)
        if len(seen) >= cap:
            break
    return list(seen)
