from django.db import migrations

# subcounties/wards/localities were dropped from the code long ago, but their tables and
# their foreign keys to counties survived and block any delete of a county.
DROP = """
DROP TABLE IF EXISTS localities CASCADE;
DROP TABLE IF EXISTS wards CASCADE;
DROP TABLE IF EXISTS subcounties CASCADE;
"""

# Irreversible by design: the models these mirrored no longer exist to rebuild from.
RESTORE = migrations.RunSQL.noop


class Migration(migrations.Migration):

    dependencies = [
        ('erates', '0012_parceldeletionrequest'),
    ]

    operations = [
        migrations.RunSQL(DROP, RESTORE),
    ]
