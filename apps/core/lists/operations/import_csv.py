"""The runnable: ImportCsvOperation(inputs).run() -> ImportStats.

CSV in, plain sheet out: every import produces columns + rows and
NOTHING else (no resolution, no entity bookkeeping; company-ness is
decided later, at use time, by whoever points at a column). The parsing
and type-inference helpers are pure so the subtle parts test without a
database or an upload."""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass

from openbower_kernel.domains import normalize_domain
from openbower_schema.lists import derive_column_key

from ..constants import (
    COLUMN_KEY_MAX_LENGTH,
    COLUMN_LABEL_MAX_LENGTH,
    MAX_LIST_COLUMNS,
    MAX_LIST_ROWS,
    ColumnType,
    ListOrigin,
)
from ..models import List
from ..services.lists import ListService

# Type inference threshold: a column is url/number/email only when the
# vote is decisive; mixed columns stay text (a wrong type renders wrong
# everywhere, a text fallback renders plainly everywhere).
_TYPE_THRESHOLD = 0.8
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NUMBER_SHAPE = re.compile(r"^-?[\d,]+(\.\d+)?$")


class CsvTooLarge(Exception):
    """The upload exceeds MAX_CSV_BYTES or MAX_LIST_ROWS."""


logger = logging.getLogger(__name__)


class CsvUnusable(Exception):
    """No header, no columns, or undecodable bytes."""


@dataclass
class ImportStats:
    target: List
    rows: int = 0
    skipped: int = 0  # blank lines and rows wider than the header


def column_key(label: str, *, taken: set[str]) -> str:
    """A stable snake_case key from a header label, unique within the
    sheet (data dicts key on it, so collisions would silently merge
    columns)."""
    # The contract's derivation, never a local copy: an AI fill matches
    # a column BY KEY, so an imported header and an output label that
    # read the same must land the same.
    base = derive_column_key(label) or "column"
    key, n = base, 1
    while key in taken:
        n += 1
        key = f"{base[: COLUMN_KEY_MAX_LENGTH - len(str(n)) - 1]}_{n}"
    return key


def infer_type(values: list[str]) -> str:
    """The column's display type from its non-empty values (rendering
    only; no behavior branches on the answer)."""
    present = [v for v in values if v.strip()]
    if not present:
        return ColumnType.TEXT
    total = len(present)
    # Email BEFORE url: normalize_domain strips userinfo (user@host is a
    # host reference), so every email also normalizes as a domain; the
    # more specific shape must win.
    if sum(1 for v in present if _EMAIL_SHAPE.match(v.strip())) / total >= _TYPE_THRESHOLD:
        return ColumnType.EMAIL
    if sum(1 for v in present if _NUMBER_SHAPE.match(v.strip())) / total >= _TYPE_THRESHOLD:
        return ColumnType.NUMBER
    if sum(1 for v in present if normalize_domain(v)) / total >= _TYPE_THRESHOLD:
        return ColumnType.URL
    return ColumnType.TEXT


def parse_csv(raw: bytes) -> tuple[list[dict], list[dict], int]:
    """(columns, row data dicts, skipped). Header row is required and
    becomes the schema; short rows pad with "", wider-than-header rows
    are skipped and counted (silently truncating them would drop user
    data without a trace)."""
    try:
        text = raw.decode("utf-8-sig")  # -sig: spreadsheet exports love a BOM
    except UnicodeDecodeError as e:
        raise CsvUnusable("the file is not UTF-8 text") from e
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as e:
        raise CsvUnusable("the file is empty") from e
    except csv.Error as e:
        raise CsvUnusable("the file is not parseable as CSV") from e
    labels = [h.strip()[:COLUMN_LABEL_MAX_LENGTH] for h in header]
    if not any(labels):
        raise CsvUnusable("the first row must be a header")
    # Refuse, never truncate: a silently narrowed header would make
    # every data row read as too wide and skip, importing an empty
    # sheet that answers 201.
    if len(labels) > MAX_LIST_COLUMNS:
        raise CsvUnusable(
            f"the file has {len(labels)} columns; a list holds at most {MAX_LIST_COLUMNS}. "
            "Remove columns from the file and import again"
        )

    keys: list[str] = []
    taken: set[str] = set()
    for label in labels:
        key = column_key(label or "column", taken=taken)
        taken.add(key)
        keys.append(key)

    rows: list[dict] = []
    skipped = 0
    # Streamed, with csv.Error wrapping the ITERATION: the module
    # refuses some inputs mid-read (a field over its 128KiB limit), and
    # materializing the file first would build millions of row objects
    # before any cap fired.
    cells_iter = iter(reader)
    while True:
        try:
            cells = next(cells_iter)
        except StopIteration:
            break
        except csv.Error as e:
            raise CsvUnusable("the file is not parseable as CSV") from e
        if not any(c.strip() for c in cells):
            skipped += 1
            continue
        if len(cells) > len(labels):
            skipped += 1
            continue
        padded = list(cells) + [""] * (len(labels) - len(cells))
        rows.append({key: value.strip() for key, value in zip(keys, padded, strict=True)})
        if len(rows) > MAX_LIST_ROWS:
            raise CsvTooLarge(f"a list holds at most {MAX_LIST_ROWS} rows")

    columns = [
        {"key": key, "label": label or key, "type": infer_type([r[key] for r in rows])}
        for key, label in zip(keys, labels, strict=True)
    ]
    return columns, rows, skipped


class ImportCsvOperation:
    def __init__(self, *, account_id: str, user_id: str, label: str, raw: bytes) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.label = label
        self.raw = raw

    def run(self) -> ImportStats:
        columns, rows, skipped = parse_csv(self.raw)
        service = ListService(account_id=self.account_id, user_id=self.user_id)
        target = service.create(label=self.label, columns=columns, origin=ListOrigin.CSV)
        added = len(service.add_rows(target, rows))
        target.refresh_from_db()
        logger.info("csv import: list=%s rows=%d skipped=%d", target.id, added, skipped)
        return ImportStats(target=target, rows=added, skipped=skipped)
