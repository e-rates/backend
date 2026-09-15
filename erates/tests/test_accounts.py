from datetime import timedelta

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates.models import AuditLog, Parcel, Payment, User
from erates.tests.billing import bill_everyone


def plot(ref, county, ward='karura', offset=0):
    return Parcel.objects.create(
        parcel_ref=ref, geom=Polygon.from_bbox((36.8 + offset, -1.3, 36.801 + offset, -1.299)),
        county=county, sub_county='Tetu', ward=ward,
    )


class AccountHierarchyTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('platform', 'p@example.com', 'Password123!', role='owner')
        self.nyeri_official = User.objects.create_user('nyeri_admin', 'n@example.com', 'Password123!', role='admin', county='Nyeri')
        self.kiambu_official = User.objects.create_user('kiambu_admin', 'k@example.com', 'Password123!', role='admin', county='Kiambu')

    def test_public_signup_is_closed(self):
        resp = self.client.post('/api/users/', {'username': 'walkin', 'email': 'w@example.com', 'password': 'Password123!'}, format='json')
        self.assertIn(resp.status_code, (401, 403))
        self.assertFalse(User.objects.filter(username='walkin').exists())

    def test_owner_creates_official_with_one_time_password(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post('/api/users/', {
            'username': 'Meru_Admin', 'email': 'meru@example.com', 'phone': '0712345678',
            'role': 'admin', 'county': 'Meru',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        password = resp.json()['temporary_password']
        official = User.objects.get(username='meru_admin')
        self.assertEqual((official.role, official.county, official.must_change_password), ('admin', 'Meru', True))
        self.assertTrue(official.check_password(password))
        self.assertTrue(AuditLog.objects.filter(action='auth.account_created', details__username='meru_admin').exists())

    def test_owner_must_name_a_county_for_officials(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post('/api/users/', {'username': 'nocounty', 'email': 'nc@example.com', 'role': 'admin'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('county', resp.json())

    def test_official_creates_land_owner_in_their_own_county_only(self):
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.post('/api/users/', {
            'username': 'mwangi', 'email': 'mwangi@example.com', 'phone': '0722333444', 'county': 'Kiambu',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        created = User.objects.get(username='mwangi')
        self.assertEqual((created.role, created.county), ('user', 'Nyeri'))

    def test_official_cannot_create_another_official(self):
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.post('/api/users/', {'username': 'sneaky', 'email': 's@example.com', 'role': 'admin'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('role', resp.json())

    def test_land_owner_cannot_create_accounts(self):
        ratepayer = User.objects.create_user('mary', 'mary@example.com', 'Password123!', county='Nyeri')
        self.client.force_authenticate(ratepayer)
        self.assertEqual(self.client.post('/api/users/', {'username': 'x', 'email': 'x@example.com'}, format='json').status_code, 403)

    def test_first_login_flag_clears_when_password_changed(self):
        self.client.force_authenticate(self.owner)
        password = self.client.post('/api/users/', {
            'username': 'newofficial', 'email': 'no@example.com', 'role': 'auditor', 'county': 'Nyeri',
        }, format='json').json()['temporary_password']
        official = User.objects.get(username='newofficial')

        login = self.client.post('/api/token/', {'username': 'newofficial', 'password': password}, format='json')
        self.assertTrue(login.json()['user']['must_change_password'])

        self.client.force_authenticate(official)
        resp = self.client.post('/api/users/change_password/', {
            'current_password': password, 'new_password': 'Chosen-Pass123', 'new_password_confirm': 'Chosen-Pass123',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        official.refresh_from_db()
        self.assertFalse(official.must_change_password)

    def test_official_can_reset_a_land_owner_password_but_not_an_officials(self):
        ratepayer = User.objects.create_user('joy', 'joy@example.com', 'Password123!', county='Nyeri')
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.post(f'/api/users/{ratepayer.pk}/reset_password/', {}, format='json')
        self.assertEqual(resp.status_code, 200)
        ratepayer.refresh_from_db()
        self.assertTrue(ratepayer.check_password(resp.json()['temporary_password']))
        self.assertTrue(ratepayer.must_change_password)

        blocked = self.client.post(f'/api/users/{self.kiambu_official.pk}/reset_password/', {}, format='json')
        self.assertIn(blocked.status_code, (403, 404))


class CountyScopingTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('platform', 'p@example.com', 'Password123!', role='owner')
        self.nyeri_official = User.objects.create_user('nyeri_admin', 'n@example.com', 'Password123!', role='admin', county='Nyeri')
        nyeri_owner = User.objects.create_user('kamau', 'kamau@example.com', 'Password123!', county='Nyeri')
        kiambu_owner = User.objects.create_user('otieno', 'otieno@example.com', 'Password123!', county='Kiambu')
        self.nyeri_plot = plot('NY1', 'Nyeri', offset=0)
        self.kiambu_plot = plot('KB1', 'Kiambu', offset=0.05)
        self.nyeri_plot.owner_user, self.kiambu_plot.owner_user = nyeri_owner, kiambu_owner
        self.nyeri_plot.save()
        self.kiambu_plot.save()
        bill_everyone(2026, timezone.now() + timedelta(days=30))

    def refs(self, url):
        data = self.client.get(url).json()
        return sorted(f['properties']['parcel_ref'] for f in data['features'])

    def test_official_sees_only_their_county(self):
        self.client.force_authenticate(self.nyeri_official)
        self.assertEqual(self.refs('/api/parcels/geojson/'), ['NY1'])
        self.assertEqual([u['username'] for u in self.client.get('/api/users/').json()['results'] if u['username'] == 'otieno'], [])
        payments = self.client.get('/api/payments/').json()['results']
        self.assertEqual({p['parcel_ref'] for p in payments}, {'NY1'})
        wards = self.client.get('/api/payments/wards/?year=2026').json()['wards']
        self.assertEqual(sum(w['parcels'] for w in wards), 1)

    def test_platform_owner_sees_every_county(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.refs('/api/parcels/geojson/'), ['KB1', 'NY1'])
        self.assertEqual(sum(w['parcels'] for w in self.client.get('/api/payments/wards/?year=2026').json()['wards']), 2)

    def test_reports_follow_the_official_county(self):
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.get('/api/reports/download/?report=collections&file_format=xlsx&year=2026')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Payment.objects.filter(parcel__county='Kiambu').count(), 1)  # exists, but excluded above
