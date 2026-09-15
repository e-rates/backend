from decimal import Decimal

from erates.models import County, Parcel, RateSchedule
from erates.payment_flow import generate_rate_bills

NAIROBI_BANDS = [{'max_ha': '0.1', 'amount': '2560'}, {'max_ha': '0.2', 'amount': '3200'}, {'max_ha': '0.4', 'amount': '4000'}]


def schedule_for(county_name, year, deadline, **overrides):
    county = County.objects.filter(name__iexact=county_name).first() or County.objects.create(name=county_name)
    values = {
        'bands': NAIROBI_BANDS, 'top_amount': Decimal('4800'), 'usv_rate_percent': Decimal('0.115'),
        'deadline': deadline, **overrides,
    }
    schedule, _ = RateSchedule.objects.update_or_create(county=county, year=year, defaults=values)
    return schedule


def bill_everyone(year, deadline):
    for name in Parcel.objects.values_list('county', flat=True).distinct():
        generate_rate_bills(schedule_for(name, year, deadline))
