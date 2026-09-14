from decimal import Decimal, ROUND_HALF_UP

# Nairobi City County gazette notices for 2025 and 2027 (National Rating Act, 2024)
USV_RATE = Decimal('0.00115')
FLAT_BANDS_HA = [
    (Decimal('0.1'), Decimal('2560')),
    (Decimal('0.2'), Decimal('3200')),
    (Decimal('0.4'), Decimal('4000')),
]
FLAT_TOP = Decimal('4800')


def annual_rate(parcel) -> Decimal:
    if parcel.unimproved_site_value:
        amount = Decimal(parcel.unimproved_site_value) * USV_RATE
    else:
        hectares = Decimal(str(parcel.area_m2 or 0)) / Decimal('10000')
        amount = next((fee for limit, fee in FLAT_BANDS_HA if hectares <= limit), FLAT_TOP)
    return amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP)  # Daraja rejects fractional KES


def rate_basis(parcel) -> str:
    return 'usv' if parcel.unimproved_site_value else 'flat_area_band'


def rate_explanation(parcel) -> str:
    if parcel.unimproved_site_value:
        return f"0.115% of site value KES {Decimal(parcel.unimproved_site_value):,.0f}"
    hectares = Decimal(str(parcel.area_m2 or 0)) / Decimal('10000')
    lower = Decimal('0')
    for limit, fee in FLAT_BANDS_HA:
        if hectares <= limit:
            return f"Flat rate for {lower}–{limit} ha plots: KES {fee:,.0f}"
        lower = limit
    return f"Flat rate for plots over {FLAT_BANDS_HA[-1][0]} ha: KES {FLAT_TOP:,.0f}"
