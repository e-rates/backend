# Generated migration to allow NULL owner_user_id in parcels table

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('erates', '0002_add_payment_deadline'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parcel',
            name='owner_user',
            field=models.ForeignKey(
                blank=True,
                help_text='Property owner (can be assigned later)',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='parcels',
                to='erates.user'
            ),
        ),
        migrations.AlterField(
            model_name='parcelhistory',
            name='owner_user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='erates.user'
            ),
        ),
    ]
