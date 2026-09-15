from datetime import timedelta

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates import report_builders
from erates.models import Parcel, User
from erates.tests.billing import bill_everyone


class ReportCountyTests(APITestCase):
    def test_short_county_name_finds_full_name_parcels(self):
        owner = User.objects.create_user('kamau', 'k@example.com', 'Password123!')
        Parcel.objects.create(parcel_ref='N1', geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)),
                              county='Nyeri County', sub_county='Tetu', ward='Karura', owner_user=owner)
        bill_everyone(2026, timezone.now() - timedelta(days=3))
        report = report_builders.collections_report(2026, 'Nyeri')
        self.assertEqual(report.county, 'Nyeri County')
        self.assertEqual(report.summary[0][1], 1)
        self.assertEqual(report_builders.arrears_report(timezone.now().date(), 'Nyeri').summary[0][1], 1)
        self.assertTrue(report_builders.render_pdf(report, 'x').startswith(b'%PDF'))

    def test_reset_password_unlocks(self):
        boss = User.objects.create_user('boss', 'b@example.com', 'Password123!', role='owner')
        admin = User.objects.create_user('adm', 'a@example.com', 'Password123!', role='admin', county='Nyeri')
        admin.failed_login_attempts, admin.locked_until = 5, timezone.now() + timedelta(minutes=30)
        admin.save()
        self.client.force_authenticate(boss)
        self.assertEqual(self.client.post(f'/api/users/{admin.pk}/reset_password/').status_code, 200)
        admin.refresh_from_db()
        self.assertEqual((admin.failed_login_attempts, admin.locked_until), (0, None))
