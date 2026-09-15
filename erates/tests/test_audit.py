from datetime import timedelta
from unittest import mock

from django.contrib.gis.geos import Polygon
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from erates import mpesa
from erates.models import AuditLog, Parcel, User
from erates.tests.billing import bill_everyone


@override_settings(MPESA_CALLBACK_SECRET='s', MPESA_CALLBACK_URL='https://example.test/cb')
class AuditTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', 'admin@example.com', 'Password123!', role='admin')
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'Password123!', phone='0711111111')
        self.parcel = Parcel.objects.create(
            parcel_ref='P1', geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)), county='Nyeri', sub_county='Tetu',
        )

    def actions(self):
        return list(AuditLog.objects.order_by('audit_id').values_list('action', flat=True))

    def test_logins_are_recorded_with_masked_identifier(self):
        self.client.post('/api/token/', {'username': 'owner', 'password': 'wrong'}, format='json')
        self.client.post('/api/token/phone/', {'phone': '0799999999', 'password': 'x'}, format='json')
        self.client.post('/api/token/phone/', {'phone': '0711111111', 'password': 'Password123!'}, format='json')
        self.assertEqual(self.actions(), ['auth.login_failed', 'auth.login_failed', 'auth.login'])
        unknown = AuditLog.objects.filter(action='auth.login_failed').order_by('audit_id').last()
        self.assertEqual(unknown.details['identifier'], '•••••••999')
        self.assertIsNone(unknown.who)

    def test_allocation_and_payment_events(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'/api/parcels/{self.parcel.pk}/assign_owner/', {'new_owner_id': str(self.owner.pk)}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        bill_everyone(2026, timezone.now() + timedelta(days=30))
        bill = self.parcel.payments.get()

        self.client.force_authenticate(self.owner)
        with mock.patch.object(mpesa, 'stk_push', return_value={'CheckoutRequestID': 'c1', 'ResponseCode': '0'}):
            self.client.post(f'/api/payments/{bill.pk}/mpesa/', {'phone': '0711111111'}, format='json')
        self.client.force_authenticate(None)
        self.client.post('/api/payments/stk-callback/s/', {'Body': {'stkCallback': {
            'CheckoutRequestID': 'c1', 'ResultCode': 0, 'ResultDesc': 'ok',
            'CallbackMetadata': {'Item': [{'Name': 'Amount', 'Value': float(bill.amount)}, {'Name': 'MpesaReceiptNumber', 'Value': 'R1'}]},
        }}}, format='json')

        self.assertEqual(self.actions(), ['parcel.assigned', 'payment.bills_issued', 'payment.prompt_sent', 'payment.completed'])
        assigned = AuditLog.objects.get(action='parcel.assigned')
        self.assertEqual((assigned.who, assigned.details['new_owner']), (self.admin, 'owner'))
        self.assertEqual(AuditLog.objects.get(action='payment.completed').who, self.owner)

        self.client.force_authenticate(self.admin)
        rows = self.client.get('/api/audit-logs/?category=payment').json()['results']
        self.assertEqual([r['action'] for r in rows], ['payment.completed', 'payment.prompt_sent', 'payment.bills_issued'])
        self.assertTrue(all(r['integrity_verified'] for r in rows))
        self.assertIn('R1', rows[0]['summary'])

    def test_tampering_breaks_integrity(self):
        self.client.post('/api/token/', {'username': 'owner', 'password': 'wrong'}, format='json')
        log = AuditLog.objects.get()
        AuditLog.objects.filter(pk=log.pk).update(details={'identifier': 'someone-else'})
        log.refresh_from_db()
        self.assertFalse(log.verify_integrity())
