"""The canonical digest of a drafted config: what SETTLED means for a
diagnosed cell (a diagnosis holds only while the fingerprint stamped
at diagnosis time matches; a changed config is a changed ask, so
those cells re-target on the next refill) and what CONTINUE consents
to (a resume under a different fingerprint refuses; it would be a
different fill wearing the stopped one's name). Pure over the
contract model, so every custodian reads one derivation."""

from __future__ import annotations

import hashlib
import json

from openbower_schema.agents import AgentConfig


def config_fingerprint(config: AgentConfig) -> str:
    """`tools` canonicalizes to the SORTED ENABLED NAMES before
    hashing: a future tool's default-False field must not churn every
    stored fingerprint (re-targeting settled cells on the next
    Continue) just by existing in the shape."""
    dump = config.model_dump()
    dump["tools"] = sorted(name for name, on in dump["tools"].items() if on)
    return hashlib.sha256(json.dumps(dump, sort_keys=True).encode("utf-8")).hexdigest()
