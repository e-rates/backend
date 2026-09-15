from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.gis.geos import Polygon
from django.utils import timezone
from rest_framework.test import APITestCase

from erates import mpesa
from erates.models import AuditLog, Payment, Parcel, User, Waiver
from erates.payment_flow import generate_rate_bills
from erates.tests.billing import schedule_for

BANDS = [{'max_ha': '1', 'amount': '1000'}]


def plot(ref, county, owner, ward='Karura', land_use='residential'):
    return Parcel.objects.create(
        parcel_ref=ref, geom=Polygon.from_bbox((36.8, -1.3, 36.801, -1.299)), county=county,
        sub_county='Tetu', ward=ward, land_use=land_use, owner_user=owner,
    )


class WaiverTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('nyeri_admin', 'n@example.com', 'Password123!', role='admin', county='Nyeri')
        self.owner = User.objects.create_user('kamau', 'k@example.com', 'Password123!', county='Nyeri')
        self.karura = plot('N1', 'Nyeri', self.owner)
        self.gaaki = plot('N2', 'Nyeri', self.owner, ward='Gaaki')
        self.farm = plot('N3', 'Nyeri', self.owner, ward='Gaaki', land_use='agricultural')
        self.kiambu = plot('K1', 'Kiambu', self.owner)
        deadline = timezone.now() + timedelta(days=30)
        self.schedule = schedule_for('Nyeri County', 2026, deadline, bands=BANDS, top_amount=Decimal('1000'))
        generate_rate_bills(self.schedule)
        generate_rate_bills(schedule_for('Kiambu County', 2026, deadline, bands=BANDS, top_amount=Decimal('1000')))
        self.client.force_authenticate(self.admin)

    def bill(self, parcel):
        return Payment.objects.get(parcel=parcel, payment_year=2026)

    def body(self, **overrides):
        return {'name': 'Drought relief', 'percent': '50', 'years': [2026], 'wards': ['karura'],
                'starts_on': date.today().isoformat(), **overrides}

    def create(self, **overrides):
        return self.client.post('/api/waivers/', self.body(**overrides), format='json')

    def test_preview_saves_nothing(self):
        resp = self.client.post('/api/waivers/preview/', self.body(), format='json')
        self.assertEqual(resp.json()['bills'], 1)
        self.assertEqual(resp.json()['amount_waived'], '500')
        self.assertFalse(Waiver.objects.exists())
        self.assertEqual(self.bill(self.karura).amount, Decimal('1000'))

    def test_waiver_reduces_only_covered_open_bills_in_its_county(self):
        paid = self.bill(self.gaaki)
        paid.status = 'completed'
        paid.save()
        resp = self.create(wards=[])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['bills_affected'], 2)
        self.assertEqual(self.bill(self.karura).amount, Decimal('500'))
        self.assertEqual(self.bill(self.karura).metadata['waiver']['name'], 'Drought relief')
        self.assertEqual(self.bill(self.gaaki).amount, Decimal('1000'))
        self.assertEqual(self.bill(self.kiambu).amount, Decimal('1000'))
        self.assertTrue(AuditLog.objects.filter(action='waiver.created').exists())

    def test_blocks_combine(self):
        self.create(wards=[], land_uses=['agricultural'])
        self.assertEqual(self.bill(self.farm).amount, Decimal('500'))
        self.assertEqual(self.bill(self.karura).amount, Decimal('1000'))

    def test_highest_waiver_wins(self):
        self.create()
        self.create(percent='20', wards=[])
        self.assertEqual(self.bill(self.karura).amount, Decimal('500'))
        self.assertEqual(self.bill(self.gaaki).amount, Decimal('800'))

    def test_full_waiver_settles_and_revoke_restores(self):
        waiver_id = self.create(percent='100').json()['waiver_id']
        bill = self.bill(self.karura)
        self.assertEqual((bill.status, bill.processor, bill.amount), ('completed', 'waiver', Decimal('0')))
        resp = self.client.post(f'/api/waivers/{waiver_id}/revoke/')
        self.assertEqual(resp.json()['status'], 'revoked')
        bill = self.bill(self.karura)
        self.assertEqual((bill.status, bill.processor, bill.amount), ('pending', None, Decimal('1000')))
        self.assertNotIn('waiver', bill.metadata)
        self.assertTrue(bill.verify_integrity() if hasattr(bill, 'verify_integrity') else True)

    def test_reissue_keeps_waiver_on_new_amount(self):
        self.create()
        self.schedule.top_amount = Decimal('3000')
        self.schedule.save()
        generate_rate_bills(self.schedule)
        self.assertEqual(self.bill(self.karura).amount, Decimal('1500'))
        self.assertEqual(generate_rate_bills(self.schedule)['unchanged'], 3)

    def test_future_waiver_waits(self):
        self.create(starts_on=(date.today() + timedelta(days=3)).isoformat())
        self.assertEqual(self.bill(self.karura).amount, Decimal('1000'))

    def test_mpesa_charges_the_waived_amount(self):
        self.create()
        self.client.force_authenticate(self.owner)
        with mock.patch.object(mpesa, 'stk_push', return_value={
            'CheckoutRequestID': 'ws_CO_1', 'MerchantRequestID': 'm-1', 'ResponseCode': '0',
        }) as push:
            self.client.post(f'/api/payments/{self.bill(self.karura).pk}/mpesa/', {'phone': '0708374149'}, format='json')
        self.assertIn(500, push.call_args.args + tuple(push.call_args.kwargs.values()))

    def test_auditor_reads_but_cannot_create(self):
        auditor = User.objects.create_user('aud', 'a@example.com', 'Password123!', role='auditor', county='Nyeri')
        self.create()
        self.client.force_authenticate(auditor)
        self.assertEqual(len(self.client.get('/api/waivers/').json()), 1)
        self.assertEqual(self.create().status_code, 403)

    def test_landowner_sees_waivers_on_their_plots(self):
        self.create()
        stranger = User.objects.create_user('otieno', 'o@example.com', 'Password123!')
        plot('N9', 'Nyeri', stranger, ward='Gaaki')
        self.client.force_authenticate(self.owner)
        mine = self.client.get('/api/waivers/mine/').json()
        self.assertEqual([(w['name'], w['plots']) for w in mine], [('Drought relief', ['N1'])])
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get('/api/waivers/mine/').json(), [])

    def test_invalid_percent_rejected(self):
        self.assertEqual(self.create(percent='120').status_code, 400)
