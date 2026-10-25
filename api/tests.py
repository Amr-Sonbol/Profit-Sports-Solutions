import json
from unittest.mock import patch
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from people.models import RolePermission, Technician
from reports.models import WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskAttachment, TaskEvent, TicketMachine
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
        self.assertTrue(report.resolved)
        self.assertTrue(report.signature_url.startswith('http'))
        self.assertEqual(list(report.parts_used.values_list('part_code', flat=True)), ['BELT-01'])

    def test_the_app_can_email_the_customer_a_copy(self):
        from django.core import mail
        token = self.token_for('tech1')
        self.assertEqual(self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token)).json()['send_copy_to'], '')
        with self.captureOnCommitCallbacks(execute=True):
            response = self._file(token, send_copy_to='ali@example.com')
        self.assertEqual(response.status_code, 201)
        self.assertEqual([m.to for m in mail.outbox], [['ali@example.com']])
        self.assertEqual(WorkReport.objects.get(task=self.task).copy_sent_to, 'ali@example.com')

    def test_the_app_records_each_machine_starting_from_the_ticket(self):
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0801', customer=self.customer, site=self.site,
            company_name='Fitness First', contact_name='Ali', contact_phone='0501234567',
            description='-', submitted_at=timezone.now(), task=self.task, status=CustomerTicket.Status.CONVERTED,
        )
        TicketMachine.objects.create(
            ticket=ticket, asset=self.asset, problem='noise', decision='visit', task=self.task,
        )
        unlisted = TicketMachine.objects.create(
            ticket=ticket, model_name='Old bike', serial_no='OB-9', problem='display', decision='visit',
            task=self.task, position=1,
        )
        token = self.token_for('tech1')
        screen = self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token)).json()
        self.assertEqual([row.get('asset_id') for row in screen['machines']], [self.asset.pk, None])
        self.assertEqual(screen['machines'][1]['ticket_machine_id'], unlisted.pk)
        self.assertIn({'value': 'repaired', 'label': 'Repaired'}, screen['outcomes'])

        response = self._file(token, machines=[
            {'asset_id': self.asset.pk, 'outcome': 'repaired', 'note': 'Belt tightened.'},
            {'brand_id': self.brand.pk, 'model_name': 'Old bike', 'serial_no': 'OB-9', 'outcome': 'replaced',
             'note': 'Console replaced.', 'ticket_machine_id': unlisted.pk},
        ])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            sorted(self.task.task_assets.values_list('note', flat=True)), ['Belt tightened.', 'Console replaced.'],
        )
        unlisted.refresh_from_db()
        self.assertEqual(unlisted.result.outcome, 'replaced')

    def test_a_machine_without_an_outcome_is_refused(self):
        token = self.token_for('tech1')
        response = self._file(token, machines=[{'asset_id': self.asset.pk, 'outcome': '', 'note': ''}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('machines', response.json())

    def test_an_older_app_sending_no_machines_keeps_them(self):
        from tasks.models import TaskAsset
        TaskAsset.objects.create(task=self.task, asset=self.asset, outcome='repaired')
        self.assertEqual(self._file(self.token_for('tech1')).status_code, 201)
        self.assertEqual(self.task.task_assets.count(), 1)

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
        self.assertEqual(str(report.labour_hours), '0.00')  # typed hours are ignored; no Start tap here
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
        self.assertEqual(body['parts_used'][0]['part_code'], 'BELT-01')

    def test_the_hours_come_from_the_start_tap_not_from_typing(self):
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.STARTED,
            occurred_at=timezone.now() - timedelta(hours=2), actor=self.supervisor_user,
        )
        token = self.token_for('tech1')
        self.assertEqual(
            self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token)).json()['time_on_site'],
            '2:00',
        )
        response = self._file(token, labour_hours='')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(WorkReport.objects.get(task=self.task).labour_hours), '2.00')

    def test_a_report_filed_offline_counts_until_it_was_filed(self):
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.STARTED,
            occurred_at=timezone.now() - timedelta(hours=5), actor=self.supervisor_user,
        )
        filed_at = (timezone.now() - timedelta(hours=4)).isoformat()
        self._file(self.token_for('tech1'), filed_at=filed_at)
        self.assertEqual(str(WorkReport.objects.get(task=self.task).labour_hours), '1.00')

    def test_half_filled_part_row_is_rejected(self):
        token = self.token_for('tech1')
        # A part code with no quantity (prices aren't asked for).
        response = self._file(token, parts=[{'part_code': 'BELT-01', 'quantity': ''}])
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

    def test_manager_can_change_the_team_once_work_starts(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])
        token = self.token_for('manager1')
        self.assertTrue(self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()['can_assign'])
        response = self._post(token, 'assign', {'action': 'add_helper', 'technician': self.other_tech.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['helpers'][0]['technician_name'], 'Omar Other')

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


class ReportFixesApiTests(ApiTestCase):
    """Signature waivers, helper hours, and a report from a lead who was
    taken off the task before it reached the server."""

    def setUp(self):
        super().setUp()
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])
        self.task.task_type = self.task_type
        self.task_type.requires_signature = True
        self.task_type.save(update_fields=['requires_signature'])
        self.task.save(update_fields=['task_type'])

    def _file(self, token, **overrides):
        body = {
            'findings': 'Belt worn', 'action_taken': 'Replaced belt', 'resolved': True,
            'labour_hours': '3.00', 'customer_name': 'Ali', 'parts': [], 'signature': None, **overrides,
        }
        return self.client.post(
            f'/api/my-tasks/{self.task.pk}/report/', json.dumps(body),
            content_type='application/json', **self.auth(token),
        )

    def test_no_signature_needs_a_reason(self):
        token = self.token_for('tech1')
        self.assertEqual(self._file(token).status_code, 400)
        response = self._file(token, signature_waived_reason='Gym manager was off site')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.task.report.signature_waived_reason, 'Gym manager was off site')

    def test_lead_sets_a_helpers_hours(self):
        helper_tech = Technician.objects.create(
            user=User.objects.create_user('tech2', password='pass12345'), country=self.country,
            full_name='Omar Helper', language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        helper = TaskAssignment.objects.create(
            task=self.task, technician=helper_tech, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        token = self.token_for('tech1')
        helpers = self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token)).json()['helpers']
        self.assertEqual(helpers[0]['technician_name'], 'Omar Helper')
        self._file(token, signature_waived_reason='Not there', helper_hours={str(helper.pk): '1.5'})
        helper.refresh_from_db()
        self.assertEqual(str(helper.labour_hours), '1.50')

    def test_report_from_a_replaced_lead_goes_to_the_team(self):
        from tasks.models import TaskMessage

        new_lead = Technician.objects.create(
            user=User.objects.create_user('tech2', password='pass12345'), country=self.country,
            full_name='Rami New', language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        token = self.token_for('tech1')
        self.assignment.is_active = False
        self.assignment.ended_at = timezone.now()
        self.assignment.end_reason = 'sick'
        self.assignment.save()
        TaskAssignment.objects.create(
            task=self.task, technician=new_lead, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        response = self._file(token, labour_hours='2.00')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['report'])
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())
        message = TaskMessage.objects.get(task=self.task)
        self.assertIn('Belt worn', message.message)
        self.assertTrue(message.recipients.filter(technician=new_lead).exists())
        self.assignment.refresh_from_db()
        self.assertEqual(str(self.assignment.labour_hours), '2.00')


class TapLocationTests(ApiTestCase):
    """Where the phone was at each tap, checked against the site."""

    MARINA = {'latitude': 25.0772, 'longitude': 55.1306, 'accuracy': 20}
    # About 1.6 km away along the coast.
    JBR = {'latitude': 25.0780, 'longitude': 55.1465, 'accuracy': 20}

    def _act(self, token, action, **extra):
        return self.client.post(
            f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': action, **extra}),
            content_type='application/json', **self.auth(token),
        )

    def _event(self, event_type):
        return self.task.events.get(event_type=event_type)

    def test_first_arrival_sets_the_site_location(self):
        token = self.token_for('tech1')
        self._act(token, 'accept')
        self._act(token, 'en_route', **self.JBR)
        self._act(token, 'arrive', **self.MARINA)
        self.site.refresh_from_db()
        self.assertEqual(float(self.site.latitude), 25.0772)
        self.assertEqual(self.site.location_source, 'arrival')
        self.assertEqual(self._event(TaskEvent.EventType.ARRIVED).location_status, 'at_site')
        # Before the site had a location, "on the way" couldn't be checked.
        self.assertEqual(self._event(TaskEvent.EventType.EN_ROUTE).location_status, 'site_unknown')

    def test_a_tap_far_from_the_site_is_flagged_but_counts(self):
        self.site.latitude, self.site.longitude = 25.0772, 55.1306
        self.site.save()
        token = self.token_for('tech1')
        self._act(token, 'accept')
        self._act(token, 'en_route', **self.JBR)
        response = self._act(token, 'arrive', **self.JBR)
        self.assertEqual(response.status_code, 200)
        arrived = self._event(TaskEvent.EventType.ARRIVED)
        self.assertEqual(arrived.location_status, 'away')
        self.assertGreater(arrived.distance_m, 1000)
        # "On the way" is away from the site by nature — kept, never flagged.
        en_route = self._event(TaskEvent.EventType.EN_ROUTE)
        self.assertEqual(en_route.location_status, '')
        self.assertIsNotNone(en_route.distance_m)

    def test_no_location_is_recorded_as_such(self):
        token = self.token_for('tech1')
        self._act(token, 'accept')
        self.assertEqual(self._event(TaskEvent.EventType.ACCEPTED).location_status, 'no_location')

    def test_report_filed_away_from_the_site_is_flagged(self):
        self.site.latitude, self.site.longitude = 25.0772, 55.1306
        self.site.save()
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])
        token = self.token_for('tech1')
        self.client.post(
            f'/api/my-tasks/{self.task.pk}/report/', json.dumps({
                'findings': 'Belt worn', 'action_taken': '', 'resolved': True, 'labour_hours': '1.00',
                'customer_name': 'Ali', 'parts': [], **self.JBR,
            }), content_type='application/json', **self.auth(token),
        )
        self.assertEqual(self._event(TaskEvent.EventType.REPORT_SUBMITTED).location_status, 'away')
        self.assertTrue(WorkReport.objects.get(task=self.task).has_taps_away_from_site)


class ListSearchApiTests(ApiTestCase):
    """The app's search boxes: ?q= on the team task list and ticket list."""

    def setUp(self):
        super().setUp()
        from tasks.models import CustomerTicket, TaskProduct

        self.done = Task.objects.create(
            task_number='UAE-0099', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.CLOSED, pak_reference_number='PAK-77777',
        )
        TaskProduct.objects.create(task=self.done, product_code='TM-1', serial_number='SER-4242')
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0042', company_name='Iron Temple Gym', site_description='JBR',
            contact_name='Huda', contact_phone='0501112233', description='x', submitted_at=timezone.now(),
            status='closed',
        )
        self.token = self.token_for('supervisor1')

    def _numbers(self, path):
        return [row.get('task_number') or row.get('ticket_number')
                for row in self.client.get(path, **self.auth(self.token)).json()]

    def test_without_a_search_only_open_tasks(self):
        self.assertEqual(self._numbers('/api/tasks/'), ['UAE-0001'])

    def test_search_finds_closed_tasks_by_any_field(self):
        for text in ('0099', 'pak-777', 'SER-4242', 'fitness first', 'marina'):
            with self.subTest(text=text):
                self.assertIn('UAE-0099', self._numbers(f'/api/tasks/?q={text}'))
        self.assertEqual(self._numbers('/api/tasks/?q=nothing-like-this'), [])

    def test_ticket_search_covers_every_status(self):
        Technician.objects.create(
            user=User.objects.create_user('support1', password='pass12345'), country=self.country,
            full_name='Sara Support', language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.token = self.token_for('support1')
        self.assertEqual(self._numbers('/api/tickets/'), [])
        for text in ('T0042', 'iron temple', '1112233', 'huda'):
            with self.subTest(text=text):
                self.assertEqual(self._numbers(f'/api/tickets/?q={text}'), ['AE-T0042'])


class MyPasswordApiTests(ApiTestCase):
    # The app's "choose your password" screen and Profile > Change password
    # (POST /api/me/password/).

    def _post(self, token, body):
        return self.client.post('/api/me/password/', json.dumps(body), content_type='application/json', **self.auth(token))

    def _put_on_a_temporary_password(self):
        self.technician.must_change_password = True
        self.technician.save(update_fields=['must_change_password'])

    def test_sign_in_says_when_the_password_is_temporary(self):
        self._put_on_a_temporary_password()
        response = self.client.post(
            '/api/login/', json.dumps({'username': 'tech1', 'password': 'pass12345'}), content_type='application/json',
        )
        self.assertTrue(response.json()['must_change_password'])

    def test_nothing_else_works_until_they_choose_their_own(self):
        self._put_on_a_temporary_password()
        token = self.token_for('tech1')
        response = self.client.get('/api/my-tasks/', **self.auth(token))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail'], 'Choose your own password first.')
        self.assertEqual(self.client.get('/api/me/', **self.auth(token)).status_code, 200)

    def test_choosing_a_password_needs_no_current_one_and_keeps_the_phone_signed_in(self):
        self._put_on_a_temporary_password()
        token = self.token_for('tech1')
        response = self._post(token, {'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['token'], token)
        self.assertFalse(response.json()['must_change_password'])
        self.assertEqual(self.client.get('/api/my-tasks/', **self.auth(token)).status_code, 200)
        self.tech_user.refresh_from_db()
        self.assertTrue(self.tech_user.check_password('br4nd-New-Pass!'))

    def test_changing_it_later_needs_the_current_password(self):
        token = self.token_for('tech1')
        response = self._post(token, {
            'old_password': 'wrong', 'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('old_password', response.json())
        response = self._post(token, {
            'old_password': 'pass12345', 'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertEqual(response.status_code, 200)

    def test_a_weak_password_comes_back_as_a_field_error(self):
        token = self.token_for('tech1')
        response = self._post(token, {'old_password': 'pass12345', 'new_password1': '1234', 'new_password2': '1234'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('new_password2', response.json())


class AppScheduleAndProgressTests(ApiTestCase):
    # The app's Today, Progress and team progress screens: the website's
    # own plan and figures (tasks.views.today_plan, progress_context,
    # team_progress_rows).

    def test_today_has_the_next_job_with_its_tap_and_directions(self):
        from zoneinfo import ZoneInfo
        dubai = ZoneInfo('Asia/Dubai')
        today = timezone.localtime(timezone.now(), dubai).date()
        self.task.scheduled_for = datetime.combine(today, time(23), dubai)
        self.task.save(update_fields=['scheduled_for'])
        token = self.token_for('tech1')
        plan = self.client.get('/api/my-today/', **self.auth(token)).json()
        self.assertEqual(plan['today_count'], 1)
        self.assertEqual(plan['next_job']['task_number'], 'UAE-0001')
        self.assertEqual(plan['next_job']['next_action'], 'accept')
        self.assertEqual(plan['next_job']['my_role'], 'lead')
        self.assertTrue(plan['next_job']['directions_url'].startswith('https://www.google.com/maps/'))

    def test_progress_has_the_years_line_and_the_month(self):
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=timezone.now(),
            actor=self.tech_user,
        )
        token = self.token_for('tech1')
        progress = self.client.get('/api/my-progress/', **self.auth(token)).json()
        self.assertEqual(progress['trend']['this_month'], 1)
        self.assertEqual(progress['trend']['months'][-1]['completed'], 1)
        self.assertIn('hours', progress['month'])
        self.assertIn('certified', progress['certification'])

    def test_team_progress_is_for_those_who_oversee_a_team(self):
        self.assertEqual(
            self.client.get('/api/team-progress/', **self.auth(self.token_for('tech1'))).status_code, 403,
        )
        rows = self.client.get('/api/team-progress/', **self.auth(self.token_for('supervisor1'))).json()['rows']
        self.assertEqual([row['name'] for row in rows], [self.technician.full_name])
        self.assertTrue(rows[0]['months'])  # their year, month by month


class AppIdentityTests(ApiTestCase):
    # What the app needs to look right: tabs by permission, the profile,
    # and the task screen's contact, directions and machines.

    def test_me_carries_the_roles_permissions_and_profile(self):
        token = self.token_for('tech1')
        me = self.client.get('/api/me/', **self.auth(token)).json()
        self.assertNotIn('manage_tickets', me['permissions'])  # so no Tickets tab
        self.assertEqual(me['username'], 'tech1')
        self.assertIn('photo_url', me)
        supervisor = self.client.get('/api/me/', **self.auth(self.token_for('supervisor1'))).json()
        self.assertIn('view_tasks', supervisor['permissions'])

    def test_the_task_has_its_contact_directions_and_machines(self):
        from tasks.models import CustomerTicket, TicketMachine
        self.site.contact_phone = '+971500000001'
        self.site.save(update_fields=['contact_phone'])
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0990', company_name='Fitness First', contact_name='Ali',
            contact_phone='0501234567', description='-', submitted_at=timezone.now(), task=self.task,
        )
        TicketMachine.objects.create(ticket=ticket, model_name='Old bike', serial_no='OB-9', problem='display', task=self.task)
        detail = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(self.token_for('tech1'))).json()
        self.assertEqual(detail['site_contact_phone'], '+971500000001')
        self.assertTrue(detail['directions_url'].startswith('https://www.google.com/maps/'))
        self.assertEqual(detail['machines'][0]['serial'], 'OB-9')


class AppScheduleTests(ApiTestCase):
    # The app's Week / Month / Year: the website's calendar of one's own
    # tasks, and a supervisor opening a technician's.

    def setUp(self):
        super().setUp()
        from zoneinfo import ZoneInfo
        self.task.scheduled_for = datetime(2026, 9, 9, 10, tzinfo=ZoneInfo('Asia/Dubai'))
        self.task.save(update_fields=['scheduled_for'])

    def _get(self, username, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items())
        return self.client.get(f'/api/my-schedule/?{query}', **self.auth(self.token_for(username)))

    def test_week_month_and_year(self):
        week = self._get('tech1', view='week', start='2026-09-09').json()
        self.assertEqual(len(week['days']), 7)
        busy = [day for day in week['days'] if day['tasks']]
        self.assertEqual(busy[0]['date'], '2026-09-09')
        self.assertEqual(busy[0]['tasks'][0]['my_role'], 'lead')
        self.assertIn('is_weekend', busy[0])

        month = self._get('tech1', view='month', month='2026-09').json()
        self.assertEqual(sum(len(day['tasks']) for day in month['days']), 1)

        year = self._get('tech1', view='year', year='2026').json()
        self.assertEqual([m['count'] for m in year['months']][8], 1)
        self.assertEqual(len(year['months']), 12)

    def test_a_supervisor_opens_a_technicians_schedule_but_not_the_reverse(self):
        response = self._get('supervisor1', view='week', start='2026-09-09', technician=self.technician.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['person']['name'], self.technician.full_name)
        self.assertEqual(
            self._get('tech1', technician=self.supervisor_user.technician.pk).status_code, 403,
        )
        # Someone the supervisor doesn't oversee (another supervisor) isn't found.
        from people.models import Technician
        other = Technician.objects.create(
            user=User.objects.create_user('supervisor2', password='pass12345'), country=self.country,
            full_name='Other Supervisor', language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        self.assertEqual(self._get('supervisor1', technician=other.pk).status_code, 404)


class ReportReviewTests(ApiTestCase):
    # "Review report": the report exactly as it would be sent, with what to
    # fix — and nothing saved, sent or notified.

    def setUp(self):
        super().setUp()
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])

    def test_the_preview_shows_the_report_and_its_problems_and_saves_nothing(self):
        from tasks.models import TaskAsset
        token = self.token_for('tech1')
        with patch('people.push.send_push') as send_push, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f'/api/my-tasks/{self.task.pk}/report/?preview=1', json.dumps({
                    'findings': '', 'action_taken': '', 'resolved': True, 'labour_hours': '1.50',
                    'customer_name': 'Ali', 'parts': [], 'send_copy_to': 'ali@example.com',
                    'machines': [{'asset_id': self.asset.pk, 'outcome': 'repaired', 'note': '', 'action': ''}],
                }), content_type='application/json', **self.auth(token),
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('PREVIEW', body['html'])
        self.assertTrue(any('pick a finding' in problem for problem in body['problems']))
        self.assertIn('Pick the work performed.', body['problems'])
        # Nothing kept, sent or notified.
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())
        self.assertFalse(TaskAsset.objects.filter(task=self.task).exists())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)
        send_push.assert_not_called()
        from django.core import mail
        self.assertEqual(mail.outbox, [])

    def test_typed_text_in_the_wrong_language_is_flagged(self):
        self.country.report_language = 'ar'
        self.country.save(update_fields=['report_language'])
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/report/?preview=1', json.dumps({
                'findings': 'Belt was worn', 'action_taken': '', 'work_lines': ['bolts'], 'resolved': True,
                'labour_hours': '1', 'customer_name': 'Ali', 'parts': [],
            }), content_type='application/json', **self.auth(self.token_for('tech1')),
        )
        problems = response.json()['problems']
        self.assertTrue(any('Belt was worn' in problem for problem in problems), problems)


class MySkillsApiTests(ApiTestCase):
    # The app's My skills: the same figures and rules as the website.

    def setUp(self):
        super().setUp()
        from reference.models import ConductArea, Skill
        self.skill = Skill.objects.create(name='Treadmill repair', name_ar='إصلاح جهاز الجري')
        self.area = ConductArea.objects.create(name='Punctuality', name_ar='الالتزام بالمواعيد')

    def test_rating_a_skill_with_evidence_then_it_waits_for_review(self):
        token = self.token_for('tech1')
        body = self.client.get('/api/my-skills/', **self.auth(token)).json()
        row = next(item for item in body['basic'] if item['id'] == self.skill.pk)
        self.assertEqual((row['status'], row['can_rate']), ('none', True))
        self.assertEqual(len(body['levels']), 4)

        evidence = SimpleUploadedFile('doing-it.jpg', b'jpeg bytes', content_type='image/jpeg')
        response = self.client.post('/api/my-skills/', {
            'kind': 'skill', 'id': self.skill.pk, 'level': '3', 'note': 'belt change', 'evidence': evidence,
        }, **self.auth(token))
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()['basic'] if item['id'] == self.skill.pk)
        self.assertEqual((row['status'], row['level']), ('self', 3))
        self.assertTrue(row['evidence_url'].startswith('http'))
        self.assertEqual(response.json()['progress']['waiting'], 1)

    def test_a_skill_needs_evidence_and_a_reviewed_one_is_locked(self):
        from people.models import TechnicianConduct
        token = self.token_for('tech1')
        response = self.client.post('/api/my-skills/', {'kind': 'skill', 'id': self.skill.pk, 'level': '3'},
                                    **self.auth(token))
        self.assertEqual(response.status_code, 400)
        self.assertIn('evidence', response.json())

        technician = Technician.objects.get(user__username='tech1')
        TechnicianConduct.objects.create(
            technician=technician, conduct_area=self.area, level=2, source=TechnicianConduct.Source.SUPERVISOR,
            set_on=timezone.localdate(), set_by=technician,
        )
        response = self.client.post('/api/my-skills/', {'kind': 'conduct', 'id': self.area.pk, 'level': '4'},
                                    **self.auth(token))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TechnicianConduct.objects.get(technician=technician).level, 2)


class MyProfileApiTests(ApiTestCase):
    # Editing my profile from the app, as on the website's My profile.

    def test_phone_language_email_and_photo_change_and_the_rest_stays(self):
        token = self.token_for('tech1')
        technician = Technician.objects.get(user__username='tech1')
        role, country = technician.role, technician.country_id
        photo = SimpleUploadedFile('me.jpg', b'jpeg bytes', content_type='image/jpeg')
        response = self.client.post('/api/me/', {
            'phone': '+971500000001', 'language': 'ar', 'email': 'tech1@example.com', 'photo': photo,
            'role': 'manager',
        }, **self.auth(token))
        self.assertEqual(response.status_code, 200)
        technician.refresh_from_db()
        self.assertEqual((technician.phone, technician.language, technician.user.email),
                         ('+971500000001', 'ar', 'tech1@example.com'))
        self.assertTrue(response.json()['photo_url'].startswith('http'))
        self.assertEqual((technician.role, technician.country_id), (role, country))

        # Only the phone: the photo and the rest are kept.
        self.client.post('/api/me/', {'phone': '+971500000002'}, **self.auth(token))
        technician.refresh_from_db()
        self.assertEqual((technician.phone, technician.language), ('+971500000002', 'ar'))
        self.assertTrue(technician.photo)

    def test_a_bad_email_is_rejected(self):
        response = self.client.post('/api/me/', {'email': 'not-an-email'}, **self.auth(self.token_for('tech1')))
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.json())


class PersonApiTests(ApiTestCase):
    # A person's page in the app: details, skills to confirm, availability.

    def test_a_supervisor_confirms_a_skill_and_sets_availability(self):
        token = self.token_for('supervisor1')
        page = self.client.get(f'/api/people/{self.technician.pk}/', **self.auth(token)).json()
        self.assertTrue(page['can_review_skills'])
        self.assertTrue(page['can_set_availability'])
        self.assertTrue(any(row['id'] == self.skill.pk for row in page['skills']['basic'] + page['skills']['advanced']))

        response = self.client.post(f'/api/people/{self.technician.pk}/skills/', {
            'kind': 'skill', 'id': self.skill.pk, 'level': '3', 'note': 'seen on site',
        }, **self.auth(token))
        self.assertEqual(response.status_code, 200)
        rows = response.json()['skills']['basic'] + response.json()['skills']['advanced']
        row = next(item for item in rows if item['id'] == self.skill.pk)
        self.assertEqual((row['status'], row['level'], row['can_rate']), ('confirmed', 3, False))

        response = self.client.post(f'/api/people/{self.technician.pk}/availability/', {
            'available': 'false', 'reason': Technician.UnavailableReason.choices[0][0],
        }, **self.auth(token))
        self.assertFalse(response.json()['is_available'])
        self.client.post(f'/api/people/{self.technician.pk}/availability/', {'available': 'true'}, **self.auth(token))
        self.technician.refresh_from_db()
        self.assertTrue(self.technician.is_available)

    def test_a_technician_cannot_open_or_change_someone_else(self):
        other = Technician.objects.get(user__username='supervisor1')
        token = self.token_for('tech1')
        self.assertEqual(self.client.get(f'/api/people/{other.pk}/', **self.auth(token)).status_code, 403)
        response = self.client.post(f'/api/people/{self.technician.pk}/skills/', {
            'kind': 'skill', 'id': self.skill.pk, 'level': '4',
        }, **self.auth(token))
        self.assertEqual(response.status_code, 403)


class WebsiteInTheAppTests(ApiTestCase):
    # The More tab: the website's menu, and its pages opened signed in.

    def test_the_menu_follows_the_website_and_leaves_out_my_own_pages(self):
        groups = self.client.get('/api/website-menu/', **self.auth(self.token_for('supervisor1'))).json()
        urls = [page['url'] for group in groups for page in group['pages']]
        self.assertIn('/tasks/', urls)
        self.assertNotIn('/tasks/my-week/', urls)

    def test_the_token_opens_a_website_session_and_only_this_site(self):
        token = self.token_for('tech1')
        response = self.client.get('/api/website-session/?next=/tasks/my-skills/', **self.auth(token))
        self.assertRedirects(response, '/tasks/my-skills/', fetch_redirect_response=False)
        # Signed in on the website now, with no token.
        page = self.client.get('/tasks/my-skills/')
        self.assertEqual(page.status_code, 200, page.get('Location'))
        response = self.client.get('/api/website-session/?next=https://evil.example/', **self.auth(token))
        self.assertEqual(response['Location'], '/')
        self.assertEqual(self.client.get('/api/website-session/').status_code, 401)


class TaskCreateApiTests(ApiTestCase):
    # New task from the app: the website's form and rules.

    def test_a_supervisor_creates_a_task(self):
        token = self.token_for('supervisor1')
        fields = self.client.get('/api/tasks/new/', **self.auth(token)).json()['fields']
        self.assertIn(str(self.site.pk), [choice['value'] for choice in fields['site']['choices']])
        self.assertEqual(fields['priority']['initial'], Task.Priority.NORMAL)

        response = self.client.post('/api/tasks/new/', json.dumps({
            'site': self.site.pk, 'brand': self.brand.pk, 'description': 'Treadmill belt slipping',
            'priority': fields['priority']['initial'], 'source': fields['source']['initial'],
            'billing_type': fields['billing_type']['initial'], 'reported_at': fields['reported_at']['initial'],
            'is_warranty': False,
        }), content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 201, response.content)
        task = Task.objects.get(pk=response.json()['id'])
        self.assertEqual((task.site, task.status, task.description), (self.site, Task.Status.NEW, 'Treadmill belt slipping'))
        self.assertTrue(task.events.filter(event_type=TaskEvent.EventType.CREATED).exists())

    def test_a_site_is_needed_and_a_technician_cannot_create(self):
        token = self.token_for('supervisor1')
        response = self.client.post('/api/tasks/new/', json.dumps({'description': 'x'}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 400)
        self.assertIn('site', response.json())
        self.assertEqual(self.client.get('/api/tasks/new/', **self.auth(self.token_for('tech1'))).status_code, 403)


class CustomersAndMachinesApiTests(ApiTestCase):
    # Customers and machines in the app, as on the website.

    def test_a_supervisor_finds_a_customer_its_sites_machines_and_history(self):
        token = self.token_for('supervisor1')
        found = self.client.get('/api/customers/?q=fitness', **self.auth(token)).json()
        self.assertEqual([row['id'] for row in found], [self.customer.pk])
        page = self.client.get(f'/api/customers/{self.customer.pk}/', **self.auth(token)).json()
        site = page['sites'][0]
        self.assertEqual(site['name'], self.site.name)
        self.assertTrue(site['directions_url'].startswith('https://www.google.com/maps/'))
        self.assertEqual(site['machines'][0]['id'], self.asset.pk)
        self.assertEqual(page['tasks'][0]['task_number'], self.task.task_number)

        machines = self.client.get('/api/machines/?q=excite', **self.auth(token)).json()
        self.assertEqual([row['id'] for row in machines], [self.asset.pk])
        self.assertEqual(self.client.get(f'/api/machines/{self.asset.pk}/', **self.auth(token)).status_code, 200)

    def test_a_technician_cannot_open_them(self):
        token = self.token_for('tech1')
        self.assertEqual(self.client.get('/api/customers/', **self.auth(token)).status_code, 403)
        self.assertEqual(self.client.get('/api/machines/', **self.auth(token)).status_code, 403)


class HoursApiTests(ApiTestCase):
    def test_the_hours_page_lists_the_team_for_a_month(self):
        body = self.client.get('/api/hours/?month=2026-01', **self.auth(self.token_for('supervisor1'))).json()
        self.assertEqual(body['month'], '2026-01-01')
        self.assertEqual(body['prev_month'], '2025-12-01')
        self.assertIn(self.technician.pk, [row['id'] for row in body['rows']])
        self.assertEqual(self.client.get('/api/hours/', **self.auth(self.token_for('tech1'))).status_code, 403)


class InternalTicketsApiTests(ApiTestCase):
    def test_a_supervisor_sees_the_list_and_a_technician_cannot(self):
        response = self.client.get('/api/internal-tickets/', **self.auth(self.token_for('supervisor1')))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(self.client.get('/api/internal-tickets/', **self.auth(self.token_for('tech1'))).status_code, 403)


class TeamMapApiTests(ApiTestCase):
    def test_the_last_tap_today_is_a_pin(self):
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.ARRIVED, occurred_at=timezone.now(), actor=self.tech_user,
            latitude='25.080000', longitude='55.140000', location_status=TaskEvent.LocationStatus.AT_SITE,
        )
        body = self.client.get('/api/team-map/', **self.auth(self.token_for('supervisor1'))).json()
        self.assertEqual([(pin['name'], pin['task_id']) for pin in body['pins']], [(self.technician.full_name, self.task.pk)])
        self.assertEqual(self.client.get('/api/team-map/', **self.auth(self.token_for('tech1'))).status_code, 403)


class AppUsesTheCountryDayTests(ApiTestCase):
    # At 1 a.m. in Dubai it's still yesterday in UTC: the app's "today"
    # must be the person's country's, like the website's.

    def test_today_just_after_midnight_in_dubai(self):
        from zoneinfo import ZoneInfo
        dubai = ZoneInfo('Asia/Dubai')
        one_am = datetime(2026, 10, 25, 1, 0, tzinfo=dubai)
        self.task.scheduled_for = datetime(2026, 10, 25, 10, 0, tzinfo=dubai)
        self.task.save(update_fields=['scheduled_for'])
        token = self.token_for('tech1')
        with patch('django.utils.timezone.now', return_value=one_am):
            plan = self.client.get('/api/my-today/', **self.auth(token)).json()
        self.assertEqual(plan['today'], '2026-10-25')
        self.assertEqual(plan['today_count'], 1)


class ManagerChoosesTheCountryTests(ApiTestCase):
    # The app's version of the website's country switcher, managers only.

    def setUp(self):
        super().setUp()
        from reference.models import Country
        self.egypt = Country.objects.create(
            name='Egypt', name_ar='مصر', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP', weekend_days='4',
        )
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager', language='en',
            role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.cairo_tech = Technician.objects.create(
            user=User.objects.create_user('cairo1', password='pass12345'), country=self.egypt,
            full_name='Omar Cairo', language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

    def test_a_manager_switches_country_and_sees_its_team(self):
        token = self.token_for('manager1')
        me = self.client.get('/api/me/', **self.auth(token)).json()
        self.assertIn(str(self.egypt.pk), [country['value'] for country in me['countries']])
        home = self.client.get('/api/team-progress/', **self.auth(token)).json()
        self.assertNotIn(self.cairo_tech.pk, [row['id'] for row in home['rows']])
        egypt = self.client.get('/api/team-progress/', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token)).json()
        self.assertIn(self.cairo_tech.pk, [row['id'] for row in egypt['rows']])
        self.assertEqual(
            self.client.get(f'/api/people/{self.cairo_tech.pk}/', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk),
                            **self.auth(token)).status_code, 200,
        )

    def test_a_supervisor_cannot_switch(self):
        token = self.token_for('supervisor1')
        self.assertEqual(self.client.get('/api/me/', **self.auth(token)).json()['countries'], [])
        rows = self.client.get('/api/team-progress/', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token)).json()['rows']
        self.assertNotIn(self.cairo_tech.pk, [row['id'] for row in rows])


class WebsitePagesFollowTheAppCountryTests(ManagerChoosesTheCountryTests):
    def test_a_website_page_opened_in_the_app_shows_the_chosen_country(self):
        from people.permissions import ACTIVE_COUNTRY_SESSION_KEY
        token = self.token_for('manager1')
        self.client.get('/api/website-session/?next=/tasks/', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token))
        self.assertEqual(self.client.session[ACTIVE_COUNTRY_SESSION_KEY], self.egypt.pk)
        self.client.get('/api/website-session/?next=/tasks/', **self.auth(token))
        self.assertNotIn(ACTIVE_COUNTRY_SESSION_KEY, self.client.session)


class DashboardApiTests(ManagerChoosesTheCountryTests):
    # The manager's home screen in the app.

    def test_a_manager_sees_what_needs_them_and_the_open_tasks(self):
        self.task.priority = Task.Priority.EMERGENCY
        self.task.save(update_fields=['priority'])
        body = self.client.get('/api/dashboard/', **self.auth(self.token_for('manager1'))).json()
        self.assertEqual(body['stats']['emergency_count'], 1)
        self.assertEqual(body['tasks'][0]['task_number'], self.task.task_number)
        self.assertIn('Emergencies', [item['label'] for item in body['actions']])

    def test_a_technician_has_no_dashboard(self):
        self.assertEqual(self.client.get('/api/dashboard/', **self.auth(self.token_for('tech1'))).status_code, 403)


class ManagerTaskActionsApiTests(ManagerChoosesTheCountryTests):
    # Cancel, close, flag and correct a tap's time from the app.

    def test_a_manager_corrects_a_tap_cancels_and_a_supervisor_cannot(self):
        token = self.token_for('manager1')
        started = TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.STARTED,
            occurred_at=timezone.now() - timedelta(hours=1), actor=self.tech_user,
        )
        page = self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()
        self.assertTrue(page['manage']['can_cancel'])
        self.assertIn(started.pk, page['manage']['correctable_event_ids'])

        when = timezone.localtime(timezone.now() - timedelta(minutes=50)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(f'/api/tasks/{self.task.pk}/manage/', json.dumps({
            'action': 'correct_time', 'event_id': started.pk, 'corrected_at': when, 'reason': 'Tapped late',
        }), content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 200, response.content)
        started.refresh_from_db()
        self.assertEqual(started.correction_reason, 'Tapped late')

        supervisor = self.token_for('supervisor1')
        response = self.client.post(f'/api/tasks/{self.task.pk}/manage/', json.dumps({'action': 'cancel', 'note': 'x'}),
                                    content_type='application/json', **self.auth(supervisor))
        self.assertEqual(response.status_code, 403)

        response = self.client.post(f'/api/tasks/{self.task.pk}/manage/', json.dumps({
            'action': 'cancel', 'note': 'Customer called it off',
        }), content_type='application/json', **self.auth(token))
        self.assertEqual(response.json()['status'], Task.Status.CANCELLED)
