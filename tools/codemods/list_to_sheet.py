"""Rename the core's List to Sheet: the `lists` app, its models, and the
names that mean the product noun.

    uv run --no-sync python -m tools.codemods.list_to_sheet

Moves the files with `git mv`, then rewrites Python NAME and STRING tokens
under apps/core from an explicit map, never a regex over text, so `list(`,
`list[...]`, `values_list`, DRF's `ListField`, `Listener*` and every
collection-sense name are untouched by construction. Idempotent: a second
run finds nothing to move or rename.

Out of reach on purpose, done by hand after it:
- comments and docstrings (prose needs judgment);
- the wire: `openbower_schema` imports and the packages/ and tools/ trees,
  so the contract's `List*` names and fields wait for their own step;
- migrations, whose operations are written by hand;
- exact "list"/"lists"/"list id" strings (verbose names, the app name, the
  web's `/lists` route);
- `list_id` in `discover/`, where it is also the request field the web
  sends; its ORM filters are fixed by hand.

A file where a target name already exists is reported and left unwritten,
so two names never merge silently.
"""

import io
import re
import subprocess
import sys
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "apps" / "core"

MOVES = [
    ("apps/core/lists", "apps/core/sheets"),
    ("apps/core/sheets/models/list.py", "apps/core/sheets/models/sheet.py"),
    ("apps/core/sheets/models/list_row.py", "apps/core/sheets/models/sheet_row.py"),
    ("apps/core/sheets/services/lists.py", "apps/core/sheets/services/sheets.py"),
]

# Module path segments: the app, and the moved modules.
MODULES = {"lists": "sheets", "list": "sheet", "list_row": "sheet_row"}

NAMES = {
    "List": "Sheet",
    "ListRow": "SheetRow",
    "ListCellState": "SheetCellState",
    "ListService": "SheetService",
    "ListNotFound": "SheetNotFound",
    "ListOrigin": "SheetOrigin",
    "ListsFull": "SheetFull",
    "ListColumnsField": "SheetColumnsField",
    "MAX_LIST_ROWS": "MAX_SHEET_ROWS",
    "MAX_LIST_COLUMNS": "MAX_SHEET_COLUMNS",
    "ListsConfig": "SheetsConfig",
    "ListCreateRequest": "SheetCreateRequest",
    "ListPatchRequest": "SheetPatchRequest",
    "ListsView": "SheetsView",
    "ListImportView": "SheetImportView",
    "ListDetailView": "SheetDetailView",
    "ListRowsView": "SheetRowsView",
    "ListRowView": "SheetRowView",
    "ListIngestView": "SheetIngestView",
    "ListFillsView": "SheetFillsView",
    "LIST_ROWS_INGESTED": "SHEET_ROWS_INGESTED",
    "LIST_MISSING": "SHEET_MISSING",
    "list_id": "sheet_id",
    "target_list": "target_sheet",
    "target_list_id": "target_sheet_id",
    "lists": "sheets",
    "list_row": "sheet_row",
    "_get_lists": "_get_sheets",
    "_list_or_404": "_sheet_or_404",
    "_list_or_raise": "_sheet_or_raise",
    "_list_summary": "_sheet_summary",
    "list_wire": "sheet_wire",
    "list_detail_wire": "sheet_detail_wire",
    "list_counts": "sheet_counts",
    "foreign_lists": "foreign_sheets",
    "locked_list": "locked_sheet",
    "page_for_list": "page_for_sheet",
    "delete_for_list": "delete_for_sheet",
    "purge_for_list": "purge_for_sheet",
    "purge_list": "purge_sheet",
    "workflow_by_list": "workflow_by_sheet",
    "by_list": "by_sheet",
    "one_list": "one_sheet",
    "list_c": "sheet_c",
    "_make_list": "_make_sheet",
    "LIST": "SHEET",
    "LIST_ID": "SHEET_ID",
    "_LIST": "_SHEET",
    "ListDeleteTests": "SheetDeleteTests",
    "ListWireTests": "SheetWireTests",
    "ListServiceTests": "SheetServiceTests",
    "ListsViewsTests": "SheetsViewsTests",
    "ListColumnsFieldTests": "SheetColumnsFieldTests",
}

# Test names where "list" is the product noun. The rest ("never listed",
# "the restart list", "the push schema lists", listeners) mean something
# else and stay.
TESTS = [
    "test_foreign_list_reads_as_missing",
    "test_foreign_list_is_404",
    "test_the_lists_kinds_are_on_the_roster_at_boot",
    "test_list_origin_parity",
    "test_a_list_delete_takes_all_of_its_children_or_none",
    "test_deleting_the_list_removes_its_workflow_and_leaves_the_preview_node",
    "test_a_deleted_list_settles_list_missing",
    "test_a_walk_whose_list_is_gone_ends",
    "test_unknown_row_and_list_read_as_missing",
    "test_a_landing_and_a_list_delete_take_the_tables_in_the_same_order",
    "test_the_write_locks_the_row_and_not_the_list",
    "test_a_purged_list_cancels_the_fill",
    "test_a_rerank_waits_for_the_lists_open_fills",
    "test_crud_and_delete_sets_lists_loose",
    "test_unknown_list_is_404_and_no_cookie_is_401",
    "test_page_for_list_keysets_open_runs_only",
    "test_a_missing_list_is_dropped_not_retried",
    "test_publish_produces_the_serialized_event_keyed_by_list",
    "test_the_backfill_job_names_its_list_as_its_target",
    "test_deleting_the_list_deletes_its_webhook_runs",
    "test_a_foreign_accounts_list_is_404",
    "test_a_vanished_list_settles_list_missing",
    "test_a_sharded_autofill_pick_seeks_one_list",
    "test_unknown_list_column_and_fill_less_column_read_as_missing",
    "test_the_create_returns_the_list_and_starts_no_fill",
    "test_foreign_list_and_foreign_agent_read_as_missing",
    "test_list_delete_purges_runs_and_outcomes",
    "test_the_request_inserts_no_runs_and_locks_the_list_around_one_write",
]
for _name in TESTS:
    _words = ["sheets" if w == "lists" else "sheet" if w == "list" else w for w in _name.split("_")]
    NAMES[_name] = "_".join(_words)

DISCOVER_NAMES = {old: new for old, new in NAMES.items() if old != "list_id"}

# Table names in test SQL, before the generic `lists_` prefix (URL names
# and the other tables).
TABLES = {
    "lists_listcellstate": "sheets_sheetcellstate",
    "lists_listrow": "sheets_sheetrow",
    "lists_list": "sheets_sheet",
}


def rename_module_path(path: str) -> str:
    """`lists.services.lists.ListService.add_rows` segment by segment."""
    head, *rest = path.split(".")
    return ".".join([MODULES[head], *(MODULES.get(s) or NAMES.get(s, s) for s in rest)])


def rename_string(text: str, counts: Counter) -> str:
    """A module path (`mock.patch("lists....")`) segment by segment, and the
    table and URL names (`lists_<name>`) inside a string; any other string
    is left alone."""
    prefix = text[: len(text) - len(text.lstrip("rRbBuU"))]
    width = 3 if text[len(prefix) :].startswith(('"""', "'''")) else 1
    body = text[len(prefix) + width : len(text) - width]
    if body.startswith("lists.") and all(s.isidentifier() for s in body.split(".")):
        counts["str: module path"] += 1
        return f"{text[: len(prefix) + width]}{rename_module_path(body)}{text[len(text) - width :]}"
    renamed = body
    for old, table in TABLES.items():
        renamed = re.sub(rf"\b{old}\b", table, renamed)
    renamed = re.sub(r"\blists_(?=\w)", "sheets_", renamed)
    if renamed == body:
        return text
    counts["str: table or URL name"] += 1
    return f"{text[: len(prefix) + width]}{renamed}{text[len(text) - width :]}"


def rewrite(path: Path, counts: Counter) -> tuple[str, list[str]]:
    src = path.read_text()
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    names = NAMES if "discover" not in path.relative_to(CORE).parts else DISCOVER_NAMES
    code = [t for t in toks if t.type not in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    # A module path in an import (`from lists.models import ...`) is not a
    # name in the file's scope, so it cannot clash.
    present = set()
    in_import = False
    for i, t in enumerate(code):
        if t.type == tokenize.NEWLINE:
            in_import = False
        elif t.string in ("from", "import") and (i == 0 or code[i - 1].type == tokenize.NEWLINE):
            in_import = True
        elif t.type == tokenize.NAME and not in_import:
            present.add(t.string)
    clashes = [f"{old} -> {new}" for old, new in names.items() if old in present and new in present]
    edits = []
    skip_statement = False
    for i, t in enumerate(code):
        if t.type == tokenize.NEWLINE:
            skip_statement = False
            continue
        prev = code[i - 1] if i else None
        if (
            t.type == tokenize.NAME
            and t.string in ("from", "import")
            and (prev is None or prev.type == tokenize.NEWLINE)
        ):
            nxt = code[i + 1]
            skip_statement = nxt.string == "openbower_schema"
        if skip_statement:
            continue
        new = None
        if t.type == tokenize.NAME:
            nxt = code[i + 1] if i + 1 < len(code) else None
            is_module = prev is not None and prev.string == "." and nxt is not None and nxt.string == "import"
            if t.string == "list":
                new = "sheet" if is_module else None
            elif t.string in names:
                new = names[t.string]
        elif t.type == tokenize.STRING:
            renamed = rename_string(t.string, counts)
            new = renamed if renamed != t.string else None
        if new is not None:
            if t.type == tokenize.NAME:
                counts[t.string] += 1
            edits.append((t.start, t.end, new))
    starts = [0]
    for line in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    out = src
    for (srow, scol), (erow, ecol), new in reversed(edits):
        out = out[: starts[srow - 1] + scol] + new + out[starts[erow - 1] + ecol :]
    return out, clashes


def main() -> int:
    for old, new in MOVES:
        if (ROOT / old).exists():
            subprocess.run(["git", "mv", old, new], cwd=ROOT, check=True)
            sys.stdout.write(f"moved {old} -> {new}\n")
    counts: Counter = Counter()
    blocked = []
    changed = 0
    for path in sorted(CORE.rglob("*.py")):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        text, clashes = rewrite(path, counts)
        if clashes:
            blocked.append((path, clashes))
            continue
        if text != path.read_text():
            path.write_text(text)
            changed += 1
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        sys.stdout.write(f"{count:6} {name}\n")
    sys.stdout.write(f"{changed} files rewritten\n")
    for path, clashes in blocked:
        sys.stderr.write(f"BLOCKED {path.relative_to(ROOT)}: {', '.join(clashes)}\n")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
