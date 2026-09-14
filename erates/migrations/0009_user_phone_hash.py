from django.db import migrations, models


def populate(apps, schema_editor):
    from erates.models import phone_lookup_hash

    User = apps.get_model('erates', 'User')
    for user in User.objects.exclude(phone__isnull=True).exclude(phone='').only('pk', 'phone'):
        User.objects.filter(pk=user.pk).update(phone_hash=phone_lookup_hash(user.phone))


class Migration(migrations.Migration):
    dependencies = [('erates', '0008_backfill_parcel_audit')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='phone_hash',
            field=models.CharField(blank=True, editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(populate, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='user',
            name='phone_hash',
            field=models.CharField(blank=True, editable=False, max_length=64, null=True, unique=True),
        ),
    ]
