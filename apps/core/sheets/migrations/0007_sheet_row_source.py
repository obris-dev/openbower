# The source a log takes rows from, with a database default so every sheet
# that exists, and every insert from a process still on the code before
# this column, reads "feed": every log until now is fed by a hosted
# signal, and a standard sheet ignores it. Additive, no stop.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sheets', '0006_row_external_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='sheet',
            name='row_source',
            field=models.CharField(db_default='feed', default='feed', help_text='RowSource', max_length=16, verbose_name='row source'),
        ),
    ]
