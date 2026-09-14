from django.conf import settings
from django.db import migrations, models


def set_existing_counties(apps, schema_editor):
    """Existing accounts predate multi-county support: put them in the county they work in."""
    User = apps.get_model('erates', 'User')
    Parcel = apps.get_model('erates', 'Parcel')
    default = (Parcel.objects.exclude(county='').exclude(county='Unknown')
               .values_list('county', flat=True).first() or settings.COUNTY_NAME)
    for user in User.objects.all().only('pk'):
        county = (Parcel.objects.filter(owner_user_id=user.pk).exclude(county='')
                  .values_list('county', flat=True).first() or default)
        User.objects.filter(pk=user.pk).update(county=county)


class Migration(migrations.Migration):
    dependencies = [('erates', '0009_user_phone_hash')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='county',
            field=models.CharField(blank=True, db_index=True, help_text='County this account belongs to; blank for platform owners', max_length=100),
        ),
        migrations.AddField(
            model_name='user',
            name='must_change_password',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('user', 'Land owner'), ('admin', 'County official'), ('auditor', 'Auditor'), ('owner', 'Platform owner')],
                default='user', max_length=50,
            ),
        ),
        migrations.RunPython(set_existing_counties, migrations.RunPython.noop),
    ]
