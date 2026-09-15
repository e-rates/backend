from datetime import timedelta
from decimal import Decimal

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates.models import Parcel, Payment, User
from erates.tests.billing import bill_everyone


def square(offset):
    return Polygon.from_bbox((36.8 + offset, -1.3, 36.801 + offset, -1.299))


class RateReportTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', 'admin@example.com', 'Password123!', role='admin')
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'Password123!')
        make = lambda ref, ward, i: Parcel.objects.create(
            parcel_ref=ref, geom=square(i * 0.01), county='Nyeri', sub_county='Tetu', ward=ward, owner_user=self.owner,
        )
        self.paid = make('A1', 'karura', 0)
        self.overdue = make('A2', 'karura', 1)
        self.unpaid = make('B1', 'mugunda', 2)
        bill_everyone(2026, timezone.now() + timedelta(days=30))
        Payment.objects.filter(parcel=self.overdue).update(deadline=timezone.now() - timedelta(days=5))
        bill = Payment.objects.get(parcel=self.paid)
        bill.status, bill.processor_ref = 'completed', 'RCPT123'
        bill.save()
        self.client.force_authenticate(self.admin)

    def test_wards_summary_orders_defaulters_first(self):
        data = self.client.get('/api/payments/wards/?year=2026').json()['wards']
        self.assertEqual([w['ward'] for w in data], ['karura', 'mugunda'])
        karura = data[0]
        self.assertEqual((karura['parcels'], karura['paid'], karura['unpaid'], karura['overdue']), (2, 1, 1, 1))
        self.assertEqual(Decimal(karura['overdue_amount']), Payment.objects.get(parcel=self.overdue).amount)

    def test_ward_parcels_lists_overdue_first(self):
        rows = self.client.get('/api/payments/ward-parcels/?ward=Karura&year=2026').json()['parcels']
        self.assertEqual([(r['parcel_ref'], r['status']) for r in rows], [('A2', 'overdue'), ('A1', 'paid')])
        self.assertEqual(rows[1]['receipt'], 'RCPT123')

    def test_collections_counts_only_completed(self):
        data = self.client.get('/api/payments/collections/?year=2026').json()
        paid_amount = Payment.objects.get(parcel=self.paid).amount
        self.assertEqual(Decimal(data['total_collected']), paid_amount)
        self.assertEqual(Decimal(data['collected_today']), paid_amount)
        self.assertEqual(data['payments_count'], 1)

    def test_payment_statuses_by_parcel_ref(self):
        statuses = self.client.get('/api/parcels/payment-statuses/?year=2026').json()['statuses']
        self.assertEqual(statuses, {'A1': 'paid', 'A2': 'overdue', 'B1': 'unpaid'})

    def test_reports_are_admin_only(self):
        self.client.force_authenticate(self.owner)
        for url in ('/api/payments/wards/', '/api/payments/collections/', '/api/parcels/payment-statuses/'):
            self.assertEqual(self.client.get(url).status_code, 403, url)
