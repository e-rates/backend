from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import Payment, Waiver, WaiverClaim
from .rate_reports import county_q

UNPAID = ('pending', 'failed')


def active_waivers(county):
    today = timezone.localdate()
    rows = list(Waiver.objects.filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=today),
        county=county, revoked_at__isnull=True, is_deleted=False, starts_on__lte=today,
    ))
    claimed = defaultdict(set)
    for waiver_id, parcel_id in WaiverClaim.objects.filter(waiver__in=rows).values_list('waiver_id', 'parcel_id'):
        claimed[waiver_id].add(parcel_id)
    for waiver in rows:
        waiver.claimed = claimed[waiver.pk]
    return rows


def _in(values, value):
    return not values or (value or '').strip().lower() in {v.strip().lower() for v in values}


def covers(waiver, parcel, year=None):
    return (
        (year is None or not waiver.years or year in waiver.years)
        and _in(waiver.sub_counties, parcel.sub_county)
        and _in(waiver.wards, parcel.ward)
        and _in(waiver.land_uses, parcel.land_use)
        and _in(waiver.parcel_refs, parcel.parcel_ref)
    )


def best(waivers, parcel, year):
    matching = [w for w in waivers if parcel.pk in w.claimed and covers(w, parcel, year)]
    return max(matching, key=lambda w: w.percent, default=None)


def net_amount(gross, waiver):
    if waiver is None:
        return gross
    return (gross * (Decimal(100) - waiver.percent) / Decimal(100)).quantize(Decimal('1'))


def is_waived_off(bill):
    return bill.status == 'completed' and bill.processor == 'waiver'


def settle(bill, gross, waiver):
    """Sets amount, status and waiver note from the gross; returns whether anything changed."""
    before = (bill.amount, bill.status, bill.processor, bill.processor_ref, (bill.metadata or {}).get('waiver'))
    net = net_amount(gross, waiver)
    metadata = {**(bill.metadata or {}), 'standard_amount': str(gross)}
    metadata.pop('waiver', None)
    if waiver:
        metadata['waiver'] = {'waiver_id': str(waiver.waiver_id), 'name': waiver.name, 'percent': str(waiver.percent)}
    bill.amount, bill.metadata = net, metadata
    if waiver and net == 0:
        bill.status, bill.processor, bill.processor_ref = 'completed', 'waiver', f'WAIVER-{str(waiver.waiver_id)[:8].upper()}'
    elif is_waived_off(bill):
        bill.status, bill.processor, bill.processor_ref = 'pending', None, None
    return before != (bill.amount, bill.status, bill.processor, bill.processor_ref, metadata.get('waiver'))


def open_bills(county):
    return Payment.objects.filter(
        county_q('parcel__county', county.name), is_deleted=False, parcel__isnull=False,
    ).filter(Q(status__in=UNPAID) | Q(status='completed', processor='waiver')).select_related('parcel')


def gross_of(bill):
    return Decimal(str((bill.metadata or {}).get('standard_amount') or bill.amount))


def apply_waivers(county) -> dict:
    waivers = active_waivers(county)
    changed = 0
    for bill in open_bills(county):
        if settle(bill, gross_of(bill), best(waivers, bill.parcel, bill.payment_year)):
            bill.save()
            changed += 1
    return {'bills_changed': changed}


def impact(waiver) -> dict:
    bills = [b for b in open_bills(waiver.county) if covers(waiver, b.parcel, b.payment_year)]
    waived = sum((gross_of(b) - net_amount(gross_of(b), waiver) for b in bills), Decimal(0))
    return {'bills': len(bills), 'parcels': len({b.parcel_id for b in bills}), 'amount_waived': str(waived)}
