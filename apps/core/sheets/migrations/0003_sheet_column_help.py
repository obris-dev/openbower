# The columns field's help text names the contract's SheetColumn. Help text
# is migration state only: no SQL runs.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sheets', '0002_sheet_names'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sheet',
            name='columns',
            field=models.JSONField(default=list, help_text="The contract's SheetColumn dumps, in display order", verbose_name='columns'),
        ),
    ]
