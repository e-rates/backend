import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import audit, mpesa
from .models import Account, MpesaTransaction, Parcel, Payment
from .rates import annual_rate, rate_basis

logger = logging.getLogger(__name__)

PUSH_EXPIRY = timedelta(minutes=2)
QUERY_AFTER = timedelta(seconds=25)
QUERY_EVERY = timedelta(seconds=10)  # Daraja sandbox spike-arrests rapid STK queries


def generate_rate_bills(year: int, deadline, parcels=None) -> dict:
    parcels = parcels if parcels is not None else Parcel.objects.filter(
        owner_user__isnull=False, is_deleted=False,
    ).select_related('owner_user')
    created = skipped = 0
    for parcel in parcels:
        account, _ = Account.objects.get_or_create(
            owner_user=parcel.owner_user, account_type='main', defaults={'currency': 'KES'},
        )
        _, was_created = Payment.objects.get_or_create(
            idempotency_key=f'rates:{parcel.parcel_ref}:{year}',
            defaults={
                'user': parcel.owner_user,
                'account': account,
                'parcel': parcel,
                'payment_year': year,
                'amount': annual_rate(parcel),
                'deadline': deadline,
                'metadata': {'kind': 'land_rates', 'basis': rate_basis(parcel), 'land_use': parcel.land_use},
            },
        )
        created += was_created
        skipped += not was_created
    if created:
        audit.record('payment.bills_issued', object_type='rating_year', year=year, created=created,
                     deadline=deadline.isoformat() if deadline else None)
    return {'created': created, 'skipped': skipped}


def latest_push(payment):
    return payment.mpesa_transactions.order_by('-created_at').first()


def start_stk_push(payment_id, phone_raw: str) -> MpesaTransaction:
    phone = mpesa.normalize_phone(phone_raw)
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status == 'processing':
            last = latest_push(payment)
            if last and not last.is_final and timezone.now() - last.created_at < PUSH_EXPIRY:
                raise mpesa.MpesaError('A payment prompt is already waiting on your phone')
        elif payment.status not in ('pending', 'failed'):
            raise mpesa.MpesaError(f'This bill is already {payment.status}')

        # Ratepayers recognise their plot number; a bill UUID means nothing on a phone.
        ref = mpesa.account_reference(
            payment.parcel.parcel_ref if payment.parcel_id else f'BILL{payment.payment_id.hex[:8]}'
        )
        data = mpesa.stk_push(
            phone, int(payment.amount), ref, mpesa.transaction_description(payment.payment_year)
        )
        payment.metadata = {**(payment.metadata or {}), 'account_reference': ref}
        tx = MpesaTransaction.objects.create(
            payment=payment,
            checkout_request_id=data['CheckoutRequestID'],
            merchant_request_id=data.get('MerchantRequestID', ''),
            phone=phone,
            amount=payment.amount,
        )
        payment.status = 'processing'
        payment.failure_reason = None
        payment.save()
    audit.record('payment.prompt_sent', obj=payment, amount=str(payment.amount), phone=audit.mask(phone),
                 parcel_ref=ref, checkout_request_id=tx.checkout_request_id)
    return tx


def apply_result(checkout_request_id: str, result_code, result_desc: str, items=None, raw=None) -> bool:
    items = items or {}
    with transaction.atomic():
        tx = MpesaTransaction.objects.select_for_update().filter(checkout_request_id=checkout_request_id).first()
        if tx is None:
            logger.warning('M-Pesa result for unknown CheckoutRequestID %s', checkout_request_id)
            return False
        if tx.is_final:
            return True
        payment = Payment.objects.select_for_update().get(pk=tx.payment_id)

        tx.result_code = int(result_code)
        tx.result_desc = result_desc or ''
        tx.receipt = str(items.get('MpesaReceiptNumber') or '')
        if raw is not None:
            tx.raw_callback = raw
        tx.save()

        if payment.status != 'processing' or latest_push(payment).pk != tx.pk:
            return True

        paid = items.get('Amount')
        if tx.result_code == 0 and paid is not None and Decimal(str(paid)) != payment.amount:
            payment.failure_reason = f'Amount mismatch: paid {paid}, billed {payment.amount}. Needs review.'
            payment.metadata = {**(payment.metadata or {}), 'needs_review': True}
            event = ('payment.amount_mismatch', {'paid': str(paid), 'billed': str(payment.amount), 'receipt': tx.receipt or None})
        elif tx.result_code == 0:
            payment.status = 'completed'
            payment.processor = 'mpesa'
            payment.processor_ref = tx.receipt or tx.checkout_request_id
            payment.failure_reason = None
            event = ('payment.completed', {'amount': str(payment.amount), 'receipt': tx.receipt or None})
            payment.metadata = {
                **(payment.metadata or {}),
                'mpesa_transaction_date': items.get('TransactionDate'),
                'mpesa_payer_phone': items.get('PhoneNumber'),
                'confirmed_via': 'callback' if items else 'stk_query',
            }
        else:
            payment.status = 'failed'
            payment.failure_reason = tx.result_desc
            event = ('payment.failed', {'reason': tx.result_desc, 'result_code': tx.result_code})
        payment.save()
    action, details = event
    audit.record(action, obj=payment, who=payment.user, parcel_ref=payment.parcel.parcel_ref if payment.parcel_id else None,
                 via='callback' if raw is not None else 'status_query', **details)
    return True


def refresh_from_daraja(tx: MpesaTransaction) -> None:
    now = timezone.now()
    if tx.is_final or now - tx.created_at < QUERY_AFTER or now - tx.updated_at < QUERY_EVERY:
        return
    tx.save(update_fields=['updated_at'])
    try:
        data = mpesa.stk_query(tx.checkout_request_id)
    except mpesa.MpesaError:
        return  # Daraja errors while the prompt is still open
    if 'ResultCode' in data:
        apply_result(tx.checkout_request_id, data['ResultCode'], data.get('ResultDesc', ''))
