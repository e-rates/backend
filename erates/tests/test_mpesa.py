from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.gis.geos import Polygon
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from erates import mpesa
from erates.models import MpesaTransaction, Parcel, Payment, RateSchedule, User
from erates.payment_flow import generate_rate_bills
from erates.rates import annual_rate
from erates.tests.billing import bill_everyone

SECRET = 'cb-secret'


def square(size_deg=0.001):
    return Polygon.from_bbox((36.8, -1.3, 36.8 + size_deg, -1.3 + size_deg))


def callback(checkout_id, code=0, amount=None, receipt='SGH123ABC'):
    cb = {'MerchantRequestID': 'm-1', 'CheckoutRequestID': checkout_id, 'ResultCode': code, 'ResultDesc': 'desc'}
    if code == 0:
        cb['CallbackMetadata'] = {'Item': [
            {'Name': 'Amount', 'Value': amount},
            {'Name': 'MpesaReceiptNumber', 'Value': receipt},
            {'Name': 'PhoneNumber', 'Value': 254708374149},
        ]}
    return {'Body': {'stkCallback': cb}}


@override_settings(MPESA_CALLBACK_SECRET=SECRET, MPESA_CALLBACK_URL='https://example.test/cb')
class MpesaFlowTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'password123')
        self.other = User.objects.create_user('other', 'other@example.com', 'password123')
        self.parcel = Parcel.objects.create(
            parcel_ref='NBI/1', geom=square(), county='Nairobi', sub_county='Westlands', owner_user=self.owner,
        )
        bill_everyone(2026, timezone.now() + timedelta(days=30))
        self.bill = Payment.objects.get(parcel=self.parcel, payment_year=2026)
        self.client.force_authenticate(self.owner)

    def pay(self, checkout_id='ws_CO_1'):
        with mock.patch.object(mpesa, 'stk_push', return_value={
            'CheckoutRequestID': checkout_id, 'MerchantRequestID': 'm-1', 'ResponseCode': '0',
        }) as push:
            resp = self.client.post(f'/api/payments/{self.bill.pk}/mpesa/', {'phone': '0708374149'}, format='json')
        return resp, push

    def post_callback(self, body, secret=SECRET):
        self.client.force_authenticate(None)
        return self.client.post(f'/api/payments/stk-callback/{secret}/', body, format='json')

    def geojson_status(self, user=None, year=2026):
        self.client.force_authenticate(user or self.owner)
        resp = self.client.get(f'/api/parcels/geojson/?year={year}')
        return resp.json()['features'][0]['properties']

    def test_bill_amount_uses_flat_band_then_usv(self):
        schedule = RateSchedule.objects.get(county__name='Nairobi', year=2026)
        self.assertEqual(self.bill.amount, annual_rate(self.parcel, schedule))
        self.assertEqual(generate_rate_bills(schedule)['unchanged'], 1)
        self.parcel.unimproved_site_value = Decimal('10000000')
        self.assertEqual(annual_rate(self.parcel, schedule), Decimal('11500'))

    def test_successful_payment_greenlights_parcel(self):
        self.assertEqual(self.geojson_status()['payment_status'], 'unpaid')
        resp, push = self.pay()
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(push.call_args.args[:2], ('254708374149', int(self.bill.amount)))
        self.assertEqual(self.geojson_status()['payment_status'], 'processing')

        body = callback('ws_CO_1', amount=float(self.bill.amount))
        self.assertEqual(self.post_callback(body).json()['ResultCode'], 0)
        self.post_callback(body)

        self.bill.refresh_from_db()
        self.assertEqual((self.bill.status, self.bill.processor_ref), ('completed', 'SGH123ABC'))
        self.assertTrue(self.bill.verify_integrity())
        props = self.geojson_status()
        self.assertEqual(props['payment_status'], 'paid')
        self.assertEqual(props['bill']['receipt'], 'SGH123ABC')
        self.assertEqual(self.geojson_status(year=2027)['payment_status'], 'not_billed')

    def test_cancelled_push_can_be_retried(self):
        self.pay('ws_CO_1')
        self.post_callback(callback('ws_CO_1', code=1032))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, 'failed')
        self.client.force_authenticate(self.owner)
        resp, _ = self.pay('ws_CO_2')
        self.assertEqual(resp.status_code, 202)

    def test_second_push_blocked_while_prompt_open(self):
        self.pay('ws_CO_1')
        resp, push = self.pay('ws_CO_2')
        self.assertEqual(resp.status_code, 400)
        push.assert_not_called()

    def test_amount_mismatch_is_not_completed(self):
        self.pay()
        self.post_callback(callback('ws_CO_1', amount=1))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, 'processing')
        self.assertTrue(self.bill.metadata['needs_review'])

    def test_callback_rejects_bad_secret_and_unknown_ids(self):
        self.pay()
        self.assertEqual(self.post_callback(callback('ws_CO_1', amount=1), secret='nope').status_code, 404)
        self.post_callback(callback('ws_CO_unknown', amount=float(self.bill.amount)))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, 'processing')

    def test_other_users_cannot_pay_or_see_bill(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(f'/api/payments/{self.bill.pk}/mpesa/', {'phone': '0708374149'}, format='json')
        self.assertEqual(resp.status_code, 404)
        props = self.geojson_status(user=self.other)
        self.assertEqual((props['payment_status'], props['bill']), ('not_billed', None))

    def test_status_endpoint_falls_back_to_stk_query(self):
        self.pay()
        url = f'/api/payments/{self.bill.pk}/mpesa/status/'
        self.client.force_authenticate(self.owner)
        with mock.patch.object(mpesa, 'stk_query', return_value={'ResultCode': '0', 'ResultDesc': 'ok'}) as query:
            self.assertEqual(self.client.get(url).json()['status'], 'processing')
            past = timezone.now() - timedelta(minutes=1)
            MpesaTransaction.objects.update(created_at=past, updated_at=past)
            self.assertEqual(self.client.get(url).json()['status'], 'completed')
        query.assert_called_once()

    @override_settings(MPESA_SHORTCODE='174379', MPESA_PASSKEY='pk', MPESA_PARTY_B='')
    def test_stk_push_payload_matches_daraja_spec(self):
        with mock.patch.object(mpesa, '_post', return_value={'ResponseCode': '0', 'CheckoutRequestID': 'c'}) as post:
            mpesa.stk_push('254708374149', 1, mpesa.account_reference('NBI/Block 12/345'), 'Rates 2026')
        path, body = post.call_args.args
        self.assertEqual(path, '/mpesa/stkpush/v1/processrequest')
        self.assertEqual(set(body), {
            'BusinessShortCode', 'Password', 'Timestamp', 'TransactionType', 'Amount', 'PartyA',
            'PartyB', 'PhoneNumber', 'CallBackURL', 'AccountReference', 'TransactionDesc',
        })
        self.assertRegex(body['Timestamp'], r'^\d{14}$')
        self.assertEqual(body['AccountReference'], 'NBIBlock1234')
        self.assertLessEqual(len(body['TransactionDesc']), 13)
        self.assertEqual(body['PartyB'], '174379')
        self.assertEqual(body['CallBackURL'], f'https://example.test/cb/{SECRET}/')

    def test_stk_push_rejects_amounts_outside_limits(self):
        for amount in (0, 250_001):
            with self.assertRaises(mpesa.MpesaError):
                mpesa.stk_push('254708374149', amount, 'REF', 'Rates')

    def test_phone_normalization(self):
        self.assertEqual(mpesa.normalize_phone('+254 712-345-678'), '254712345678')
        self.assertEqual(mpesa.normalize_phone('0112345678'), '254112345678')
        with self.assertRaises(mpesa.MpesaError):
            mpesa.normalize_phone('12345')
