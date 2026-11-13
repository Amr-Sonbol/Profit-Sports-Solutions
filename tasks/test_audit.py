"""The audit trail (spots.audit): who did what, when, from where, and what it changed."""
import json

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token

from people.models import AuditEntry, Technician
from tasks.models import Task
from tasks.tests import TaskTestCase

User = get_user_model()


class AuditTrailTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(user=self.admin, country=self.country, full_name='Amina Admin', language='en',
                                  role=Technician.Role.ADMIN, employment_type='staff')
        AuditEntry.objects.all().delete()  # what the fixture itself made

    def test_a_change_on_the_website_says_who_from_where_and_what(self):
        self.client.login(username='admin1', password='pass12345')
        AuditEntry.objects.all().delete()
        self.site.name = 'Marina Branch'
        page = self.client.get(f'/customers/sites/{self.site.pk}/edit/')
        form = page.context['form']
        data = {name: ('' if form[name].value() is None else form[name].value()) for name in form.fields}
        data['name'] = 'Marina Walk'
        self.client.post(f'/customers/sites/{self.site.pk}/edit/', data, REMOTE_ADDR='10.1.2.3')
        entry = AuditEntry.objects.get(action=AuditEntry.Action.CHANGED, object_type='customers.site')
        self.assertEqual((entry.actor, entry.actor_name, entry.ip), (self.admin, 'Amina Admin', '10.1.2.3'))
        self.assertEqual(entry.changes['name'], ['Marina Branch', 'Marina Walk'])

    def test_a_change_from_the_app_says_who(self):
        self.task_type.category = 'installation'
        self.task_type.save(update_fields=['category'])
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, task_type=self.task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, responsible_supervisor=self.supervisor_user.technician,
        )
        token = Token.objects.create(user=self.supervisor_user).key
        AuditEntry.objects.all().delete()
        saved = self.client.post(f'/api/tasks/{task.pk}/site-readiness/', json.dumps({'power': True}),
                                 content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
                                 HTTP_X_FORWARDED_FOR='203.0.113.7, 10.0.0.1')
        self.assertEqual(saved.status_code, 200, saved.content)
        entry = AuditEntry.objects.get(object_type='tasks.sitereadiness', action=AuditEntry.Action.CREATED)
        self.assertEqual((entry.actor, entry.ip), (self.supervisor_user, '203.0.113.7'))
        self.assertEqual(entry.changes['power'], [None, 'True'])

    def test_sign_ins_and_failed_ones_with_their_address(self):
        self.client.post('/accounts/login/', {'username': 'admin1', 'password': 'wrong'}, REMOTE_ADDR='10.9.9.9')
        failed = AuditEntry.objects.get(action=AuditEntry.Action.SIGN_IN_FAILED)
        self.assertEqual((failed.message, failed.ip, failed.actor), ('admin1', '10.9.9.9', None))
        self.client.post('/accounts/login/', {'username': 'admin1', 'password': 'pass12345'}, REMOTE_ADDR='10.9.9.9')
        self.assertTrue(AuditEntry.objects.filter(action=AuditEntry.Action.SIGNED_IN, actor=self.admin).exists())
        # The app's sign-in too.
        self.client.post('/api/login/', json.dumps({'username': 'tech1', 'password': 'pass12345'}),
                         content_type='application/json')
        self.assertTrue(AuditEntry.objects.filter(action=AuditEntry.Action.SIGNED_IN, actor=self.tech_user,
                                                  message='app').exists())

    def test_a_password_is_never_kept_only_that_it_changed(self):
        self.tech_user.set_password('n3w-Pass-word!')
        self.tech_user.save()
        entry = AuditEntry.objects.get(object_type='auth.user', action=AuditEntry.Action.CHANGED)
        self.assertEqual(entry.changes['password'], ['•', '• (changed)'])
        self.assertNotIn('pbkdf2', json.dumps(entry.changes))

    def test_a_deletion_keeps_what_was_there(self):
        from tasks.models import TaskPartNeeded
        task = Task.objects.create(
            task_number='AE-0002', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        line = TaskPartNeeded.objects.create(task=task, product_code='BELT-01', quantity=2, created_by=self.supervisor_user)
        line.delete()
        entry = AuditEntry.objects.get(action=AuditEntry.Action.DELETED, object_type='tasks.taskpartneeded')
        self.assertEqual(entry.changes['product_code'], ['BELT-01', None])

    def test_the_audit_log_is_searched_and_exported_by_the_admin_only(self):
        self.site.name = 'Marina Walk'
        self.site.save()
        self.client.login(username='admin1', password='pass12345')
        page = self.client.get('/tasks/audit-log/?kind=customers.site&q=Marina')
        self.assertEqual([entry.object_repr for entry in page.context['page_obj']], ['Fitness First — Marina Walk'])
        export = self.client.get('/tasks/audit-log/?kind=customers.site&export=csv')
        self.assertIn('Marina Walk', export.content.decode('utf-8-sig'))
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/audit-log/').status_code, 403)
