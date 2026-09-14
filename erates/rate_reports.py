import re

def county_q(field_name: str, county_name):
    """Build a Q object matching county with or without 'County' suffix, case-insensitively."""
    from django.db.models import Q
    if not county_name:
        return Q()
    base = re.sub(r'\s+(city\s+)?county$', '', str(county_name).strip(), flags=re.I)
    return (
        Q(**{f'{field_name}__iexact': base}) |
        Q(**{f'{field_name}__iexact': f'{base} County'}) |
        Q(**{f'{field_name}__iexact': f'{base} City County'})
    )

from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import Parcel, Payment, User

NAIROBI = ZoneInfo('Africa/Nairobi')


def bill_state(bill) -> str:
    if bill is None or bill.status == 'refunded':
        return 'not_billed'
    if bill.status == 'completed':
        return 'paid'
    if bill.status == 'processing':
        return 'processing'
    return 'overdue' if bill.is_defaulter() else 'unpaid'


def bills_for_year(year: int, parcels=None, county=None):
    bills = Payment.objects.filter(payment_year=year, parcel__isnull=False, is_deleted=False)
    if parcels is not None:
        bills = bills.filter(parcel__in=parcels)
    if county:
        bills = bills.filter(county_q('parcel__county', county))
    return {bill.parcel_id: bill for bill in bills}


def payment_statuses(year: int, county=None) -> dict:
    bills = bills_for_year(year, county=county)
    refs = Parcel.objects.filter(is_deleted=False, pk__in=bills.keys()).values_list('pk', 'parcel_ref')
    return {ref: bill_state(bills[pk]) for pk, ref in refs}


def collections(year: int, county=None) -> dict:
    completed = Payment.objects.filter(status='completed', is_deleted=False)
    if county:
        completed = completed.filter(
            county_q('parcel__county', county) | (Q(parcel__isnull=True) & county_q('user__county', county))
        )
    start_of_day = timezone.now().astimezone(NAIROBI).replace(hour=0, minute=0, second=0, microsecond=0)
    total = lambda qs: qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    return {
        'year': year,
        'total_collected': total(completed),
        'collected_today': total(completed.filter(updated_at__gte=start_of_day)),
        'collected_for_year': total(completed.filter(payment_year=year)),
        'payments_count': completed.count(),
    }


def years(county=None) -> list:
    """One row per rating year that has bills: what was billed, paid and is still outstanding."""
    bills = Payment.objects.filter(is_deleted=False, payment_year__isnull=False).exclude(status='refunded')
    if county:
        bills = bills.filter(
            county_q('parcel__county', county) | (Q(parcel__isnull=True) & county_q('user__county', county))
        )
    rows = {}
    for year, status, count, amount in (
        bills.values('payment_year', 'status')
        .annotate(n=Count('payment_id'), amt=Sum('amount'))
        .values_list('payment_year', 'status', 'n', 'amt')
    ):
        row = rows.setdefault(year, {
            'year': year, 'bills': 0, 'paid_bills': 0, 'unpaid_bills': 0,
            'billed': Decimal(0), 'collected': Decimal(0), 'outstanding': Decimal(0),
        })
        amount = amount or Decimal(0)
        row['bills'] += count
        row['billed'] += amount
        if status == 'completed':
            row['paid_bills'] += count
            row['collected'] += amount
        else:
            row['unpaid_bills'] += count
            row['outstanding'] += amount
    return sorted(rows.values(), key=lambda r: r['year'])


def _ward_key(parcel):
    return (parcel.ward or 'Unassigned').strip().lower(), parcel.sub_county or ''


def wards(year: int, county=None) -> list:
    owned = Parcel.objects.filter(is_deleted=False, owner_user__isnull=False)
    if county:
        owned = owned.filter(county_q('county', county))
    parcels = list(owned.only('parcel_id', 'ward', 'sub_county'))
    bills = bills_for_year(year, [p.pk for p in parcels])
    rows = defaultdict(lambda: {
        'parcels': 0, 'billed': 0, 'paid': 0, 'unpaid': 0, 'overdue': 0,
        'outstanding': Decimal('0'), 'overdue_amount': Decimal('0'), 'collected': Decimal('0'),
    })
    for parcel in parcels:
        key = _ward_key(parcel)
        row = rows[key]
        row['parcels'] += 1
        bill = bills.get(parcel.pk)
        state = bill_state(bill)
        if bill is None or state == 'not_billed':
            continue
        row['billed'] += 1
        if state == 'paid':
            row['paid'] += 1
            row['collected'] += bill.amount
        elif state in ('unpaid', 'overdue', 'processing'):
            row['unpaid'] += 1
            row['outstanding'] += bill.amount
            if state == 'overdue':
                row['overdue'] += 1
                row['overdue_amount'] += bill.amount
    result = [
        {'ward': ward, 'sub_county': sub_county, **values}
        for (ward, sub_county), values in rows.items()
    ]
    return sorted(result, key=lambda r: (-r['overdue'], -r['outstanding'], r['ward']))


def ward_parcels(ward: str, year: int, county=None) -> list:
    parcels = Parcel.objects.filter(is_deleted=False, owner_user__isnull=False).select_related('owner_user')
    if county:
        parcels = parcels.filter(county_q('county', county))
    parcels = parcels.filter(ward__isnull=True) if ward == 'unassigned' else parcels.filter(ward__iexact=ward)
    parcels = list(parcels)
    bills = bills_for_year(year, [p.pk for p in parcels])
    rows = []
    for parcel in parcels:
        bill = bills.get(parcel.pk)
        state = bill_state(bill)
        rows.append({
            'parcel_id': str(parcel.pk),
            'parcel_ref': parcel.parcel_ref,
            'registration_section': (parcel.props or {}).get('REG_SECTIO'),
            'owner': parcel.owner_user.username,
            'owner_phone': parcel.owner_user.phone,
            'owner_email': parcel.owner_user.email,
            'area_m2': parcel.area_m2,
            'land_use': parcel.land_use,
            'status': state,
            'amount': str(bill.amount) if bill else None,
            'deadline': bill.deadline.isoformat() if bill and bill.deadline else None,
            'days_overdue': bill.days_overdue() if bill else None,
            'receipt': bill.processor_ref if bill and bill.status == 'completed' else None,
            'paid_at': bill.updated_at.isoformat() if bill and bill.status == 'completed' else None,
        })
    order = {'overdue': 0, 'unpaid': 1, 'processing': 2, 'not_billed': 3, 'paid': 4}
    return sorted(rows, key=lambda r: (order[r['status']], -(r['days_overdue'] or 0), r['parcel_ref']))


def counties(year: int) -> list:
    """One row per county: parcels, staff, ratepayers and what has been billed and collected."""
    from django.db.models import Count

    rows = {}
    for county, parcels in (
        Parcel.objects.filter(is_deleted=False).exclude(county='')
        .values_list('county').annotate(n=Count('parcel_id'))
    ):
        rows.setdefault(county, _empty_county(county))['parcels'] = parcels
    for county, allocated in (
        Parcel.objects.filter(is_deleted=False, owner_user__isnull=False).exclude(county='')
        .values_list('county').annotate(n=Count('parcel_id'))
    ):
        rows.setdefault(county, _empty_county(county))['allocated'] = allocated
    for county, role, people in (
        User.objects.filter(is_deleted=False).exclude(county='')
        .values_list('county', 'role').annotate(n=Count('user_id'))
    ):
        row = rows.setdefault(county, _empty_county(county))
        if role == 'user':
            row['ratepayers'] = people
        elif role in ('admin', 'auditor'):
            row['officials'] += people

    for county, row in rows.items():
        bills = Payment.objects.filter(payment_year=year, parcel__county__iexact=county, is_deleted=False).exclude(status='refunded')
        totals = bills.aggregate(billed=Sum('amount'))
        collected = bills.filter(status='completed').aggregate(total=Sum('amount'))
        row['billed'] = totals['billed'] or Decimal(0)
        row['collected'] = collected['total'] or Decimal(0)
        row['outstanding'] = row['billed'] - row['collected']

    return sorted(rows.values(), key=lambda r: r['county'])


def _empty_county(name):
    return {
        'county': name, 'parcels': 0, 'allocated': 0, 'ratepayers': 0, 'officials': 0,
        'billed': Decimal(0), 'collected': Decimal(0), 'outstanding': Decimal(0),
    }
