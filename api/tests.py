import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from people.models import RolePermission, Technician
from reports.models import WorkReport
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


# A 1x1 transparent PNG — enough to stand in for a drawn signature.
SIGNATURE_DATA_URL = (
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
)


class MyTaskReportTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])

    def _file(self, token, **overrides):
        body = {
            'findings': 'Belt worn through', 'action_taken': 'Replaced belt', 'resolved': True,
            'labour_hours': '2.50', 'customer_name': 'Ali Manager',
            'parts': [{
                'part_code': 'BELT-01', 'description': 'Treadmill belt', 'quantity': 1,
                'unit_cost': '120.00', 'currency_code': 'AED',
            }],
            'signature': SIGNATURE_DATA_URL,
            **overrides,
        }
        return self.client.post(
            f'/api/my-tasks/{self.task.pk}/report/', json.dumps(body),
            content_type='application/json', **self.auth(token),
        )

    def test_filing_saves_report_parts_and_signature(self):
        token = self.token_for('tech1')
        response = self._file(token)
        self.assertEqual(response.status_code, 201)
        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(str(report.labour_hours), '2.50')
        self.assertTrue(report.resolved)
        self.assertTrue(report.signature_url.startswith('http'))
        self.assertEqual(list(report.parts_used.values_list('part_code', flat=True)), ['BELT-01'])

    def test_technicians_report_goes_to_supervisor_review(self):
        token = self.token_for('tech1')
        response = self._file(token)
        self.assertEqual(response.json()['status'], Task.Status.PENDING_SUPERVISOR_REVIEW)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.PENDING_SUPERVISOR_REVIEW)
        self.assertTrue(TaskEvent.objects.filter(task=self.task, event_type=TaskEvent.EventType.COMPLETED).exists())

    def test_correction_keeps_status_and_signature(self):
        token = self.token_for('tech1')
        self._file(token)
        first_signature = WorkReport.objects.get(task=self.task).signature_url
        response = self._file(token, labour_hours='3.00', signature=None, parts=[])
        self.assertEqual(response.status_code, 200)
        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(str(report.labour_hours), '3.00')
        self.assertEqual(report.signature_url, first_signature)
        self.assertFalse(report.parts_used.exists())
        self.assertEqual(
            TaskEvent.objects.filter(task=self.task, event_type=TaskEvent.EventType.COMPLETED).count(), 1,
        )

    def test_get_returns_saved_report(self):
        token = self.token_for('tech1')
        response = self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token))
        self.assertIsNone(response.json()['report'])
        self._file(token)
        body = self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token)).json()['report']
        self.assertEqual(body['labour_hours'], '2.50')
        self.assertEqual(body['parts_used'][0]['part_code'], 'BELT-01')

    def test_missing_labour_hours_is_rejected(self):
        token = self.token_for('tech1')
        response = self._file(token, labour_hours='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('labour_hours', response.json())
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_half_filled_part_row_is_rejected(self):
        token = self.token_for('tech1')
        response = self._file(token, parts=[{'part_code': 'BELT-01', 'quantity': 1}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('parts', response.json())
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_unreadable_signature_is_rejected(self):
        token = self.token_for('tech1')
        response = self._file(token, signature='data:image/png;base64,@@@not-base64@@@')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_task_type_requiring_signature_needs_one(self):
        self.task_type.requires_signature = True
        self.task_type.save(update_fields=['requires_signature'])
        self.task.task_type = self.task_type
        self.task.save(update_fields=['task_type'])
        token = self.token_for('tech1')

        detail = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(token)).json()
        self.assertTrue(detail['requires_signature'])

        response = self._file(token, signature=None)
        self.assertEqual(response.status_code, 400)
        self.assertIn('signature', response.json())
        self.assertEqual(self._file(token).status_code, 201)

    def test_cannot_change_once_closed(self):
        token = self.token_for('tech1')
        self._file(token)
        self.task.status = Task.Status.CLOSED
        self.task.save(update_fields=['status'])
        response = self._file(token, labour_hours='9.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('closed', response.json()['detail'])

    def test_cannot_file_before_work_starts(self):
        self.task.status = Task.Status.ACCEPTED
        self.task.save(update_fields=['status'])
        token = self.token_for('tech1')
        self.assertEqual(self._file(token).status_code, 400)

    def test_cannot_file_while_paused(self):
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.PAUSED, occurred_at=timezone.now(),
            actor=self.tech_user, note='Waiting for part',
        )
        token = self.token_for('tech1')
        self.assertEqual(self._file(token).status_code, 400)

    def test_helper_cannot_file(self):
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
        self.assertEqual(self._file(token).status_code, 403)

    def test_requires_a_token(self):
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/report/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 401)


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

    def test_supervisor_is_forbidden(self):
        # manage_tickets moved to the technical support manager role —
        # a plain supervisor has no ticket access at all now.
        token = self.token_for('supervisor1')
        response = self.client.get('/api/tickets/', **self.auth(token))
        self.assertEqual(response.status_code, 403)

    def test_support_manager_can_list_tickets(self):
        support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=support_user, country=self.country, full_name='Sara Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        token = self.token_for('support1')
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
        support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=support_user, country=self.country, full_name='Sara Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        token = self.token_for('support1')
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


class TeamTaskApiTests(ApiTestCase):
    """The supervisor/manager endpoints: task detail, candidates, assign, approve."""

    def setUp(self):
        super().setUp()
        self.supervisor = self.supervisor_user.technician
        self.other_user = User.objects.create_user('tech2', password='pass12345')
        self.other_tech = Technician.objects.create(
            user=self.other_user, country=self.country, full_name='Omar Other',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

    def _post(self, token, path, body):
        return self.client.post(
            f'/api/tasks/{self.task.pk}/{path}/', json.dumps(body), content_type='application/json',
            **self.auth(token),
        )

    def test_supervisor_sees_task_with_team_and_permissions(self):
        token = self.token_for('supervisor1')
        body = self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()
        self.assertEqual(body['lead']['technician_name'], 'Tarek Tech')
        self.assertEqual(body['helpers'], [])
        self.assertTrue(body['can_assign'])
        self.assertFalse(body['can_manager_approve'])

    def test_technician_cannot_use_team_endpoints(self):
        token = self.token_for('tech1')
        self.assertEqual(self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).status_code, 403)
        self.assertEqual(self._post(token, 'assign', {'action': 'add_helper'}).status_code, 403)

    def test_candidates_exclude_people_already_on_the_task(self):
        token = self.token_for('supervisor1')
        names = [c['full_name'] for c in self.client.get(
            f'/api/tasks/{self.task.pk}/candidates/', **self.auth(token)).json()]
        self.assertIn('Omar Other', names)
        self.assertNotIn('Tarek Tech', names)

    def test_add_and_remove_helper(self):
        token = self.token_for('supervisor1')
        body = self._post(token, 'assign', {'action': 'add_helper', 'technician': self.other_tech.pk}).json()
        self.assertEqual(body['helpers'][0]['technician_name'], 'Omar Other')

        body = self._post(token, 'assign', {
            'action': 'remove_helper', 'assignment_id': body['helpers'][0]['id'], 'end_reason': 'overloaded',
        }).json()
        self.assertEqual(body['helpers'], [])

    def test_replacing_the_lead_needs_a_reason(self):
        token = self.token_for('supervisor1')
        response = self._post(token, 'assign', {'action': 'set_lead', 'technician': self.other_tech.pk})
        self.assertEqual(response.status_code, 400)
        response = self._post(token, 'assign', {
            'action': 'set_lead', 'technician': self.other_tech.pk, 'end_reason': 'sick',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['lead']['technician_name'], 'Omar Other')
        self.assertTrue(TaskEvent.objects.filter(task=self.task, event_type=TaskEvent.EventType.REASSIGNED).exists())

    def test_team_is_locked_once_work_starts(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])
        token = self.token_for('supervisor1')
        response = self._post(token, 'assign', {'action': 'add_helper', 'technician': self.other_tech.pk})
        self.assertEqual(response.status_code, 400)

    def test_another_supervisors_task_is_off_limits(self):
        other_sup_user = User.objects.create_user('supervisor2', password='pass12345')
        other_sup = Technician.objects.create(
            user=other_sup_user, country=self.country, full_name='Samir Super',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        self.task.responsible_supervisor = other_sup
        self.task.save(update_fields=['responsible_supervisor'])
        token = self.token_for('supervisor1')
        self.assertFalse(self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()['can_assign'])
        response = self._post(token, 'assign', {'action': 'add_helper', 'technician': self.other_tech.pk})
        self.assertEqual(response.status_code, 403)

    def test_two_step_approval(self):
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.responsible_supervisor = self.supervisor
        self.task.save(update_fields=['status', 'responsible_supervisor'])

        supervisor_token = self.token_for('supervisor1')
        body = self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(supervisor_token)).json()
        self.assertTrue(body['can_supervisor_approve'])
        response = self._post(supervisor_token, 'approve', {})
        self.assertEqual(response.json()['status'], Task.Status.COMPLETED)

        # The supervisor can't do the manager's step.
        self.assertEqual(self._post(supervisor_token, 'approve', {}).status_code, 403)

        manager_token = self.token_for('manager1')
        response = self._post(manager_token, 'approve', {})
        self.assertEqual(response.json()['status'], Task.Status.CLOSED)
        self.assertEqual(self._post(manager_token, 'approve', {}).status_code, 400)

    def test_other_country_task_is_404_for_a_supervisor(self):
        from reference.models import Country

        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        self.customer.country = other_country
        self.customer.save(update_fields=['country'])
        token = self.token_for('supervisor1')
        self.assertEqual(self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).status_code, 404)


class MyTaskUndoApiTests(ApiTestCase):
    def test_undo_through_the_api(self):
        token = self.token_for('tech1')
        self.client.post(
            f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': 'accept'}),
            content_type='application/json', **self.auth(token),
        )
        detail = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(token)).json()
        self.assertEqual(detail['undoable_step'], 'Accepted')

        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': 'undo'}),
            content_type='application/json', **self.auth(token),
        )
        self.assertEqual(response.json()['status'], Task.Status.ASSIGNED)
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': 'undo'}),
            content_type='application/json', **self.auth(token),
        )
        self.assertEqual(response.status_code, 400)


class PartListApiTests(ApiTestCase):
    def test_lists_active_parts_only(self):
        from reference.models import Part

        Part.objects.create(code='BELT-01', description='Treadmill belt')
        Part.objects.create(code='OLD-9', is_active=False)
        token = self.token_for('tech1')
        response = self.client.get('/api/parts/', **self.auth(token))
        self.assertEqual(response.json(), [{'code': 'BELT-01', 'description': 'Treadmill belt'}])


class PushDeviceTests(ApiTestCase):
    """Registering phones, and pushes going out (Expo's service mocked)."""

    TOKEN = 'ExponentPushToken[abc123]'

    def _register(self, token, username='tech1'):
        return self.client.post(
            '/api/push-device/', json.dumps({'token': token}), content_type='application/json',
            **self.auth(self.token_for(username)),
        )

    def _expo_reply(self, mock_urlopen, tickets):
        from unittest.mock import MagicMock

        response = MagicMock()
        response.read.return_value = json.dumps({'data': tickets}).encode()
        mock_urlopen.return_value.__enter__.return_value = response

    def test_register_and_unregister(self):
        from people.models import PushDevice

        self.assertEqual(self._register(self.TOKEN).status_code, 204)
        self.assertEqual(PushDevice.objects.get().technician, self.technician)
        self.assertEqual(self._register('not-a-token').status_code, 400)

        self.client.delete(
            '/api/push-device/', json.dumps({'token': self.TOKEN}), content_type='application/json',
            **self.auth(self.token_for('tech1')),
        )
        self.assertFalse(PushDevice.objects.exists())

    def test_a_phone_signing_in_as_someone_else_moves_to_them(self):
        from people.models import PushDevice

        self._register(self.TOKEN)
        self._register(self.TOKEN, username='supervisor1')
        self.assertEqual(PushDevice.objects.get().technician.user.username, 'supervisor1')

    def test_assigning_a_lead_pushes_to_their_phone(self):
        from unittest.mock import patch

        from tasks.views import _set_lead

        self._register(self.TOKEN)
        with patch('people.push.urlopen') as mock_urlopen:
            self._expo_reply(mock_urlopen, [{'status': 'ok'}])
            _set_lead(self.task, self.assignment, self.technician, 'other', self.supervisor_user)
        sent = json.loads(mock_urlopen.call_args.args[0].data)
        self.assertEqual(sent[0]['to'], self.TOKEN)
        self.assertEqual(sent[0]['data'], {'type': 'my_task', 'task_id': self.task.pk})

    def test_nothing_is_sent_without_a_registered_phone(self):
        from unittest.mock import patch

        from tasks.views import _set_lead

        with patch('people.push.urlopen') as mock_urlopen:
            _set_lead(self.task, self.assignment, self.technician, 'other', self.supervisor_user)
        mock_urlopen.assert_not_called()

    def test_unregistered_phones_are_forgotten(self):
        from unittest.mock import patch

        from people.models import PushDevice
        from people.push import send_push

        self._register(self.TOKEN)
        with patch('people.push.urlopen') as mock_urlopen:
            self._expo_reply(mock_urlopen, [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}])
            send_push([self.technician], 'x', 'y')
        self.assertFalse(PushDevice.objects.exists())

    def test_a_failed_push_never_breaks_the_action(self):
        from unittest.mock import patch

        from tasks.views import _set_lead

        self._register(self.TOKEN)
        with patch('people.push.urlopen', side_effect=OSError('no network')):
            _set_lead(self.task, self.assignment, self.technician, 'other', self.supervisor_user)
        self.assertTrue(self.task.assignments.filter(technician=self.technician, is_active=True).exists())
