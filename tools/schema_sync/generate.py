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

from pydantic.json_schema import models_json_schema

from openbower_schema import (
    AuthUser,
    Company,
    FoldersList,
    ImportResult,
    ListRowsPage,
    ListsPage,
    ListSummary,
    LookalikeItem,
    LookalikeListResponse,
    RowsAdded,
)

# The models projected into the contract. Grow this list as shared shapes land.
CONTRACT_MODELS: list[type[Any]] = [
    AuthUser,
    Company,
    FoldersList,
    ImportResult,
    ListRowsPage,
    ListsPage,
    ListSummary,
    LookalikeItem,
    LookalikeListResponse,
    RowsAdded,
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
    refs, bundle = models_json_schema(keyed, ref_template=REF_TEMPLATE)
    return {
        "$schema": DIALECT,
        "title": "OpenBower contract",
        DEFS_KEY: bundle.get(DEFS_KEY, {}),
        # A `$defs` library, not a schema one instance validates against; roots
        # are an annotation the consumer reads to find the top-level shapes.
        "x-roots": [refs[(model, MODE)] for model in CONTRACT_MODELS],
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
