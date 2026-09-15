from rest_framework.test import APITestCase

from erates import audit
from erates.models import User


class AuditVisibilityTests(APITestCase):
    def setUp(self):
        self.boss = User.objects.create_user('boss', 'b@example.com', 'Password123!', role='owner')
        self.admin = User.objects.create_user('adm', 'a@example.com', 'Password123!', role='admin', county='Nyeri')
        audit.record('auth.login', obj=self.boss, object_type='user', who=self.boss)
        audit.record('auth.login', obj=self.admin, object_type='user', who=self.admin)
        audit.record('county.updated', obj=self.boss, object_type='county', who=self.boss)
        audit.record('waiver.created', obj=self.admin, object_type='waiver', who=self.admin)

    def actions(self, user):
        self.client.force_authenticate(user)
        return [(e['action'], e['who_email']) for e in self.client.get('/api/audit-logs/').json()['results']]

    def test_officials_see_work_events_with_who_but_no_logins_or_owner_activity(self):
        self.assertEqual(self.actions(self.admin), [('waiver.created', 'a@example.com')])

    def test_platform_owner_sees_everything(self):
        self.assertEqual(len(self.actions(self.boss)), 4)
