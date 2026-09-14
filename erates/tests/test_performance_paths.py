import json

from django.contrib.gis.geos import GEOSGeometry, Polygon
from rest_framework.test import APITestCase

from erates.models import Parcel, User, canonical_phone


class PhoneLookupTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('amina', 'amina@example.com', 'Password123!', phone='+254 712 345 678')

    def test_canonical_forms(self):
        for raw in ('0712345678', '+254712345678', '254 712-345-678', '712345678'):
            self.assertEqual(canonical_phone(raw), '254712345678', raw)

    def test_login_accepts_any_phone_format(self):
        for raw in ('0712345678', '+254712345678', '254712345678'):
            resp = self.client.post('/api/token/phone/', {'phone': raw, 'password': 'Password123!'}, format='json')
            self.assertEqual(resp.status_code, 200, raw)
            self.assertEqual(resp.json()['user']['username'], 'amina')

    def test_phone_change_updates_lookup(self):
        self.user.phone = '0799000111'
        self.user.save(update_fields=['phone'])
        self.assertEqual(User.find_by_phone('+254799000111'), self.user)
        self.assertIsNone(User.find_by_phone('0712345678'))

    def test_duplicate_number_in_other_format_is_rejected(self):
        other = User.objects.create_user('baraka', 'baraka@example.com', 'Password123!')
        self.client.force_authenticate(other)
        resp = self.client.patch('/api/users/me/', {'phone': '0712 345 678'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('phone', resp.json())


class GeoJSONTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', 'admin@example.com', 'Password123!', role='admin')
        edge = [(36.8 + i * 0.001, -1.29 + (0.00001 if i % 2 else 0)) for i in range(11)]
        self.shape = Polygon([(36.8, -1.3), *edge, (36.81, -1.3), (36.8, -1.3)], srid=4326)
        self.parcel = Parcel.objects.create(
            parcel_ref='G1', geom=self.shape, county='Nyeri', sub_county='Tetu', ward='karura',
            props={'REG_SECTIO': 'AGUTHI-GAAKI', 'SHEET_NO': '10'},
        )
        Parcel.objects.create(parcel_ref='G2', geom=Polygon.from_bbox((36.9, -1.3, 36.91, -1.29)), county='Nyeri', sub_county='Tetu')
        self.client.force_authenticate(self.admin)

    def get(self, query=''):
        resp = self.client.get(f'/api/parcels/geojson/{query}')
        self.assertEqual(resp.status_code, 200)
        return json.loads(resp.content)

    def test_features_match_stored_geometry_and_properties(self):
        data = self.get()
        self.assertEqual((data['type'], data['count']), ('FeatureCollection', 2))
        feature = next(f for f in data['features'] if f['properties']['parcel_ref'] == 'G1')
        self.assertTrue(GEOSGeometry(json.dumps(feature['geometry'])).equals_exact(self.shape, 1e-7))
        props = feature['properties']
        self.assertEqual((props['registration_section'], props['map_sheet'], props['payment_status']), ('AGUTHI-GAAKI', '10', 'not_billed'))
        self.assertAlmostEqual(props['centroid']['lng'], self.parcel.centroid.x, places=6)

    def test_search_and_bbox_filter(self):
        self.assertEqual([f['properties']['parcel_ref'] for f in self.get('?search=g1')['features']], ['G1'])
        self.assertEqual(self.get('?bbox=36.85,-1.31,36.95,-1.28')['count'], 1)

    def test_simplify_drops_vertices(self):
        full = self.get('?search=G1')['features'][0]['geometry']['coordinates'][0]
        simple = self.get('?search=G1&simplify=0.001')['features'][0]['geometry']['coordinates'][0]
        self.assertLess(len(simple), len(full))
