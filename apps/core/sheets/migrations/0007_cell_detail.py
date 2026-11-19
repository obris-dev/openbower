# A cell record's detail: what its writer said about it beyond its state.
# Agent cells settled before it existed get theirs from the run that
# wrote them, the last time anything reads a run for a cell: a FILLED
# cell only when the row still holds that run's answer (an occupied or
# edited cell kept another value), an UNVERIFIED one with the answer the
# run dropped, offered only when it carries no URL (grounding needs the
# run's hit list, which no record kept) and the run had evidence or no
# tools. Plain dicts throughout: a migration imports no contract. The
# reverse is a no-op; the column goes with the schema reversal.

import re

from django.db import migrations, models
from django.db.models.fields.json import KeyTransform

_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_BATCH = 500


def _detail(assessment, *, unconfirmed=""):
    return {
        "kind": "agent",
        "confidence": float(assessment.get("confidence") or 0.0),
        "reason": assessment.get("reason") or "",
        "unconfirmed": unconfirmed,
    }


def _offered(assessment, result):
    dropped = assessment.get("dropped") or ""
    if not dropped or _URL.search(dropped):
        return ""
    if result.get("tool_calls") and not result.get("evidence"):
        return ""
    return dropped


def _backfill(apps, schema_editor):
    Sheet = apps.get_model("sheets", "Sheet")
    SheetRow = apps.get_model("sheets", "SheetRow")
    NodeRun = apps.get_model("sheets", "NodeRun")
    SheetCellState = apps.get_model("sheets", "SheetCellState")
    for sheet_id, columns in Sheet.objects.values_list("id", "columns").iterator():
        agent_keys = {c["key"]: c["node_id"] for c in columns or [] if c.get("kind") == "ai" and c.get("node_id")}
        if not agent_keys:
            continue
        records = list(
            SheetCellState.objects.filter(
                sheet_id=str(sheet_id),
                column_key__in=list(agent_keys),
                source="node",
                state__in=["filled", "unverified"],
                detail__isnull=True,
            )
        )
        if not records:
            continue
        # The newest DONE run of each (row, node): what wrote the cell.
        newest = {}
        runs = (
            NodeRun.objects.filter(sheet_id=str(sheet_id), node_id__in=set(agent_keys.values()), status="done")
            .annotate(
                assessments=KeyTransform("assessments", "result"),
                cells=KeyTransform("cells", "result"),
                evidence=KeyTransform("evidence", "result"),
                tool_calls=KeyTransform("tool_calls", "result"),
            )
            .order_by("-id")
            .values_list("row_id", "node_id", "assessments", "cells", "evidence", "tool_calls")
        )
        for row_id, node_id, assessments, cells, evidence, tool_calls in runs.iterator():
            newest.setdefault(
                (row_id, node_id),
                {"assessments": assessments or {}, "cells": cells or {}, "evidence": evidence, "tool_calls": tool_calls},
            )
        row_data = dict(
            SheetRow.objects.filter(id__in={r.row_id for r in records if r.state == "filled"}).values_list("id", "data")
        )
        changed = []
        for record in records:
            run = newest.get((record.row_id, agent_keys[record.column_key]))
            assessment = (run or {}).get("assessments", {}).get(record.column_key)
            if not isinstance(assessment, dict):
                continue
            if record.state == "filled":
                answer = run["cells"].get(record.column_key)
                if not answer or (row_data.get(record.row_id) or {}).get(record.column_key) != answer:
                    continue
                record.detail = _detail(assessment)
            else:
                record.detail = _detail(assessment, unconfirmed=_offered(assessment, run))
            changed.append(record)
        SheetCellState.objects.bulk_update(changed, ["detail"], batch_size=_BATCH)


class Migration(migrations.Migration):
    dependencies = [
        ("sheets", "0006_row_external_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="sheetcellstate",
            name="detail",
            field=models.JSONField(blank=True, null=True, verbose_name="detail"),
        ),
        migrations.RunPython(_backfill, migrations.RunPython.noop),
    ]
