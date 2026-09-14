import hashlib
import hmac
from decimal import Decimal

from django.conf import settings
from django.db import migrations


def _hmac(*parts):
    message = "|".join(str(p) for p in parts).encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _money(value):
    return f"{Decimal(value):.2f}"


def rehash(apps, schema_editor):
    Account = apps.get_model('erates', 'Account')
    Payment = apps.get_model('erates', 'Payment')
    LedgerEntry = apps.get_model('erates', 'LedgerEntry')

    for a in Account.objects.all():
        Account.objects.filter(pk=a.pk).update(
            balance_hash=_hmac(a.account_id, _money(a.current_balance))
        )
    for p in Payment.objects.all():
        Payment.objects.filter(pk=p.pk).update(
            payment_hash=_hmac(p.payment_id, p.user_id, p.account_id, _money(p.amount), p.currency, p.status)
        )
    for e in LedgerEntry.objects.all():
        LedgerEntry.objects.filter(pk=e.pk).update(
            entry_hash=_hmac(e.entry_id, e.account_id, _money(e.amount), _money(e.balance_after), e.entry_type)
        )


class Migration(migrations.Migration):

    dependencies = [
        ('erates', '0004_parcel_county_parcel_sub_county_parcel_ward'),
    ]

    operations = [
        migrations.RunPython(rehash, migrations.RunPython.noop),
        migrations.RunSQL(
            "UPDATE parcels SET area_m2 = ST_Area(geom::geography) WHERE geom IS NOT NULL",
            migrations.RunSQL.noop,
        ),
    ]
