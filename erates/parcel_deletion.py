"""Who may remove a parcel, and what has to be escalated.

Three tiers:
  * clean      - never allocated, never billed. A county official deletes it themselves.
  * escalate   - allocated or billed, but nothing has been paid. Needs owner approval.
  * protected  - money has changed hands. Never deletable; archive it instead.
"""
from .models import Parcel, Payment

CLEAN = 'clean'
ESCALATE = 'escalate'
PROTECTED = 'protected'


def classify(parcel: Parcel) -> tuple[str, str]:
    """Return (tier, human explanation)."""
    bills = Payment.objects.filter(parcel=parcel, is_deleted=False)

    if bills.filter(status='completed').exists():
        return PROTECTED, (
            'This plot has completed rate payments. Deleting it would orphan receipts the '
            'ratepayer holds. Archive it instead by setting its status to archived.'
        )

    if bills.exists():
        return ESCALATE, 'This plot has rate bills raised against it.'

    if parcel.owner_user_id:
        return ESCALATE, 'This plot is allocated to a land owner.'

    return CLEAN, 'This plot has never been allocated or billed.'


def can_delete_directly(parcel: Parcel) -> bool:
    return classify(parcel)[0] == CLEAN
