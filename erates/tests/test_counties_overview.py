from datetime import timedelta

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates.models import Parcel, Payment, User
from erates.payment_flow import generate_rate_bills


class CountiesOverviewTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('platform', 'p@example.com', 'Password123!', role='owner')
        User.objects.create_user('nyeri_admin', 'n@example.com', 'Password123!', role='admin', county='Nyeri')
        User.objects.create_user('nyeri_auditor', 'a@example.com', 'Password123!', role='auditor', county='Nyeri')
        ratepayer = User.objects.create_user('kamau', 'k@example.com', 'Password123!', county='Nyeri')

        allocated = Parcel.objects.create(
            parcel_ref='NY1', geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)),
            county='Nyeri', sub_county='Tetu', ward='karura', owner_user=ratepayer,
        )
        Parcel.objects.create(
            parcel_ref='NY2', geom=Polygon.from_bbox((36.81, -1.3, 36.811, -1.299)),
            county='Nyeri', sub_county='Tetu', ward='karura',
        )
        Parcel.objects.create(
            parcel_ref='KB1', geom=Polygon.from_bbox((36.9, -1.3, 36.901, -1.299)),
            county='Kiambu', sub_county='Limuru', ward='ndeiya',
        )
        generate_rate_bills(2026, timezone.now() + timedelta(days=30))
        bill = Payment.objects.get(parcel=allocated)
        bill.status = 'completed'
        bill.save()
        self.bill_amount = bill.amount

    def test_owner_sees_every_county_with_its_position(self):
        self.client.force_authenticate(self.owner)
        data = self.client.get('/api/payments/counties/?year=2026').json()
        by_name = {c['county']: c for c in data['counties']}
        self.assertEqual(sorted(by_name), ['Kiambu', 'Nyeri'])
        nyeri = by_name['Nyeri']
        self.assertEqual(
            (nyeri['parcels'], nyeri['allocated'], nyeri['ratepayers'], nyeri['officials']),
            (2, 1, 1, 2),
        )
        self.assertEqual(float(nyeri['collected']), float(self.bill_amount))
        self.assertEqual(float(nyeri['outstanding']), 0.0)
        self.assertEqual(by_name['Kiambu']['parcels'], 1)

    def test_counties_overview_is_owner_only(self):
        for username in ('nyeri_admin', 'nyeri_auditor', 'kamau'):
            self.client.force_authenticate(User.objects.get(username=username))
            self.assertEqual(self.client.get('/api/payments/counties/').status_code, 403, username)
