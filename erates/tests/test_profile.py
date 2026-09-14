from rest_framework.test import APITestCase

from erates.models import User


class ProfileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'Password123!', phone='0711111111')
        self.other = User.objects.create_user('bob', 'bob@example.com', 'Password123!', phone='0722222222')
        self.client.force_authenticate(self.user)

    def test_patch_me_updates_email_and_phone_only(self):
        resp = self.client.patch('/api/users/me/', {'email': 'new@example.com', 'phone': '0733 333 333', 'role': 'admin'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.phone, self.user.role), ('new@example.com', '0733333333', 'user'))
        self.assertFalse(self.user.is_verified)

    def test_patch_me_rejects_phone_of_another_account(self):
        resp = self.client.patch('/api/users/me/', {'phone': '0722-222-222'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('phone', resp.json())

    def test_change_password_requires_current_password(self):
        url = '/api/users/change_password/'
        bad = self.client.post(url, {'current_password': 'wrong', 'new_password': 'N3w-Passw0rd!', 'new_password_confirm': 'N3w-Passw0rd!'}, format='json')
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(url, {'current_password': 'Password123!', 'new_password': 'N3w-Passw0rd!', 'new_password_confirm': 'N3w-Passw0rd!'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('N3w-Passw0rd!'))
