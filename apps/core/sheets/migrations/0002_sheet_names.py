# The product noun is Sheet: the models, their tables (off the label
# `lists` they kept through 0001), the `list_id` pointers, the index and
# constraint names, and the stored run status a vanished sheet settles
# with. Every step reverses, so `migrate sheets 0001` puts back the schema
# the previous release reads. Old code cannot run against the renamed
# tables: deploy with the app stopped.

from django.db import migrations, models

_MODELS = [
    "folder",
    "sheet",
    "sheetcellstate",
    "sheetrow",
    "node",
    "nodepath",
    "noderun",
    "processedingestevent",
    "workflow",
]
_POINTERS = ["sheetcellstate", "sheetrow", "noderun", "processedingestevent", "workflow"]


def _rename_constraint(table, old, new, constraint):
    """No Django operation renames a constraint in place, so the database
    half is the one statement and the state half drops the old name and
    adds the new."""
    model = table.removeprefix("sheets_")
    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunSQL(
                f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"',
                f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new}" TO "{old}"',
            )
        ],
        state_operations=[
            migrations.RemoveConstraint(model_name=model, name=old),
            migrations.AddConstraint(model_name=model, constraint=constraint(new)),
        ],
    )


def _repoint(model, old, new):
    """The column rename already moved this index or constraint onto
    `sheet_id` in the database (Postgres renames a column everywhere it is
    used); only the migration state still names `list_id`."""
    if isinstance(new, models.Index):
        state = [migrations.RemoveIndex(model_name=model, name=old), migrations.AddIndex(model_name=model, index=new)]
    else:
        state = [
            migrations.RemoveConstraint(model_name=model, name=old),
            migrations.AddConstraint(model_name=model, constraint=new),
        ]
    return migrations.SeparateDatabaseAndState(state_operations=state)


def _status(old, new):
    def run(apps, schema_editor):
        apps.get_model("sheets", "NodeRun").objects.filter(status=old).update(status=new)

    return run


class Migration(migrations.Migration):
    dependencies = [
        ("sheets", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel("List", "Sheet"),
        migrations.RenameModel("ListRow", "SheetRow"),
        migrations.RenameModel("ListCellState", "SheetCellState"),
        *[migrations.AlterModelTable(name=model, table=None) for model in _MODELS],
        *[migrations.RenameField(model_name=model, old_name="list_id", new_name="sheet_id") for model in _POINTERS],
        migrations.RenameIndex(model_name="sheet", new_name="sheet_account_idx", old_name="list_account_idx"),
        _rename_constraint(
            "sheets_sheetrow",
            "list_row_rank_uniq",
            "sheet_row_rank_uniq",
            lambda name: models.UniqueConstraint(fields=("sheet_id", "rank"), name=name),
        ),
        _rename_constraint(
            "sheets_sheetrow",
            "list_row_rank_named",
            "sheet_row_rank_named",
            lambda name: models.CheckConstraint(condition=~models.Q(rank=""), name=name),
        ),
        _rename_constraint(
            "sheets_workflow",
            "workflow_list_uniq",
            "workflow_sheet_uniq",
            lambda name: models.UniqueConstraint(fields=("sheet_id",), name=name),
        ),
        _repoint(
            "noderun",
            "node_run_autofill_idx",
            models.Index(
                condition=models.Q(("fill_run_id__isnull", True)),
                fields=["status", "sheet_id", "rank", "id"],
                include=("not_before", "kind"),
                name="node_run_autofill_idx",
            ),
        ),
        _repoint(
            "sheetcellstate",
            "cell_state_column_idx",
            models.Index(fields=["sheet_id", "column_key", "state"], name="cell_state_column_idx"),
        ),
        _repoint(
            "sheetcellstate",
            "cell_state_state_idx",
            models.Index(fields=["sheet_id", "state"], name="cell_state_state_idx"),
        ),
        _repoint(
            "sheetcellstate",
            "cell_state_cell_uniq",
            models.UniqueConstraint(fields=("sheet_id", "row_id", "column_key"), name="cell_state_cell_uniq"),
        ),
        migrations.AlterModelOptions(name="sheet", options={"verbose_name": "sheet", "verbose_name_plural": "sheets"}),
        migrations.AlterModelOptions(
            name="sheetcellstate",
            options={"verbose_name": "sheet cell state", "verbose_name_plural": "sheet cell states"},
        ),
        migrations.AlterModelOptions(
            name="sheetrow", options={"verbose_name": "sheet row", "verbose_name_plural": "sheet rows"}
        ),
        migrations.AlterField(
            model_name="noderun",
            name="sheet_id",
            field=models.CharField(blank=True, default="", max_length=26, verbose_name="sheet id"),
        ),
        *[
            migrations.AlterField(
                model_name=model, name="sheet_id", field=models.CharField(max_length=26, verbose_name="sheet id")
            )
            for model in ["processedingestevent", "sheetcellstate", "sheetrow", "workflow"]
        ],
        migrations.AlterField(
            model_name="sheet",
            name="origin",
            field=models.CharField(help_text="SheetOrigin", max_length=16, verbose_name="origin"),
        ),
        migrations.RunPython(_status("list_missing", "sheet_missing"), _status("sheet_missing", "list_missing")),
    ]
