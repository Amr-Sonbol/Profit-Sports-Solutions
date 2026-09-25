import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from people.models import RolePermission, Technician
from tasks.models import Task, TaskAssignment, TaskAttachment, TaskEvent
from tasks.tests import TaskTestCase

User = get_user_model()


class ApiTestCase(TaskTestCase):
    """TaskTestCase's fixtures, plus a task assigned to tech1 as lead —
    what almost every API test needs.
    """

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='UAE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
        )
        self.assignment = TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

    def token_for(self, username, password='pass12345'):
        response = self.client.post(
            '/api/login/', json.dumps({'username': username, 'password': password}),
            content_type='application/json',
        )
        return response.json()['token']

    def auth(self, token):
        return {'HTTP_AUTHORIZATION': f'Token {token}'}


class LoginTests(ApiTestCase):
    def test_valid_credentials_return_a_token(self):
        response = self.client.post(
            '/api/login/', json.dumps({'username': 'tech1', 'password': 'pass12345'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.json())
        self.assertEqual(response.json()['role'], 'technician')

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            '/api/login/', json.dumps({'username': 'tech1', 'password': 'wrong'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_me_requires_a_token(self):
        response = self.client.get('/api/me/')
        self.assertEqual(response.status_code, 401)

    def test_me_returns_the_technician_profile(self):
        token = self.token_for('tech1')
        response = self.client.get('/api/me/', **self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['full_name'], 'Tarek Tech')


class MyTasksTests(ApiTestCase):
    def test_technician_sees_their_own_task(self):
        token = self.token_for('tech1')
        response = self.client.get('/api/my-tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]['task_number'], 'UAE-0001')

    def test_someone_elses_task_is_not_listed(self):
        other_user = User.objects.create_user('helper1', password='pass12345')
        Technician.objects.create(
            user=other_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        token = self.token_for('helper1')
        response = self.client.get('/api/my-tasks/', **self.auth(token))
        self.assertEqual(response.json(), [])

    def test_detail_shows_next_action(self):
        token = self.token_for('tech1')
        response = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(token))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['next_action'], 'accept')
        self.assertTrue(body['is_lead'])

    def test_detail_404s_for_a_task_not_assigned_to_you(self):
        other_user = User.objects.create_user('helper1', password='pass12345')
        Technician.objects.create(
            user=other_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        token = self.token_for('helper1')
        response = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(token))
        self.assertEqual(response.status_code, 404)


class MyTaskActionTests(ApiTestCase):
    def _act(self, token, action, **extra):
        return self.client.post(
            f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': action, **extra}),
            content_type='application/json', **self.auth(token),
        )

    def test_full_accept_to_start_sequence(self):
        token = self.token_for('tech1')

        response = self._act(token, 'accept')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'accepted')

        self._act(token, 'en_route')
        self._act(token, 'arrive')
        response = self._act(token, 'start')
        self.assertEqual(response.json()['status'], 'in_progress')

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)
        self.assertEqual(
            TaskEvent.objects.filter(task=self.task).count(), 4,
        )

    def test_wrong_action_for_current_state_is_rejected(self):
        token = self.token_for('tech1')
        response = self._act(token, 'start')
        self.assertEqual(response.status_code, 400)

    def test_helper_cannot_act(self):
        helper_user = User.objects.create_user('helper1', password='pass12345')
        helper = Technician.objects.create(
            user=helper_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=self.task, technician=helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        token = self.token_for('helper1')
        response = self._act(token, 'accept')
        self.assertEqual(response.status_code, 403)

    def test_block_requires_a_note(self):
        token = self.token_for('tech1')
        self._act(token, 'accept')
        response = self._act(token, 'block')
        self.assertEqual(response.status_code, 400)

    def test_block_with_a_reason_works(self):
        token = self.token_for('tech1')
        self._act(token, 'accept')
        response = self._act(token, 'block', note='No spare part in stock')
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.BLOCKED)


class MyTaskAttachmentTests(ApiTestCase):
    def test_upload_creates_an_attachment(self):
        token = self.token_for('tech1')
        photo = SimpleUploadedFile('fault.jpg', b'not a real image', content_type='image/jpeg')
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/attachments/', {'file': photo, 'purpose': 'fault'},
            **self.auth(token),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TaskAttachment.objects.filter(task=self.task).count(), 1)

    def test_disallowed_extension_is_rejected(self):
        token = self.token_for('tech1')
        bad = SimpleUploadedFile('malware.exe', b'x', content_type='application/octet-stream')
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/attachments/', {'file': bad, 'purpose': 'fault'},
            **self.auth(token),
        )
        self.assertEqual(response.status_code, 400)


class TaskListTests(ApiTestCase):
    def test_supervisor_sees_tasks_in_their_country(self):
        token = self.token_for('supervisor1')
        response = self.client.get('/api/tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_technician_is_forbidden(self):
        token = self.token_for('tech1')
        response = self.client.get('/api/tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 403)

    def test_disabling_view_tasks_takes_effect_immediately(self):
        token = self.token_for('supervisor1')
        RolePermission.objects.filter(
            role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS,
        ).update(allowed=False)
        response = self.client.get('/api/tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 403)


class TicketListTests(ApiTestCase):
    def test_technician_is_forbidden(self):
        token = self.token_for('tech1')
        response = self.client.get('/api/tickets/', **self.auth(token))
        self.assertEqual(response.status_code, 403)

    def test_supervisor_can_list_tickets(self):
        token = self.token_for('supervisor1')
        response = self.client.get('/api/tickets/', **self.auth(token))
        self.assertEqual(response.status_code, 200)


class ApiSecurityTests(ApiTestCase):
    """Auth and isolation edge cases — every endpoint here handles real
    customer data, so these lock in the boundaries that matter: no token,
    a bad token, someone else's data, a deactivated account, and the same
    country-scoping the web app relies on.
    """

    def test_every_endpoint_requires_a_token(self):
        for url in [
            '/api/me/', '/api/my-tasks/', f'/api/my-tasks/{self.task.pk}/', '/api/tasks/', '/api/tickets/',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 401, url)

    def test_garbage_token_is_rejected(self):
        response = self.client.get('/api/me/', HTTP_AUTHORIZATION='Token not-a-real-token')
        self.assertEqual(response.status_code, 401)

    def test_malformed_authorization_header_is_rejected(self):
        response = self.client.get('/api/me/', HTTP_AUTHORIZATION='not-the-right-format')
        self.assertEqual(response.status_code, 401)

    def test_action_requires_a_token(self):
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': 'accept'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_attachment_upload_requires_a_token(self):
        photo = SimpleUploadedFile('fault.jpg', b'not a real image', content_type='image/jpeg')
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/attachments/', {'file': photo, 'purpose': 'fault'})
        self.assertEqual(response.status_code, 401)

    def test_deactivated_users_token_is_rejected(self):
        token = self.token_for('tech1')
        self.tech_user.is_active = False
        self.tech_user.save(update_fields=['is_active'])
        response = self.client.get('/api/me/', **self.auth(token))
        self.assertEqual(response.status_code, 401)

    def test_a_customer_login_has_no_technician_access(self):
        """A customer-portal account is a real User too — it must not be
        able to reach any technician/staff endpoint just by having a token.
        """
        customer_user = User.objects.create_user('customer1', password='pass12345')
        token = self.client.post(
            '/api/login/', json.dumps({'username': 'customer1', 'password': 'pass12345'}),
            content_type='application/json',
        ).json()['token']
        response = self.client.get('/api/my-tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 403)

    def test_helper_can_upload_attachments(self):
        """Only status actions (action/) are lead-only, same as the web
        form — a helper attaching a photo is normal, both places.
        """
        helper_user = User.objects.create_user('helper1', password='pass12345')
        helper = Technician.objects.create(
            user=helper_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=self.task, technician=helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        token = self.token_for('helper1')
        photo = SimpleUploadedFile('fault.jpg', b'not a real image', content_type='image/jpeg')
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/attachments/', {'file': photo, 'purpose': 'fault'}, **self.auth(token),
        )
        # Allowed at the model level (any active assignee), same as the web
        # form — only *acting on status* (action/) is lead-only.
        self.assertEqual(response.status_code, 201)

    def test_supervisor_in_another_country_sees_nothing(self):
        from reference.models import Country

        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('supervisor2', password='pass12345')
        Technician.objects.create(
            user=other_user, country=other_country, full_name='Samir Supervisor',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        token = self.token_for('supervisor2')
        response = self.client.get('/api/tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_ticket_from_another_country_is_not_visible(self):
        from reference.models import Country
        from tasks.models import CustomerTicket

        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        CustomerTicket.objects.create(
            country=other_country, company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Sara', contact_phone='0501234567', description='Broken treadmill',
            submitted_at=timezone.now(), status='new',
        )
        token = self.token_for('supervisor1')
        response = self.client.get('/api/tickets/', **self.auth(token))
        self.assertEqual(response.json(), [])

    def test_login_endpoint_is_rate_limited_by_axes(self):
        from django.test import override_settings

        with override_settings(AXES_ENABLED=True):
            for _ in range(5):
                self.client.post(
                    '/api/login/', json.dumps({'username': 'tech1', 'password': 'wrong'}),
                    content_type='application/json',
                )
            response = self.client.post(
                '/api/login/', json.dumps({'username': 'tech1', 'password': 'pass12345'}),
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 429)
