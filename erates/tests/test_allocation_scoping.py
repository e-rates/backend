from django.contrib.gis.geos import Polygon
from rest_framework.test import APITestCase

from erates.models import Parcel, User


class AllocationScopingTests(APITestCase):
    """An official must not see or touch another county's plots and ratepayers."""

    def setUp(self):
        self.nyeri_official = User.objects.create_user('nyeri_admin', 'n@example.com', 'Password123!', role='admin', county='Nyeri')
        self.owner = User.objects.create_user('platform', 'p@example.com', 'Password123!', role='owner')
        self.nyeri_ratepayer = User.objects.create_user('kamau', 'kamau@example.com', 'Password123!', county='Nyeri')
        self.kiambu_ratepayer = User.objects.create_user('otieno', 'otieno@example.com', 'Password123!', county='Kiambu')
        self.nyeri_plot = Parcel.objects.create(
            parcel_ref='NY-FREE', geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)),
            county='Nyeri', sub_county='Tetu', ward='karura',
        )
        self.kiambu_plot = Parcel.objects.create(
            parcel_ref='KB-FREE', geom=Polygon.from_bbox((36.9, -1.3, 36.901, -1.299)),
            county='Kiambu', sub_county='Limuru', ward='ndeiya',
        )

    def refs(self, body):
        """unassigned/ is paginated (results), available_for_allocation/ is not (parcels)."""
        rows = body.get('parcels') or body.get('results') or []
        return sorted(p['parcel_ref'] for p in rows)

    def test_unallocated_lists_are_county_scoped(self):
        self.client.force_authenticate(self.nyeri_official)
        self.assertEqual(self.refs(self.client.get('/api/parcels/unassigned/').json()), ['NY-FREE'])
        self.assertEqual(self.refs(self.client.get('/api/parcels/available_for_allocation/').json()), ['NY-FREE'])

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.refs(self.client.get('/api/parcels/unassigned/').json()), ['KB-FREE', 'NY-FREE'])

    def test_land_owner_search_is_county_scoped(self):
        self.client.force_authenticate(self.nyeri_official)
        names = [u['username'] for u in self.client.get('/api/parcels/available_users/').json()['users']]
        self.assertIn('kamau', names)
        self.assertNotIn('otieno', names)

    def test_official_cannot_allocate_another_county_plot(self):
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.post('/api/parcels/allocate_parcel/', {
            'parcel_id': str(self.kiambu_plot.pk), 'user_id': str(self.nyeri_ratepayer.pk),
        }, format='json')
        self.assertIn(resp.status_code, (400, 403, 404), resp.content)
        self.kiambu_plot.refresh_from_db()
        self.assertIsNone(self.kiambu_plot.owner_user)

    def test_official_cannot_allocate_to_another_county_ratepayer(self):
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.post('/api/parcels/allocate_parcel/', {
            'parcel_id': str(self.nyeri_plot.pk), 'user_id': str(self.kiambu_ratepayer.pk),
        }, format='json')
        self.assertIn(resp.status_code, (400, 403, 404), resp.content)
        self.nyeri_plot.refresh_from_db()
        self.assertIsNone(self.nyeri_plot.owner_user)

    def test_official_can_allocate_within_their_county(self):
        self.client.force_authenticate(self.nyeri_official)
        resp = self.client.post('/api/parcels/allocate_parcel/', {
            'parcel_id': str(self.nyeri_plot.pk), 'user_id': str(self.nyeri_ratepayer.pk),
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.nyeri_plot.refresh_from_db()
        self.assertEqual(self.nyeri_plot.owner_user, self.nyeri_ratepayer)
