from datetime import timedelta

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates.models import AuditLog, County, Parcel, Payment, RateSchedule, User

BANDS = [{'max_ha': '0.1', 'amount': '1000'}, {'max_ha': '1', 'amount': '2000'}]


def plot(ref, county, owner):
    return Parcel.objects.create(
        parcel_ref=ref, geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)),
        county=county, sub_county='Tetu', ward='karura', owner_user=owner,
    )


class RateScheduleTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('nyeri_admin', 'n@example.com', 'Password123!', role='admin', county='Nyeri')
        owner = User.objects.create_user('kamau', 'k@example.com', 'Password123!', county='Nyeri')
        self.paid_plot = plot('N1', 'Nyeri County', owner)
        self.unpaid_plot = plot('N3', 'Nyeri', owner)
        plot('N2', 'Nyeri', None)
        plot('K1', 'Kiambu', owner)
        self.client.force_authenticate(self.admin)

    def body(self, year=2010, **overrides):
        deadline = (timezone.now() - timedelta(days=5)).isoformat()
        return {'year': year, 'bands': BANDS, 'top_amount': '3000', 'usv_rate_percent': '0.2', 'deadline': deadline, **overrides}

    def post(self, path, body):
        return self.client.post(f'/api/rate-schedules/{path}/', body, format='json')

    def test_preview_counts_without_billing(self):
        resp = self.post('preview', self.body())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.json()['created'], resp.json()['total_billed']), (2, '6000'))
        self.assertFalse(Payment.objects.exists())

    def test_issuing_bills_only_the_officials_county_and_is_logged(self):
        resp = self.post('issue', self.body())
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(set(Payment.objects.values_list('parcel__parcel_ref', flat=True)), {'N1', 'N3'})
        bill = Payment.objects.get(parcel=self.paid_plot)
        self.assertEqual((bill.payment_year, bill.amount), (2010, 3000))
        self.assertEqual(bill.metadata['explanation'], 'Flat rate for plots over 1 ha: KES 3,000')
        self.assertTrue(bill.verify_integrity())
        actions = set(AuditLog.objects.values_list('action', flat=True))
        self.assertTrue({'rate_schedule.set', 'payment.bills_issued'} <= actions)

    def test_reissuing_updates_unpaid_bills_only(self):
        self.post('issue', self.body())
        paid = Payment.objects.get(parcel=self.paid_plot)
        paid.status = 'completed'
        paid.save()
        resp = self.post('issue', self.body(top_amount='5000'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.json()['updated'], resp.json()['unchanged']), (1, 1))
        self.assertEqual(Payment.objects.get(parcel=self.paid_plot).amount, 3000)
        unpaid = Payment.objects.get(parcel=self.unpaid_plot)
        self.assertEqual(unpaid.amount, 5000)
        self.assertTrue(unpaid.verify_integrity())
        self.assertEqual(RateSchedule.objects.count(), 1)

    def test_years_go_back_twenty_years_and_no_further(self):
        current = timezone.now().year
        self.assertEqual(self.post('preview', self.body(year=current - 20)).status_code, 200)
        self.assertEqual(self.post('preview', self.body(year=current - 21)).status_code, 400)

    def test_auditors_cannot_issue_bills(self):
        auditor = User.objects.create_user('aud', 'a@example.com', 'Password123!', role='auditor', county='Nyeri')
        self.client.force_authenticate(auditor)
        self.assertEqual(self.post('issue', self.body()).status_code, 403)

    def test_officials_cannot_bill_another_county(self):
        self.post('issue', self.body(county='Kiambu County'))
        self.assertFalse(Payment.objects.filter(parcel__parcel_ref='K1').exists())

    def test_platform_owner_chooses_the_county(self):
        self.client.force_authenticate(User.objects.create_user('boss', 'b@example.com', 'Password123!', role='owner'))
        self.assertEqual(self.post('issue', self.body()).status_code, 400)
        self.assertEqual(self.post('issue', self.body(county='Kiambu County')).status_code, 201)
        self.assertEqual(list(Payment.objects.values_list('parcel__parcel_ref', flat=True)), ['K1'])

    def test_saved_schedules_are_listed_for_the_form(self):
        self.post('issue', self.body())
        schedules = self.client.get('/api/rate-schedules/').json()
        self.assertEqual([(s['year'], s['top_amount'], s['bands']) for s in schedules], [(2010, '3000.00', BANDS)])
