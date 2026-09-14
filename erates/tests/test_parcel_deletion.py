from datetime import timedelta

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates.models import Account, Parcel, ParcelDeletionRequest, Payment, User


def make_parcel(ref, **kwargs):
    return Parcel.objects.create(
        parcel_ref=ref,
        geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)),
        county='Nairobi', sub_county='Westlands', ward='karura',
        **kwargs,
    )


class ParcelDeletionTests(APITestCase):
    def setUp(self):
        self.official = User.objects.create_user('official', 'o@example.com', 'Password123!', role='admin')
        self.official.county = 'Nairobi'
        self.official.save()

        self.owner = User.objects.create_user('platformowner', 'p@example.com', 'Password123!', role='owner')

        self.ratepayer = User.objects.create_user('landowner', 'l@example.com', 'Password123!', phone='0712345678')
        self.ratepayer.county = 'Nairobi'
        self.ratepayer.save()

        self.clean = make_parcel('CLEAN-1')
        self.allocated = make_parcel('ALLOC-1', owner_user=self.ratepayer)
        self.billed = make_parcel('BILLED-1', owner_user=self.ratepayer)
        self.paid = make_parcel('PAID-1', owner_user=self.ratepayer)

        account = Account.objects.create(owner_user=self.ratepayer, account_type='main', currency='KES')
        for parcel, state in ((self.billed, 'pending'), (self.paid, 'completed')):
            Payment.objects.create(
                user=self.ratepayer, account=account, parcel=parcel, payment_year=2026,
                amount=4800, status=state, deadline=timezone.now() + timedelta(days=30),
                idempotency_key=f'rates:{parcel.parcel_ref}:2026',
            )

    # --- direct deletion -------------------------------------------------

    def test_official_deletes_a_never_allocated_never_billed_parcel(self):
        self.client.force_authenticate(self.official)
        resp = self.client.delete(f'/api/parcels/{self.clean.parcel_id}/')
        self.assertEqual(resp.status_code, 204)
        self.clean.refresh_from_db()
        self.assertTrue(self.clean.is_deleted)

    def test_allocated_parcel_cannot_be_deleted_directly(self):
        self.client.force_authenticate(self.official)
        resp = self.client.delete(f'/api/parcels/{self.allocated.parcel_id}/')
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()['can_request'])
        self.allocated.refresh_from_db()
        self.assertFalse(self.allocated.is_deleted)

    def test_paid_parcel_can_never_be_deleted(self):
        self.client.force_authenticate(self.official)
        resp = self.client.delete(f'/api/parcels/{self.paid.parcel_id}/')
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(resp.json()['can_request'])

    def test_ratepayers_cannot_delete_parcels(self):
        self.client.force_authenticate(self.ratepayer)
        resp = self.client.delete(f'/api/parcels/{self.clean.parcel_id}/')
        self.assertEqual(resp.status_code, 403)

    # --- escalation ------------------------------------------------------

    def request_deletion(self, parcel, reason='Duplicated by a bad shapefile import'):
        return self.client.post(
            f'/api/parcels/{parcel.parcel_id}/request-deletion/', {'reason': reason}, format='json'
        )

    def test_official_can_escalate_an_allocated_parcel(self):
        self.client.force_authenticate(self.official)
        resp = self.request_deletion(self.allocated)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['status'], 'pending')
        self.assertEqual(resp.json()['parcel_ref'], 'ALLOC-1')

    def test_a_clean_parcel_is_not_escalated(self):
        self.client.force_authenticate(self.official)
        self.assertEqual(self.request_deletion(self.clean).status_code, 409)

    def test_a_paid_parcel_cannot_be_escalated(self):
        self.client.force_authenticate(self.official)
        self.assertEqual(self.request_deletion(self.paid).status_code, 409)

    def test_only_one_open_request_per_parcel(self):
        self.client.force_authenticate(self.official)
        self.assertEqual(self.request_deletion(self.billed).status_code, 201)
        self.assertEqual(self.request_deletion(self.billed).status_code, 409)

    def test_reason_must_be_meaningful(self):
        self.client.force_authenticate(self.official)
        self.assertEqual(self.request_deletion(self.allocated, reason='oops').status_code, 400)

    # --- review ----------------------------------------------------------

    def open_request(self, parcel):
        return ParcelDeletionRequest.objects.create(
            parcel=parcel, requested_by=self.official, reason='Imported twice by mistake',
        )

    def test_owner_approval_removes_the_parcel(self):
        req = self.open_request(self.allocated)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f'/api/parcel-deletion-requests/{req.request_id}/approve/',
                                {'decision_note': 'Confirmed duplicate'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.allocated.refresh_from_db()
        self.assertTrue(self.allocated.is_deleted)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.reviewed_by, self.owner)

    def test_rejection_keeps_the_parcel(self):
        req = self.open_request(self.allocated)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f'/api/parcel-deletion-requests/{req.request_id}/reject/',
                                {'decision_note': 'Plot is genuine'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.allocated.refresh_from_db()
        self.assertFalse(self.allocated.is_deleted)

    def test_officials_cannot_approve_their_own_request(self):
        req = self.open_request(self.allocated)
        self.client.force_authenticate(self.official)
        resp = self.client.post(f'/api/parcel-deletion-requests/{req.request_id}/approve/', {}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_a_request_cannot_be_decided_twice(self):
        req = self.open_request(self.allocated)
        self.client.force_authenticate(self.owner)
        self.client.post(f'/api/parcel-deletion-requests/{req.request_id}/approve/', {}, format='json')
        again = self.client.post(f'/api/parcel-deletion-requests/{req.request_id}/reject/', {}, format='json')
        self.assertEqual(again.status_code, 409)

    def test_payment_landing_while_queued_blocks_approval(self):
        req = self.open_request(self.billed)
        Payment.objects.filter(parcel=self.billed).update(status='completed')
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f'/api/parcel-deletion-requests/{req.request_id}/approve/', {}, format='json')
        self.assertEqual(resp.status_code, 409)
        self.billed.refresh_from_db()
        self.assertFalse(self.billed.is_deleted)

    def test_officials_only_see_their_own_county(self):
        other = make_parcel('OTHER-1', owner_user=self.ratepayer)
        other.county = 'Nyeri'
        other.save()
        self.open_request(self.allocated)
        self.open_request(other)

        self.client.force_authenticate(self.official)
        refs = [r['parcel_ref'] for r in self.client.get('/api/parcel-deletion-requests/').json()['results']]
        self.assertEqual(refs, ['ALLOC-1'])

        self.client.force_authenticate(self.owner)
        owner_refs = {r['parcel_ref'] for r in self.client.get('/api/parcel-deletion-requests/').json()['results']}
        self.assertEqual(owner_refs, {'ALLOC-1', 'OTHER-1'})
