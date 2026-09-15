from rest_framework.test import APITestCase

from erates.models import User


class SignedDownloadTests(APITestCase):
    def test_signed_link_downloads_without_a_token_and_forged_links_fail(self):
        admin = User.objects.create_user('adm', 'a@example.com', 'Password123!', role='admin', county='Nyeri')
        self.client.force_authenticate(admin)
        url = self.client.get('/api/reports/download-link/?report=collections&file_format=pdf&year=2026').json()['url']
        self.client.force_authenticate(None)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertIn('inline', resp['Content-Disposition'])
        self.assertEqual(self.client.get(url.replace('sig=', 'sig=x')).status_code, 403)
        self.assertEqual(self.client.get('/api/reports/download/?report=collections&file_format=pdf').status_code, 403)
