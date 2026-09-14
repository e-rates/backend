import hashlib
import hmac
import json

from django.conf import settings
from django.db import migrations


def _hash(*parts):
    message = '|'.join(str(p) for p in parts).encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def backfill(apps, schema_editor):
    ParcelHistory = apps.get_model('erates', 'ParcelHistory')
    AuditLog = apps.get_model('erates', 'AuditLog')
    if AuditLog.objects.filter(action__startswith='parcel.').exists():
        return
    previous_owner = {}
    last = AuditLog.objects.order_by('-audit_id').first()
    previous_hash = last.log_hash if last else None
    for h in ParcelHistory.objects.select_related('parcel', 'owner_user', 'changed_by').order_by('change_ts', 'history_id'):
        before = previous_owner.get(h.parcel_id)
        details = {
            'parcel_ref': h.parcel.parcel_ref,
            'new_owner': h.owner_user.username if h.owner_user_id else None,
            'reason': h.change_reason,
            'backfilled': True,
        }
        if before:
            details['previous_owner'] = before
        details = {k: v for k, v in details.items() if v is not None}
        action = 'parcel.transferred' if before else 'parcel.assigned'
        log = AuditLog(
            who_id=h.changed_by_id, action=action, object_type='parcel', object_id=h.parcel_id,
            details=details, previous_hash=previous_hash,
        )
        log.log_hash = _hash(
            log.who_id, log.action, log.object_type, log.object_id, None,
            json.dumps(details, sort_keys=True, default=str), previous_hash or '',
        )
        log.save()
        AuditLog.objects.filter(pk=log.pk).update(created_at=h.change_ts)
        previous_hash = log.log_hash
        previous_owner[h.parcel_id] = details.get('new_owner')


class Migration(migrations.Migration):
    dependencies = [('erates', '0007_mpesa_rate_bills')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
