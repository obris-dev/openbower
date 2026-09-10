import lists.constants
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lists", "0005_processedingestevent"),
    ]

    operations = [
        # A pure column rename. Postgres carries the column's index and
        # unique constraint across `ALTER TABLE RENAME COLUMN`, so no DDL
        # rebuilds them; only model STATE needs updating.
        migrations.RenameField(model_name="filltask", old_name="fill_id", new_name="fill_run_id"),
        migrations.RenameField(model_name="fillcellstate", old_name="fill_id", new_name="fill_run_id"),
        # verbose_name only (no DDL).
        migrations.AlterField(
            model_name="filltask",
            name="fill_run_id",
            field=models.CharField(max_length=26, verbose_name="fill run id"),
        ),
        migrations.AlterField(
            model_name="fillcellstate",
            name="fill_run_id",
            field=models.CharField(max_length=26, verbose_name="fill run id"),
        ),
        # RenameField does not rewrite the field name inside Meta
        # constraints/indexes in STATE, so reconcile it there, but run NO
        # database operations: the real index and constraint already rode
        # the column rename above, and dropping + recreating the claim
        # index on a large fill_task table is needless work (and a window
        # without it). State only.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(model_name="filltask", name="fill_task_fill_row_uniq"),
                migrations.RemoveIndex(model_name="filltask", name="fill_task_claim_idx"),
                migrations.AddIndex(
                    model_name="filltask",
                    index=models.Index(
                        condition=models.Q(("status", lists.constants.FillTaskStatus["QUEUED"])),
                        fields=["fill_run_id", "position"],
                        name="fill_task_claim_idx",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="filltask",
                    constraint=models.UniqueConstraint(fields=("fill_run_id", "row_id"), name="fill_task_fill_row_uniq"),
                ),
            ],
            database_operations=[],
        ),
    ]
