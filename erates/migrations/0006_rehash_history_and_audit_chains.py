import hashlib

from django.db import migrations


def rehash_chains(apps, schema_editor):
    ParcelHistory = apps.get_model('erates', 'ParcelHistory')
    AuditLog = apps.get_model('erates', 'AuditLog')

    previous_by_parcel = {}
    for h in ParcelHistory.objects.order_by('change_ts', 'history_id'):
        previous = previous_by_parcel.get(h.parcel_id)
        data = f"{h.parcel_id}{h.owner_user_id}{h.area_m2}{h.changed_by_id}{h.change_reason}{previous or ''}"
        record_hash = hashlib.sha256(data.encode()).hexdigest()
        ParcelHistory.objects.filter(pk=h.pk).update(previous_hash=previous, record_hash=record_hash)
        previous_by_parcel[h.parcel_id] = record_hash

    previous = None
    for log in AuditLog.objects.order_by('created_at', 'audit_id'):
        data = f"{log.who_id}{log.action}{log.object_type}{log.object_id}{previous or ''}"
        log_hash = hashlib.sha256(data.encode()).hexdigest()
        AuditLog.objects.filter(pk=log.pk).update(previous_hash=previous, log_hash=log_hash)
        previous = log_hash


class Migration(migrations.Migration):

    dependencies = [
        ('erates', '0005_recompute_integrity_hashes_and_areas'),
    ]

    operations = [
        migrations.RunPython(rehash_chains, migrations.RunPython.noop),
    ]
