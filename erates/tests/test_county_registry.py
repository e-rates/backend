from rest_framework.test import APITestCase

from erates.models import County, User


class CountyRegistryTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(User.objects.create_user('boss', 'b@example.com', 'Password123!', role='owner'))

    def test_all_47_counties_are_pre_entered_and_not_onboarded(self):
        self.assertEqual(County.objects.count(), 47)
        self.assertFalse(County.objects.filter(is_active=True).exists())
        self.assertTrue(County.objects.filter(name="Murang'a County").exists())
        self.assertTrue(County.objects.filter(name='Nairobi City County').exists())

    def test_counties_are_onboarded_not_created(self):
        self.assertEqual(self.client.post('/api/counties/', {'name': 'Atlantis'}, format='json').status_code, 405)
        nyeri = County.objects.get(name='Nyeri County')
        self.assertEqual(self.client.delete(f'/api/counties/{nyeri.pk}/').status_code, 405)
        resp = self.client.patch(f'/api/counties/{nyeri.pk}/', {'is_active': True, 'paybill': '174379'}, format='json')
        self.assertEqual(resp.status_code, 200)
        onboarded = self.client.get('/api/counties/?is_active=true').json()['results']
        self.assertEqual([(c['name'], c['paybill']) for c in onboarded], [('Nyeri County', '174379')])
