import base64
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

BASE_URLS = {
    'sandbox': 'https://sandbox.safaricom.co.ke',
    'production': 'https://api.safaricom.co.ke',
}
TIMEOUT = 30
NAIROBI = ZoneInfo('Africa/Nairobi')
MIN_AMOUNT, MAX_AMOUNT = 1, 250_000  # Daraja M-Pesa Express per-transaction limits


class MpesaError(Exception):
    pass


def _base_url() -> str:
    return BASE_URLS[settings.MPESA_ENV]


def normalize_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif len(digits) == 9:
        digits = '254' + digits
    if not re.fullmatch(r'254[17]\d{8}', digits):
        raise MpesaError('Enter a valid Safaricom number, e.g. 0712345678')
    return digits


def _access_token() -> str:
    token = cache.get('mpesa_access_token')
    if token:
        return token
    if not (settings.MPESA_CONSUMER_KEY and settings.MPESA_CONSUMER_SECRET):
        raise MpesaError('M-Pesa is not configured')
    try:
        resp = requests.get(
            f'{_base_url()}/oauth/v1/generate?grant_type=client_credentials',
            auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise MpesaError(f'Could not authenticate with M-Pesa: {exc}') from exc
    token = data['access_token']
    cache.set('mpesa_access_token', token, int(data.get('expires_in', 3599)) - 60)
    return token


def _password(timestamp: str) -> str:
    raw = f'{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}'
    return base64.b64encode(raw.encode()).decode()


def _post(path: str, payload: dict) -> dict:
    try:
        resp = requests.post(
            f'{_base_url()}{path}',
            json=payload,
            headers={'Authorization': f'Bearer {_access_token()}'},
            timeout=TIMEOUT,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise MpesaError(f'M-Pesa request failed: {exc}') from exc
    if resp.status_code >= 400:
        raise MpesaError(data.get('errorMessage') or data.get('ResponseDescription') or str(data))
    return data


def _timestamp() -> str:
    return datetime.now(NAIROBI).strftime('%Y%m%d%H%M%S')


# Safaricom caps these: AccountReference 12 characters, TransactionDesc 13.
ACCOUNT_REF_MAX = 12
DESCRIPTION_MAX = 13


def account_reference(value: str) -> str:
    """What the ratepayer sees as the account number, and what the county reconciles against."""
    # Case is preserved: the county reconciles paybill statements against the plot reference.
    prefix = re.sub(r'[^A-Za-z0-9]', '', settings.MPESA_ACCOUNT_PREFIX or '')
    plot = re.sub(r'[^A-Za-z0-9]', '', value or '')
    if not plot:
        raise MpesaError('Bill has no usable account reference')
    ref = f'{prefix}{plot}'[:ACCOUNT_REF_MAX]
    # A prefix must never crowd out the plot number, which is what identifies the bill.
    return ref if plot[:ACCOUNT_REF_MAX] in ref else plot[:ACCOUNT_REF_MAX]


def transaction_description(year=None) -> str:
    """The line under the amount on the phone. 'Land rates 2026' is 15 chars, so close it up."""
    label = f'LandRates{year}' if year else 'Land rates'
    return label[:DESCRIPTION_MAX]


def stk_push(phone: str, amount: int, account_ref: str, description: str) -> dict:
    if not (settings.MPESA_CALLBACK_URL and settings.MPESA_CALLBACK_SECRET):
        raise MpesaError('MPESA_CALLBACK_URL and MPESA_CALLBACK_SECRET must be set')
    if not MIN_AMOUNT <= amount <= MAX_AMOUNT:
        raise MpesaError(f'M-Pesa Express accepts KES {MIN_AMOUNT} to {MAX_AMOUNT:,} per transaction')
    timestamp = _timestamp()
    data = _post('/mpesa/stkpush/v1/processrequest', {
        'BusinessShortCode': settings.MPESA_SHORTCODE,
        'Password': _password(timestamp),
        'Timestamp': timestamp,
        'TransactionType': settings.MPESA_TRANSACTION_TYPE,
        'Amount': amount,
        'PartyA': phone,
        'PartyB': settings.MPESA_PARTY_B or settings.MPESA_SHORTCODE,
        'PhoneNumber': phone,
        'CallBackURL': f"{settings.MPESA_CALLBACK_URL.rstrip('/')}/{settings.MPESA_CALLBACK_SECRET}/",
        'AccountReference': account_ref,
        'TransactionDesc': description[:DESCRIPTION_MAX],
    })
    if str(data.get('ResponseCode')) != '0':
        raise MpesaError(data.get('ResponseDescription') or 'STK push rejected')
    return data


def stk_query(checkout_request_id: str) -> dict:
    timestamp = _timestamp()
    return _post('/mpesa/stkpushquery/v1/query', {
        'BusinessShortCode': settings.MPESA_SHORTCODE,
        'Password': _password(timestamp),
        'Timestamp': timestamp,
        'CheckoutRequestID': checkout_request_id,
    })


def parse_callback_items(stk_callback: dict) -> dict:
    items = stk_callback.get('CallbackMetadata', {}).get('Item', [])
    return {item.get('Name'): item.get('Value') for item in items}
