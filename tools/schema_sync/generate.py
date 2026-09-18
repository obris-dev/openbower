"""Emit the shared wire contract as one bundled JSON Schema, and guard it.

`openbower_schema` is the single source of truth for the shapes shared by the
backend and the web client. This tool projects the models in CONTRACT_MODELS
into one JSON Schema document (2020-12, a single `$defs` block) so the web
generates its own zod validators from the same definitions instead of
hand-maintaining a parallel copy.

    uv run --no-sync python -m tools.schema_sync.generate           # write schema.json
    uv run --no-sync python -m tools.schema_sync.generate --check   # fail if stale

`--check` is the drift guard: regenerate in memory and compare to the
committed file (the same discipline as `uv lock --locked`).

Scoped on purpose: a flat CONTRACT_MODELS list and build + check, without the
heavier coverage/guard machinery. Add models to the list
as the contract grows.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from openbower_schema import (
    AgentCatalog,
    AgentsList,
    AgentSummary,
    AuthUser,
    ColumnPromptWire,
    FillRunDetail,
    FillRunPage,
    FillRunWire,
    FoldersList,
    FolderSummary,
    ImportResult,
    IngestAccepted,
    ListRowsPage,
    ListsPage,
    ListSummary,
    LookalikeListResponse,
    RowsAdded,
    WebhookColumnConfigWire,
    WebhookColumnPreviewResponse,
    WebhookColumnTestResponse,
    WebhookDeliveriesPage,
    WebhookDestinationCreated,
    WebhookDestinationsList,
    WebhookDestinationWire,
    WebhookDigestData,
    WebhookEnvelope,
    WebhookPingData,
)
from openbower_schema.agents import (
    MAX_TOOL_CALLS,
    RESERVED_OUTPUT_MARKER,
    SEARCH_PROVIDER_CHOICES,
    TEST_KEY_MAX_LENGTH,
    TEST_ROW_MAX_KEYS,
    TEST_VALUE_MAX_LENGTH,
    TOOL_STATUSES,
)
from openbower_schema.fills import (
    FREE_SEARCH_FILL_BUDGET,
    NODE_RUN_ATTEMPTS,
    ROW_LEASE_STALE_SECONDS,
    SETTLED_CELL_STATES,
)
from openbower_schema.webhooks import (
    DEFAULT_WEBHOOK_CADENCE_SECONDS,
    MAX_WEBHOOK_DESTINATIONS,
    MAX_WEBHOOK_HEADERS,
    WEBHOOK_CADENCE_SECONDS,
    RESERVED_WEBHOOK_HEADER_NAMES,
    WEBHOOK_HEADER_NAME_GRAMMAR,
    WEBHOOK_HEADER_NAME_MAX_LENGTH,
    WEBHOOK_HEADER_VALUE_GRAMMAR,
    WEBHOOK_HEADER_VALUE_MAX_LENGTH,
    WEBHOOK_ID_HEADER,
    WEBHOOK_ROTATION_GRACE_SECONDS,
    WEBHOOK_SECRET_PREFIX,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_SIGNATURE_VERSION,
    WEBHOOK_TIMESTAMP_HEADER,
)

# The models projected into the contract: exactly the RESPONSE ROOTS
# (shapes a client validates a whole response body against). Nested
# models ride in through $refs and still emit as named zod schemas, so
# they are never listed here; a client-side standalone use (a draft
# validating a nested shape) needs no listing either.
CONTRACT_MODELS: list[type[Any]] = [
    AgentCatalog,
    AgentsList,
    AgentSummary,
    AuthUser,
    ColumnPromptWire,
    FillRunDetail,
    FillRunPage,
    FillRunWire,
    FoldersList,
    FolderSummary,
    ImportResult,
    IngestAccepted,
    ListRowsPage,
    ListsPage,
    ListSummary,
    LookalikeListResponse,
    RowsAdded,
    WebhookColumnConfigWire,
    WebhookColumnPreviewResponse,
    WebhookColumnTestResponse,
    WebhookDeliveriesPage,
    WebhookDestinationCreated,
    WebhookDestinationsList,
    WebhookDestinationWire,
    # Not response roots: the body a RECEIVER validates and the two
    # shapes its `data` takes, listed so the contract documents what it
    # sends.
    WebhookEnvelope,
    WebhookPingData,
    WebhookDigestData,
]

# The committed artifact, in the schema package it belongs to. Anchored to this
# script (tools/schema_sync/generate.py -> repo root), which is stable: a repo
# script is never installed, so its path can't move into site-packages.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "packages" / "openbower-schema" / "schema.json"

DIALECT = "https://json-schema.org/draft/2020-12/schema"
DEFS_KEY = "$defs"
REF_TEMPLATE = f"#/{DEFS_KEY}/{{model}}"
# "serialization" is the shape the server writes on the wire, what a client
# validates a response against.
MODE = "serialization"


def build_schema() -> dict[str, Any]:
    keyed = [(model, MODE) for model in CONTRACT_MODELS]
    _refs, bundle = models_json_schema(keyed, ref_template=REF_TEMPLATE)
    return {
        "$schema": DIALECT,
        "title": "OpenBower contract",
        DEFS_KEY: bundle.get(DEFS_KEY, {}),
        # Output keys the server refuses (they collide with the answer
        # model's own attributes; hasattr(BaseModel, key) server-side).
        # Derived keys never start with an underscore, so the public
        # surface is the whole set. The web's readiness mirror reads
        # this so a refused name never passes the checklist.
        "x-reserved-output-keys": sorted(name for name in dir(BaseModel) if not name.startswith("_")),
        # Wire facts with no Field to hang on (the web reads them as
        # WIRE_CONSTANTS): scalars, plus the settled partition of
        # CellState so the client derives it instead of retyping it.
        "x-constants": {
            "NODE_RUN_ATTEMPTS": NODE_RUN_ATTEMPTS,
            "FREE_SEARCH_FILL_BUDGET": FREE_SEARCH_FILL_BUDGET,
            "MAX_TOOL_CALLS": MAX_TOOL_CALLS,
            "RESERVED_OUTPUT_MARKER": RESERVED_OUTPUT_MARKER,
            "ROW_LEASE_STALE_SECONDS": ROW_LEASE_STALE_SECONDS,
            "SEARCH_PROVIDER_CHOICES": list(SEARCH_PROVIDER_CHOICES),
            "SETTLED_CELL_STATES": list(SETTLED_CELL_STATES),
            # Each tool's status vocabulary (its own enum, containing
            # the base codes), so the client's copy table is typed per
            # tool and an unknown code degrades instead of crashing.
            "TOOL_STATUSES": {tool: list(statuses) for tool, statuses in TOOL_STATUSES.items()},
            "TEST_ROW_MAX_KEYS": TEST_ROW_MAX_KEYS,
            "TEST_KEY_MAX_LENGTH": TEST_KEY_MAX_LENGTH,
            "TEST_VALUE_MAX_LENGTH": TEST_VALUE_MAX_LENGTH,
            # Header facts have no wire field (values never ride the
            # wire), so the client's header grammar mirrors these.
            "MAX_WEBHOOK_DESTINATIONS": MAX_WEBHOOK_DESTINATIONS,
            "MAX_WEBHOOK_HEADERS": MAX_WEBHOOK_HEADERS,
            "RESERVED_WEBHOOK_HEADER_NAMES": list(RESERVED_WEBHOOK_HEADER_NAMES),
            "WEBHOOK_HEADER_NAME_GRAMMAR": WEBHOOK_HEADER_NAME_GRAMMAR,
            "WEBHOOK_HEADER_NAME_MAX_LENGTH": WEBHOOK_HEADER_NAME_MAX_LENGTH,
            "WEBHOOK_HEADER_VALUE_GRAMMAR": WEBHOOK_HEADER_VALUE_GRAMMAR,
            "WEBHOOK_HEADER_VALUE_MAX_LENGTH": WEBHOOK_HEADER_VALUE_MAX_LENGTH,
            # The signature scheme's names, so the verify guide the
            # client renders reads them rather than retyping them.
            "WEBHOOK_ID_HEADER": WEBHOOK_ID_HEADER,
            "WEBHOOK_TIMESTAMP_HEADER": WEBHOOK_TIMESTAMP_HEADER,
            "WEBHOOK_SIGNATURE_HEADER": WEBHOOK_SIGNATURE_HEADER,
            "WEBHOOK_SIGNATURE_VERSION": WEBHOOK_SIGNATURE_VERSION,
            "WEBHOOK_SECRET_PREFIX": WEBHOOK_SECRET_PREFIX,
            # A webhook column's schedule is a choice, and rotation's
            # grace is a fact the settings page states.
            "WEBHOOK_CADENCE_SECONDS": list(WEBHOOK_CADENCE_SECONDS),
            "DEFAULT_WEBHOOK_CADENCE_SECONDS": DEFAULT_WEBHOOK_CADENCE_SECONDS,
            "WEBHOOK_ROTATION_GRACE_SECONDS": WEBHOOK_ROTATION_GRACE_SECONDS,
        },
    }


def _serialize(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed schema.json is stale.")
    args = parser.parse_args(argv)

    rendered = _serialize(build_schema())
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current != rendered:
            sys.stderr.write(f"{SCHEMA_PATH} is stale; run `python -m tools.schema_sync.generate`.\n")
            return 1
        return 0
    SCHEMA_PATH.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"Wrote {SCHEMA_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
