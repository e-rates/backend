import re

from django.db import migrations

from erates.counties import KENYA_COUNTIES


def add_counties(apps, schema_editor):
    County = apps.get_model('erates', 'County')
    for name in KENYA_COUNTIES:
        base = re.sub(r'\s+(city\s+)?county$', '', name, flags=re.I)
        if not County.objects.filter(name__iregex=rf'^{re.escape(base)}(\s+(city\s+)?county)?$').exists():
            County.objects.create(name=name, is_active=False)


class Migration(migrations.Migration):
    dependencies = [('erates', '0015_conversations')]
    operations = [migrations.RunPython(add_counties, migrations.RunPython.noop)]
