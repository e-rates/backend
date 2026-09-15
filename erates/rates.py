from decimal import Decimal, ROUND_HALF_UP


def _hectares(parcel):
    return Decimal(str(parcel.area_m2 or 0)) / Decimal('10000')


def _bands(schedule):
    return sorted((Decimal(str(b['max_ha'])), Decimal(str(b['amount']))) for b in schedule.bands)


def annual_rate(parcel, schedule) -> Decimal:
    if parcel.unimproved_site_value:
        amount = Decimal(parcel.unimproved_site_value) * Decimal(schedule.usv_rate_percent) / 100
    else:
        hectares = _hectares(parcel)
        amount = next((fee for limit, fee in _bands(schedule) if hectares <= limit), Decimal(schedule.top_amount))
    return amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP)  # Daraja rejects fractional KES


def rate_basis(parcel) -> str:
    return 'usv' if parcel.unimproved_site_value else 'flat_area_band'


def rate_explanation(parcel, schedule) -> str:
    if parcel.unimproved_site_value:
        return f"{Decimal(schedule.usv_rate_percent).normalize():f}% of site value KES {Decimal(parcel.unimproved_site_value):,.0f}"
    hectares = _hectares(parcel)
    lower = Decimal('0')
    for limit, fee in _bands(schedule):
        if hectares <= limit:
            return f"Flat rate for {lower.normalize():f}–{limit.normalize():f} ha plots: KES {fee:,.0f}"
        lower = limit
    return f"Flat rate for plots over {lower.normalize():f} ha: KES {Decimal(schedule.top_amount):,.0f}"
