import json
from unittest.mock import patch
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from customers.models import Asset
from people.models import RolePermission, Technician
from reports.models import WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskAttachment, TaskEvent, TicketMachine
from tasks.tests import TaskTestCase
from spots.test_files import REAL_JPEG

User = get_user_model()



def give_machine_photos(task, asset, user):
    """A machine's serial plate, before and after on this task — what a report needs before it's sent."""
    from tasks.models import TaskAttachment
    for purpose in ('serial_plate', 'before', 'after'):
        TaskAttachment.objects.create(
            task=task, storage_kind='file', url=f'http://testserver/media/{purpose}.jpg', media_type='photo',
            purpose=purpose, source='technician', uploaded_by=user, uploaded_at=timezone.now(), asset=asset,
        )

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
            # Every task has its owner (docs/access_rules.md): the supervisor who created it.
            responsible_supervisor=self.supervisor_user.technician,
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


class SignInExpiryTests(ApiTestCase):
    """An app sign-in ends after 30 days unused (api.authentication.TOKEN_IDLE_DAYS);
    each use renews it, so someone using the app never notices."""

    def age(self, token, days):
        from rest_framework.authtoken.models import Token
        Token.objects.filter(key=token).update(created=timezone.now() - timezone.timedelta(days=days))

    def test_used_within_30_days_it_works_and_is_renewed(self):
        from rest_framework.authtoken.models import Token
        token = self.token_for('tech1')
        self.age(token, 29)
        self.assertEqual(self.client.get('/api/me/', **self.auth(token)).status_code, 200)
        self.assertLess(timezone.now() - Token.objects.get(key=token).created, timezone.timedelta(minutes=1))

    def test_unused_for_30_days_every_phone_is_signed_out(self):
        from people.models import PushDevice
        from rest_framework.authtoken.models import Token
        token = self.token_for('tech1')
        PushDevice.objects.create(technician=self.technician, token='ExponentPushToken[lost-phone]',
                                  created_at=timezone.now())
        self.age(token, 31)
        response = self.client.get('/api/me/', **self.auth(token))
        self.assertEqual(response.status_code, 401)
        self.assertIn('30 days', response.json()['detail'])
        self.assertFalse(Token.objects.filter(key=token).exists())
        self.assertFalse(PushDevice.objects.filter(technician=self.technician).exists())  # no more alerts there
        # Signing in again works, with a fresh 30 days.
        fresh = self.token_for('tech1')
        self.assertEqual(self.client.get('/api/me/', **self.auth(fresh)).status_code, 200)

    def test_signing_in_renews_an_old_unused_token(self):
        token = self.token_for('tech1')
        self.age(token, 40)  # never asked for since: still there
        again = self.token_for('tech1')
        self.assertEqual(self.client.get('/api/me/', **self.auth(again)).status_code, 200)


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
        photo = SimpleUploadedFile('fault.jpg', REAL_JPEG, content_type='image/jpeg')
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/attachments/', {'file': photo, 'purpose': 'fault'},
            **self.auth(token),
        )
        self.assertEqual(response.status_code, 201)
        # "fault" — from an older app, or queued offline — is a Before photo now.
        self.assertEqual(list(TaskAttachment.objects.filter(task=self.task).values_list('purpose', flat=True)), ['before'])

    def test_disallowed_extension_is_rejected(self):
        token = self.token_for('tech1')
        bad = SimpleUploadedFile('malware.exe', b'x', content_type='application/octet-stream')
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/attachments/', {'file': bad, 'purpose': 'before'},
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

        # Not sent without every machine's serial plate, before and after — the unlisted one saved first.
        response = self._file(token, machines=[
            {'asset_id': self.asset.pk, 'outcome': 'repaired', 'note': 'Belt tightened.'},
            {'brand_id': self.brand.pk, 'model_name': 'Old bike', 'serial_no': 'OB-9', 'outcome': 'replaced',
             'note': 'Console replaced.', 'ticket_machine_id': unlisted.pk},
        ])
        self.assertEqual(response.status_code, 400)
        gaps = response.json()['photos']
        self.assertEqual(len(gaps), 2)
        self.assertIn('Serial plate, Before, After', gaps[0])
        self.assertIn('Old bike', gaps[1])
        # Saved now (pending until the report records it), so its photos can be taken.
        saved = self.client.post(f'/api/my-tasks/{self.task.pk}/machines/', {
            'brand': self.brand.pk, 'model_name': 'Old bike', 'serial_no': 'OB-9', 'ticket_machine': unlisted.pk,
        }, **self.auth(token))
        self.assertEqual(saved.status_code, 201, saved.content)
        bike = Asset.objects.get(pk=saved.json()['asset_id'])
        self.assertEqual((bike.status, bike.registered_on_task), (Asset.Status.PENDING, self.task))
        unlisted.refresh_from_db()
        self.assertEqual(unlisted.asset, bike)
        for asset in (self.asset, bike):
            give_machine_photos(self.task, asset, self.tech_user)
        response = self._file(token, machines=[
            {'asset_id': self.asset.pk, 'outcome': 'repaired', 'note': 'Belt tightened.'},
            {'asset_id': bike.pk, 'outcome': 'replaced', 'note': 'Console replaced.'},
        ])
        self.assertEqual(response.status_code, 201)
        bike.refresh_from_db()
        self.assertEqual(bike.status, Asset.Status.ACTIVE)  # one of ours once the report records it
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


class WaitingForMeTests(ApiTestCase):
    """Tasks → Waiting for me and the Dashboard's top: one item per decision, for whoever makes it."""

    def waiting(self, username):
        return self.client.get('/api/waiting/', **self.auth(self.token_for(username))).json()

    def test_a_report_waits_on_its_supervisor_then_on_the_technical_support_manager(self):
        manager = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(user=manager, country=self.country, full_name='Mona Manager', language='en',
                                  role=Technician.Role.SUPPORT_MANAGER, employment_type='staff')
        self.task.responsible_supervisor = Technician.objects.get(user__username='supervisor1')
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save()
        self.assertEqual([item['kind'] for item in self.waiting('supervisor1')], ['review_report'])
        self.assertEqual(self.waiting('tech1'), [])
        self.task.status = Task.Status.COMPLETED
        self.task.save()
        item = self.waiting('manager1')[0]
        self.assertEqual((item['kind'], item['opens']), ('approve_report', {'screen': 'team-task', 'id': self.task.pk}))
        dashboard = self.client.get('/api/dashboard/', **self.auth(self.token_for('manager1'))).json()
        self.assertEqual([entry['kind'] for entry in dashboard['waiting']], ['approve_report'])
        # Not twice: the count it replaces is gone from the list below.
        self.assertFalse([entry for entry in dashboard['actions'] if 'status=completed' in entry['url']])

    def test_a_blocked_task_waits_on_its_owner(self):
        self.task.responsible_supervisor = Technician.objects.get(user__username='supervisor1')
        self.task.status = Task.Status.BLOCKED
        self.task.save()
        self.assertEqual([item['kind'] for item in self.waiting('supervisor1')], ['blocked'])


class TeamTodayTests(ApiTestCase):
    """Team → Today: who's where right now, for those who oversee people."""

    def test_working_off_and_free(self):
        self.task.scheduled_for = timezone.now()
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.STARTED, actor=self.technician.user, occurred_at=timezone.now(),
        )
        helper = Technician.objects.create(
            user=User.objects.create_user('sick1', password='pass12345'), country=self.country, full_name='Sami Sick',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
            is_available=False, unavailable_reason=Technician.UnavailableReason.SICK,
        )
        rows = {row['name']: row for row in self.client.get(
            '/api/team-today/', **self.auth(self.token_for('supervisor1'))).json()}
        working = rows[self.technician.full_name]
        self.assertEqual((working['state'], working['task']['id'], working['today_total']), ('working', self.task.pk, 1))
        self.assertEqual((rows[helper.full_name]['state'], rows[helper.full_name]['off_reason']), ('off', 'Sick'))

    def test_a_technician_may_not(self):
        self.assertEqual(self.client.get('/api/team-today/', **self.auth(self.token_for('tech1'))).status_code, 403)


class TicketScreenTests(ApiTestCase):
    """The app's Tickets tab and request screen: the website's request page,
    with the same rules (tasks.views.ticket_access, decide_request_next)."""

    def setUp(self):
        super().setUp()
        Technician.objects.create(
            user=User.objects.create_user('support1', password='pass12345'), country=self.country,
            full_name='Dana Support', language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0701', customer=self.customer, site=self.site,
            company_name='Fitness First', site_description='Marina Branch', contact_name='Ali',
            contact_phone='0501234567', contact_email='ali@example.invalid', description='(from the machines)',
            submitted_at=timezone.now(),
        )
        self.treadmill, self.bike = [
            TicketMachine.objects.create(
                ticket=self.ticket, model_name=name, serial_no=serial, problem=TicketMachine.Problem.NOT_WORKING,
                position=i, description=f'{name} stops',
            )
            for i, (name, serial) in enumerate([('Treadmill', 'T-1'), ('Bike', 'B-1')])
        ]
        self.token = self.token_for('support1')

    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type='application/json', **self.auth(self.token))

    def test_tabs_count_and_page(self):
        body = self.client.get('/api/tickets/?status=new', **self.auth(self.token)).json()
        self.assertEqual((body['counts']['new'], body['count'], body['has_more']), (1, 1, False))
        self.assertEqual(body['results'][0]['ticket_number'], 'AE-T0701')
        closed = self.client.get('/api/tickets/?status=closed', **self.auth(self.token)).json()
        self.assertEqual(closed['results'], [])
        # Apps from before the tabs still get the plain list.
        self.assertIsInstance(self.client.get('/api/tickets/', **self.auth(self.token)).json(), list)

    def test_the_request_screen_shows_the_page_and_what_happens_next(self):
        body = self.client.get(f'/api/tickets/{self.ticket.pk}/', **self.auth(self.token)).json()
        self.assertEqual([machine['name'] for machine in body['machines']], ['Treadmill', 'Bike'])
        self.assertTrue(body['machines'][0]['urgent'])
        self.assertTrue(body['can_reply'])
        self.assertTrue(body['next']['urgent'])
        self.assertIn('supervisor1', [str(User.objects.get(technician__pk=person['value']).username)
                                      for person in body['next']['supervisors']])
        self.assertTrue(all(option['suggestion'] for group in body['next']['reasons'] for option in group['options']))

    def test_a_visit_for_one_machine_then_an_answer_for_the_other(self):
        supervisor = Technician.objects.get(user__username='supervisor1')
        response = self.post(f'/api/tickets/{self.ticket.pk}/next/', {
            'choice': 'visit', 'machines': [self.treadmill.pk], 'supervisor': supervisor.pk,
        })
        self.assertEqual(response.status_code, 200, response.content)
        task = Task.objects.get(pk=response.json()['task']['id'])
        self.assertEqual((task.responsible_supervisor, task.priority), (supervisor, Task.Priority.HIGH))
        reason = self.client.get(f'/api/tickets/{self.ticket.pk}/', **self.auth(self.token)).json()['next']['reasons'][0]['options'][0]['value']
        self.assertEqual(self.post(f'/api/tickets/{self.ticket.pk}/next/', {
            'choice': 'answer', 'machines': [self.bike.pk], 'reason': reason,
        }).json()['detail'], 'Please write your answer to the customer.')
        response = self.post(f'/api/tickets/{self.ticket.pk}/next/', {
            'choice': 'answer', 'machines': [self.bike.pk], 'reason': reason, 'answer': 'Fixed on the phone.',
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_reply_goes_to_the_customer_and_a_note_stays_with_staff(self):
        from django.core import mail
        self.assertEqual(self.post(f'/api/tickets/{self.ticket.pk}/reply/', {'message': 'On our way.'}).status_code, 201)
        self.assertEqual(self.post(f'/api/tickets/{self.ticket.pk}/reply/', {'message': 'Check the warranty', 'note': True}).status_code, 201)
        self.assertEqual(self.ticket.replies.get().message, 'On our way.')
        self.assertEqual(self.ticket.internal_notes.get().message, 'Check the warranty')
        self.assertIn('ali@example.invalid', [address for message in mail.outbox for address in message.to])

    def test_who_may_not(self):
        for username in ('supervisor1', 'tech1'):
            token = self.token_for(username)
            self.assertEqual(self.client.get(f'/api/tickets/{self.ticket.pk}/', **self.auth(token)).status_code, 403)
            response = self.client.post(f'/api/tickets/{self.ticket.pk}/reply/', json.dumps({'message': 'x'}),
                                        content_type='application/json', **self.auth(token))
            self.assertEqual(response.status_code, 403)


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
        photo = SimpleUploadedFile('fault.jpg', REAL_JPEG, content_type='image/jpeg')
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/attachments/', {'file': photo, 'purpose': 'before'})
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
        from rest_framework.authtoken.models import Token
        user = User.objects.create_user('customer1', password='pass12345')
        refused = self.client.post(
            '/api/login/', json.dumps({'username': 'customer1', 'password': 'pass12345'}),
            content_type='application/json',
        )
        # Refused at sign-in: no token at all.
        self.assertEqual(refused.status_code, 400)
        self.assertIn('customer portal', refused.json()['detail'])
        self.assertFalse(Token.objects.filter(user=user).exists())
        # And had one been made some other way, every staff endpoint still refuses it.
        token = Token.objects.create(user=user).key
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
        photo = SimpleUploadedFile('fault.jpg', REAL_JPEG, content_type='image/jpeg')
        response = self.client.post(
            f'/api/my-tasks/{self.task.pk}/attachments/', {'file': photo, 'purpose': 'before'}, **self.auth(token),
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

    def test_the_supervisor_sets_the_visit_time_and_it_leaves_to_plan(self):
        self.task.responsible_supervisor = self.supervisor
        self.task.scheduled_for = None
        self.task.save(update_fields=['responsible_supervisor', 'scheduled_for'])
        token = self.token_for('supervisor1')
        today = self.client.get('/api/my-today/', **self.auth(token)).json()
        self.assertEqual([(i['id'], i['missing']) for i in today['to_plan']], [(self.task.pk, ['date'])])

        in_the_past = timezone.localtime(timezone.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.assertEqual(self._post(token, 'assign', {'action': 'schedule', 'scheduled_for': in_the_past}).status_code, 400)
        tomorrow = timezone.localtime(timezone.now() + timedelta(days=1)).replace(hour=9, minute=0)
        response = self._post(token, 'assign', {'action': 'schedule', 'scheduled_for': tomorrow.strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        # Read as the country's local time (the app sends what the supervisor picked there).
        from zoneinfo import ZoneInfo
        self.assertEqual(timezone.localtime(self.task.scheduled_for, ZoneInfo(self.country.timezone)).hour, 9)
        self.assertTrue(TaskEvent.objects.filter(task=self.task, event_type=TaskEvent.EventType.RESCHEDULED).exists())
        self.assertEqual(self.client.get('/api/my-today/', **self.auth(token)).json()['to_plan'], [])

    def test_to_plan_includes_his_own_visit_with_no_day_and_never_a_technicians(self):
        own = Task.objects.create(
            task_number='AE-0777', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED,
        )
        TaskAssignment.objects.create(task=own, technician=self.supervisor, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now())
        to_plan = self.client.get('/api/my-today/', **self.auth(self.token_for('supervisor1'))).json()['to_plan']
        self.assertIn((own.pk, ['date']), [(i['id'], i['missing']) for i in to_plan])
        self.assertEqual(self.client.get('/api/my-today/', **self.auth(self.token_for('tech1'))).json()['to_plan'], [])

    def test_the_time_is_fixed_once_the_visit_has_started(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])
        tomorrow = timezone.localtime(timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        response = self._post(self.token_for('supervisor1'), 'assign', {'action': 'schedule', 'scheduled_for': tomorrow})
        self.assertEqual(response.status_code, 400)

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

        # The final approval is the Technical Support Manager's (docs/access_rules.md), not a Manager's.
        manager_token = self.token_for('manager1')
        self.assertEqual(self._post(manager_token, 'approve', {}).status_code, 403)
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.SUPPORT_MANAGER)
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
        self.assertEqual(detail['undoable_step'], 'Confirmed the task')

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

        Part.objects.create(code='BELT-01', description='Treadmill belt', photo='part_photos/abc/BELT-01.png')
        Part.objects.create(code='OLD-9', is_active=False)
        Part.objects.create(code='PAD-7')
        token = self.token_for('tech1')
        response = self.client.get('/api/parts/', **self.auth(token))
        # Each with its photo, so the technician can check it's the right part.
        rows = response.json()
        # The photo by a signed link: the app loads it without the website's sign-in (spots.media).
        self.assertTrue(rows[0].pop('photo_url').startswith('http://testserver/media/part_photos/abc/BELT-01.png?t='))
        self.assertEqual(rows, [
            {'code': 'BELT-01', 'description': 'Treadmill belt'},
            {'code': 'PAD-7', 'description': '', 'photo_url': ''},
        ])


class AppErrorTests(ApiTestCase):
    """The phone app's errors reach the office: kept, with who and where, and in the admins' bell."""

    def test_an_error_is_kept_with_who_and_where_then_shown_to_the_admins(self):
        from people.models import AppError
        body = {'message': 'Cannot read property x', 'stack': 'at Report', 'screen': 'screen',
                'app_version': '1.0.0', 'platform': 'ios 18'}
        response = self.client.post('/api/app-errors/', json.dumps(body), content_type='application/json',
                                    **self.auth(self.token_for('tech1')))
        self.assertEqual(response.status_code, 204)
        error = AppError.objects.get()
        self.assertEqual((error.technician, error.screen, error.app_version), (self.technician, 'screen', '1.0.0'))
        # Before sign-in too, without a token.
        self.client.post('/api/app-errors/', json.dumps({'message': 'At sign-in'}), content_type='application/json')
        self.assertEqual(AppError.objects.count(), 2)
        admin = User.objects.create_user('admin9', password='pass12345', is_staff=True, is_superuser=True)
        Technician.objects.create(user=admin, country=self.country, full_name='Ada Admin', language='en',
                                  role=Technician.Role.ADMIN, employment_type='staff')
        self.client.login(username='admin9', password='pass12345')
        self.assertContains(self.client.get('/tasks/'), '2 new errors in the phone app')
        self.client.get('/admin/people/apperror/')  # opening the list marks them seen
        self.assertNotContains(self.client.get('/tasks/'), 'new errors in the phone app')


class PushDeviceTests(ApiTestCase):
    """Registering phones, and pushes going out (Expo's service mocked)."""

    TOKEN = 'ExponentPushToken[abc123]'

    def test_a_test_notification_needs_a_registered_phone_then_goes_to_it(self):
        from unittest.mock import patch
        token = self.token_for('tech1')
        response = self.client.post('/api/push-device/test/', {}, content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 409)
        self.client.post('/api/push-device/', json.dumps({'token': self.TOKEN}), content_type='application/json',
                         **self.auth(token))
        with patch('people.push.send_push') as push:
            response = self.client.post('/api/push-device/test/', {}, content_type='application/json', **self.auth(token))
        self.assertEqual((response.status_code, response.json()['phones']), (200, 1))
        self.assertEqual(push.call_args.args[3], {'type': 'test'})

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

        evidence = SimpleUploadedFile('doing-it.jpg', REAL_JPEG, content_type='image/jpeg')
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

    def test_phone_language_and_photo_change_and_the_rest_stays(self):
        token = self.token_for('tech1')
        technician = Technician.objects.get(user__username='tech1')
        role, country = technician.role, technician.country_id
        photo = SimpleUploadedFile('me.jpg', REAL_JPEG, content_type='image/jpeg')
        response = self.client.post('/api/me/', {
            'phone': '+971500000001', 'language': 'ar', 'photo': photo, 'role': 'manager',
        }, **self.auth(token))
        self.assertEqual(response.status_code, 200)
        technician.refresh_from_db()
        # A new email has its own test: it needs the password and a confirmation link
        # (tasks.tests.StaffAccountSettingsTests).
        self.assertEqual((technician.phone, technician.language), ('+971500000001', 'ar'))
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

    def test_the_page_shows_their_work_tasks_and_reports(self):
        page = self.client.get(f'/api/people/{self.technician.pk}/', **self.auth(self.token_for('supervisor1'))).json()
        self.assertEqual(set(page['workload']), {'today', 'upcoming', 'overdue'})
        self.assertEqual([task['id'] for task in page['tasks']], [self.task.pk])
        self.assertEqual((page['tasks'][0]['my_role'], page['tasks'][0]['still_on_it']), ('Lead', True))
        self.assertEqual((page['reports'], page['trips']), ([], []))

    def test_a_colleague_here_on_a_trip_opens_too(self):
        from people.models import TechnicianTrip
        from reference.models import Country
        egypt = Country.objects.create(name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP')
        visitor = Technician.objects.create(
            user=User.objects.create_user('cairo9', password='pass12345'), country=egypt, full_name='Omar Visitor',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        token = self.token_for('supervisor1')
        self.assertEqual(self.client.get(f'/api/people/{visitor.pk}/', **self.auth(token)).status_code, 404)
        today = timezone.localdate()
        # Either side of today, so it holds whatever the hour (the trip is in the country's own date).
        TechnicianTrip.objects.create(technician=visitor, country=self.country, start_date=today - timedelta(days=1),
                                      end_date=today + timedelta(days=1), created_at=timezone.now(),
                                      created_by=self.supervisor_user)
        page = self.client.get(f'/api/people/{visitor.pk}/', **self.auth(token)).json()
        # To look at only: their skills, availability and account stay with their own country.
        self.assertEqual((page['can_review_skills'], page['can_set_availability'], page['can_deactivate']), (False, False, False))
        response = self.client.post(f'/api/people/{visitor.pk}/availability/', json.dumps({'available': False}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 404)

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

    def test_before_any_tap_the_map_opens_on_the_countrys_branches(self):
        from customers.models import Site
        for lat, lng in (('25.0800', '55.1400'), ('25.2000', '55.2700'), ('25.1000', '55.2000'), ('45.4100', '11.8700')):
            Site.objects.create(customer=self.customer, name=f'Branch {lat}', address='-', latitude=lat, longitude=lng)
        area = self.client.get('/api/team-map/', **self.auth(self.token_for('supervisor1'))).json()['area']
        # The middle of them: a mistyped location on another continent doesn't pull it away.
        self.assertTrue(25 < area['latitude'] < 25.3 and 55 < area['longitude'] < 55.3, area)
        self.assertGreaterEqual(area['delta'], 0.3)


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
            # Reaching every country is the Project Manager's, not a country Manager's (docs/access_rules.md).
            role=Technician.Role.PROJECT_MANAGER, employment_type='staff',
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

    def test_a_country_manager_cannot_switch(self):
        manager = User.objects.create_user('uae_manager', password='pass12345')
        Technician.objects.create(user=manager, country=self.country, full_name='Uae Manager', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        token = self.token_for('uae_manager')
        self.assertEqual(self.client.get('/api/me/', **self.auth(token)).json()['countries'], [])
        rows = self.client.get('/api/team-progress/', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token)).json()['rows']
        self.assertNotIn(self.cairo_tech.pk, [row['id'] for row in rows])

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
        # The Project Manager's own task: they change only the tasks they own (docs/access_rules.md).
        self.task.responsible_supervisor = User.objects.get(username='manager1').technician
        self.task.save(update_fields=['responsible_supervisor'])
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


class StaffAccountEmergencyApiTests(ManagerChoosesTheCountryTests):
    # A lost phone or a locked-out technician, handled from the app.

    def test_a_manager_signs_out_phones_and_sets_a_temporary_password(self):
        from rest_framework.authtoken.models import Token
        tech_token = self.token_for('tech1')
        token = self.token_for('manager1')
        rows = self.client.get('/api/staff-accounts/?q=tech', **self.auth(token)).json()
        row = next(item for item in rows if item['id'] == self.technician.pk)
        self.assertTrue(row['can_manage'])

        response = self.client.post(f'/api/staff-accounts/{self.technician.pk}/', json.dumps({'action': 'sign_out_phones'}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Token.objects.filter(key=tech_token).exists())

        # A temporary password: the Admin's alone.
        self.assertFalse(row['can_set_password'])
        response = self.client.post(f'/api/staff-accounts/{self.technician.pk}/', json.dumps({'action': 'temporary_password'}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 403)
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.ADMIN)
        response = self.client.post(f'/api/staff-accounts/{self.technician.pk}/', json.dumps({'action': 'temporary_password'}),
                                    content_type='application/json', **self.auth(token))
        password = response.json()['password']
        self.assertTrue(password)
        self.technician.refresh_from_db()
        self.assertTrue(self.technician.must_change_password)
        self.assertTrue(self.technician.user.check_password(password))

    def test_a_supervisor_cannot(self):
        token = self.token_for('supervisor1')
        self.assertEqual(self.client.get('/api/staff-accounts/', **self.auth(token)).status_code, 403)
        response = self.client.post(f'/api/staff-accounts/{self.technician.pk}/', json.dumps({'action': 'unlock'}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 403)


class InstallationChecklistApiTests(ApiTestCase):
    # The installers' checklist in the app: each machine, and its photo.

    def setUp(self):
        super().setUp()
        from reference.models import TaskType
        self.task.status = Task.Status.IN_PROGRESS
        self.task.task_type = TaskType.objects.create(code='install', name='New installation', name_ar='تركيب', category='installation')
        self.task.brand = self.brand
        self.task.save(update_fields=['status', 'task_type', 'brand'])
        from tasks.models import TaskProduct
        self.line = TaskProduct.objects.create(task=self.task, product_code='EXCITE-RUN')

    def test_the_app_checks_off_a_machine_with_its_photo(self):
        from tasks.models import TaskProduct
        token = self.token_for('tech1')
        screen = self.client.get(f'/api/my-tasks/{self.task.pk}/report/', **self.auth(token)).json()
        self.assertEqual([row['product_code'] for row in screen['checklist']], ['EXCITE-RUN'])
        self.assertIn('done', [choice['value'] for choice in screen['checklist_statuses']])

        photo = SimpleUploadedFile('run.jpg', REAL_JPEG, content_type='image/jpeg')
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/lines/{self.line.pk}/photo/', {'photo': photo},
                                    **self.auth(token))
        self.assertTrue(response.json()['photo_url'].startswith('http'))

        body = {
            'findings': '', 'action_taken': '', 'work_lines': ['bolts'], 'resolved': True, 'customer_name': 'Ali',
            'parts': [], 'signature': SIGNATURE_DATA_URL,
            'checklist': [
                {'id': self.line.pk, 'product_code': 'EXCITE-RUN', 'status': 'damaged', 'serial_number': 'TG-5'},
                {'product_code': 'EXCITE-TOP', 'status': 'done', 'serial_number': ''},
            ],
        }
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/report/', json.dumps(body),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 400)
        self.assertIn('checklist', response.json())  # the added one is "installed" without a serial

        body['checklist'][1]['serial_number'] = 'TG-6'
        body['checklist'][1]['tested_working'] = True  # commissioning: assembled, levelled and tested
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/report/', json.dumps(body),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 201, response.content)
        self.line.refresh_from_db()
        self.assertEqual(self.line.status, 'damaged')  # its photo said what was wrong
        self.assertTrue(TaskProduct.objects.get(product_code='EXCITE-TOP').added_on_site)

    def test_a_supervisor_makes_the_follow_up_from_the_app(self):
        from reports.models import WorkReport
        self.line.status = 'later'
        self.line.save(update_fields=['status'])
        WorkReport.objects.create(task=self.task, findings='', action_taken='', resolved=True, labour_hours=1,
                                  customer_name='Ali', submitted_at=timezone.now())
        token = self.token_for('supervisor1')
        page = self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()
        self.assertEqual((page['equipment']['still_to_install'], page['equipment']['can_follow_up']), (1, True))
        response = self.client.post(f'/api/tasks/{self.task.pk}/follow-up/', json.dumps({}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Task.objects.get(pk=response.json()['id']).previous_task, self.task)


class NextStepApiTests(ManagerChoosesTheCountryTests):
    # "Send to installation" from the app: loaded in Cairo, installed in Dubai.

    def test_a_manager_carries_loaded_machines_to_another_country(self):
        from customers.models import Customer, Site
        from reference.models import TaskType
        from tasks.models import TaskProduct
        ours = Customer.objects.create(country=self.egypt, name='Profit Sports Solutions', segment='other', is_internal=True)
        warehouse = Site.objects.create(customer=ours, name='Cairo warehouse', address='Cairo')
        loading = Task.objects.create(
            task_number='EG-0001', site=warehouse, priority=Task.Priority.NORMAL, source=Task.Source.INTERNAL,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.COMPLETED,
            task_type=TaskType.objects.create(code='load', name='Loading', name_ar='تحميل', category='loading'),
        )
        TaskType.objects.create(code='inst', name='Installation', name_ar='تركيب', category='installation')
        line = TaskProduct.objects.create(task=loading, product_code='EXCITE-RUN', serial_number='TG-1', status='done')
        token = self.token_for('manager1')
        page = self.client.get(f'/api/tasks/{loading.pk}/', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token)).json()
        steps = page['equipment']['next_step']['steps']
        install = next(step for step in steps if step['value'] == 'installation')
        self.assertIn(str(self.site.pk), [site['value'] for site in install['sites']])  # the Dubai site
        dubai_supervisor = Technician.objects.get(user__username='supervisor1')
        self.assertIn(str(dubai_supervisor.pk), [person['value'] for person in page['equipment']['next_step']['supervisors']])

        send = {'step': 'installation', 'site': self.site.pk, 'lines': [line.pk]}
        # Another country, and nobody chosen to supervise it there: refused.
        response = self.client.post(f'/api/tasks/{loading.pk}/next-step/', json.dumps(send),
                                    content_type='application/json', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token))
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f'/api/tasks/{loading.pk}/next-step/', json.dumps({
            **send, 'supervisor': dubai_supervisor.pk,
        }), content_type='application/json', HTTP_X_ACTIVE_COUNTRY=str(self.egypt.pk), **self.auth(token))
        self.assertEqual(response.status_code, 201, response.content)
        installation = Task.objects.get(pk=response.json()['id'])
        self.assertEqual(installation.responsible_supervisor, dubai_supervisor)
        self.assertEqual((installation.site, installation.previous_task), (self.site, loading))
        self.assertEqual(installation.products.get().source_line, line)


class PlanLinesApiTests(InstallationChecklistApiTests):
    def _person(self, username, role):
        from people.models import Technician
        Technician.objects.create(user=User.objects.create_user(username, password='pass12345'), country=self.country,
                                  full_name=username, language='en', role=role, employment_type='staff')
        return self.token_for(username)

    def test_a_manager_plans_the_machines_from_the_app(self):
        from people.models import Technician
        plan = json.dumps({'lines': [{'product_code': 'BENCH-01', 'quantity': 3, 'frame': '', 'arm': 'Red'}]})
        url = f'/api/tasks/{self.task.pk}/lines/'
        # Not the supervisor, nor the Operations Manager: an installation's machines are the
        # Manager's, the Project Manager's and the Admin's (people.permissions.can_plan_equipment).
        for token in (self.token_for('supervisor1'), self._person('ops9', Technician.Role.OPERATIONS_MANAGER)):
            refused = self.client.post(url, plan, content_type='application/json', **self.auth(token))
            self.assertEqual(refused.status_code, 400)
            self.assertIn('Only a manager plans the machines', refused.json()['detail'])
        token = self._person('project9', Technician.Role.PROJECT_MANAGER)
        # Their own installation: the Project Manager changes only the tasks they own (docs/access_rules.md).
        self.task.responsible_supervisor = Technician.objects.get(user__username='project9')
        self.task.save(update_fields=['responsible_supervisor'])
        response = self.client.post(url, plan, content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 200, response.content)
        lines = response.json()['lines']
        # The machine already on the task stays; the three planned are added.
        self.assertEqual([(line['product_code'], line['quantity'], line['arm'], line['frame']) for line in lines],
                         [('EXCITE-RUN', 1, 'Black', 'Black')] + [('BENCH-01', 1, 'Red', 'Black')] * 3)
        self.assertEqual(self.client.get(f'/api/tasks/{self.task.pk}/lines/', **self.auth(self.token_for('tech1'))).status_code, 403)

    def test_the_app_never_removes_or_duplicates_a_machine_on_the_task(self):
        token = self.token_for('supervisor1')
        url = f'/api/tasks/{self.task.pk}/lines/'
        before = list(self.task.products.order_by('pk').values_list('pk', 'product_code', 'serial_number'))
        lines = self.client.get(url, **self.auth(token)).json()['lines']
        self.assertTrue(all('line_id' in line for line in lines))
        # Everything left out (the old app's Remove): all still there.
        self.client.post(url, json.dumps({'lines': []}), content_type='application/json', **self.auth(token))
        self.assertEqual(list(self.task.products.order_by('pk').values_list('pk', 'product_code', 'serial_number')), before)
        # The same list sent back without ids (an app from before line ids): no duplicates.
        for line in lines:
            line.pop('line_id')
        self.client.post(url, json.dumps({'lines': lines}), content_type='application/json', **self.auth(token))
        self.assertEqual(list(self.task.products.order_by('pk').values_list('pk', 'product_code', 'serial_number')), before)


class AppUsersTests(ManagerChoosesTheCountryTests):
    # The admin adds a user from the app; a manager deactivates someone,
    # handing their open tasks to a colleague.

    def test_the_admin_adds_a_user_with_their_country_and_login(self):
        admin = Technician.objects.create(
            user=User.objects.create_user('admin1', password='pass12345'), country=self.country,
            full_name='Amina Admin', language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        token = self.token_for('admin1')
        fields = self.client.get('/api/people/new/', **self.auth(token)).json()['fields']
        self.assertIn(str(self.egypt.pk), [choice['value'] for choice in fields['country']['choices']])
        response = self.client.post('/api/people/new/', json.dumps({
            'role': 'supervisor', 'full_name': 'Ahmed Egypt', 'country': self.egypt.pk, 'email': 'ahmed@example.com',
            'language': 'ar', 'employment_type': 'staff',
        }), content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()['has_login'])
        self.assertEqual(Technician.objects.get(full_name='Ahmed Egypt').country, self.egypt)
        self.assertEqual(self.client.get('/api/people/new/', **self.auth(self.token_for('manager1'))).status_code, 403)
        self.assertIsNotNone(admin)

    def test_a_manager_deactivates_and_hands_over(self):
        colleague = Technician.objects.create(
            user=User.objects.create_user('tech2', password='pass12345'), country=self.country,
            full_name='Rami Colleague', language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        token = self.token_for('manager1')
        page = self.client.get(f'/api/people/{self.technician.pk}/', **self.auth(token)).json()
        self.assertTrue(page['can_deactivate'])
        self.assertEqual(page['open_tasks'], [self.task.task_number])
        response = self.client.post(f'/api/people/{self.technician.pk}/deactivate/', json.dumps({
            'reason': 'Resigned', 'move_to': colleague.pk,
        }), content_type='application/json', **self.auth(token))
        self.assertEqual(response.json(), {'moved': 1, 'still_to_reassign': 0})
        self.assertEqual(self.task.assignments.get(is_active=True).technician, colleague)


class MyTasksNewestFirstTests(ApiTestCase):
    def test_the_task_received_last_comes_first(self):
        later = Task.objects.create(
            task_number='AE-0778', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED, scheduled_for=timezone.now() + timedelta(days=30),
        )
        TaskAssignment.objects.create(task=later, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now() + timedelta(minutes=5))
        ids = [t['id'] for t in self.client.get('/api/my-tasks/', **self.auth(self.token_for('tech1'))).json()]
        self.assertEqual(ids[:2], [later.pk, self.task.pk])


class TaskMessagesApiTests(ApiTestCase):
    """The task's team messages in the app, for its technician and for the team view."""

    def test_the_lead_and_the_supervisor_talk_on_the_task(self):
        tech, supervisor = self.token_for('tech1'), self.token_for('supervisor1')
        url = f'/api/tasks/{self.task.pk}/messages/'
        response = self.client.post(url, json.dumps({'message': 'The part arrived.'}),
                                    content_type='application/json', **self.auth(supervisor))
        self.assertEqual(response.status_code, 201)
        thread = self.client.get(url, **self.auth(tech)).json()
        self.assertEqual([(m['message'], m['mine']) for m in thread], [('The part arrived.', False)])
        self.assertEqual(self.client.post(url, json.dumps({'message': ''}), content_type='application/json',
                                          **self.auth(tech)).status_code, 400)

    def test_someone_not_on_the_task_without_the_team_view_cannot_read_it(self):
        outsider = User.objects.create_user('outsider', password='pass12345')
        Technician.objects.create(user=outsider, country=self.country, full_name='Out Sider', language='en',
                                  role=Technician.Role.TECHNICIAN, employment_type='staff')
        response = self.client.get(f'/api/tasks/{self.task.pk}/messages/', **self.auth(self.token_for('outsider')))
        self.assertEqual(response.status_code, 403)


class TaskActionApiTests(TeamTaskApiTests):
    """The website task page's actions from the app: same rules, same permissions."""

    def act(self, token, **body):
        return self._post(token, 'action', body)

    def test_the_supervisor_tells_the_customer_the_visit_time_and_a_delay(self):
        from django.core import mail
        self.task.responsible_supervisor = self.supervisor
        self.task.scheduled_for = timezone.now() + timedelta(days=1)
        self.task.save(update_fields=['responsible_supervisor', 'scheduled_for'])
        self.site.contact_email = 'gym@example.test'
        self.site.save(update_fields=['contact_email'])
        token = self.token_for('supervisor1')
        actions = self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()['actions']
        self.assertTrue(actions['notify_schedule'] and actions['notify_delay'])
        mail.outbox.clear()
        self.assertEqual(self.act(token, action='notify_schedule').status_code, 200)
        self.assertTrue(any('gym@example.test' in m.to for m in mail.outbox))
        self.assertEqual(self.act(token, action='notify_delay', reason='').status_code, 400)  # needs a reason
        self.assertEqual(self.act(token, action='notify_delay', reason='Traffic on the highway').status_code, 200)
        self.assertTrue(TaskEvent.objects.filter(task=self.task, event_type=TaskEvent.EventType.DELAY_NOTICE).exists())

    def test_manager_and_admin_actions_stay_theirs(self):
        supervisor = self.token_for('supervisor1')
        self.assertEqual(self.act(supervisor, action='send_feedback_request').status_code, 403)
        self.assertEqual(self.act(supervisor, action='reopen').status_code, 403)
        self.assertEqual(self.act(self.token_for('manager1'), action='reopen').status_code, 403)  # admins only

    def test_a_manager_books_a_new_visit_for_a_task_stopped_on_site(self):
        manager = self.token_for('manager1')
        when = timezone.localtime(timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        self.assertEqual(self.act(manager, action='rebook', scheduled_for=when).status_code, 400)  # not stopped
        self.task.status = Task.Status.BLOCKED
        self.task.save(update_fields=['status'])
        self.assertTrue(self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(manager)).json()['actions']['rebook'])
        self.assertEqual(self.act(manager, action='rebook', scheduled_for=when, note='Part arrived').status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)


class TaskEditApiTests(TeamTaskApiTests):
    """The website's Edit task and documents, from the app."""

    def test_every_choice_is_listed_once_and_never_blank(self):
        # The app's picker adds its own "None"; a blank or repeated value would give its list
        # two entries with one key (the "two children with the same key" crash).
        self.task.responsible_supervisor = self.supervisor
        self.task.save(update_fields=['responsible_supervisor'])
        token = self.token_for('supervisor1')
        for field in self.client.get(f'/api/tasks/{self.task.pk}/edit/', **self.auth(token)).json()['fields']:
            values = [choice['value'] for choice in field['choices']]
            self.assertNotIn('', values, field['name'])
            self.assertEqual(len(values), len(set(values)), field['name'])

    def test_saving_one_field_keeps_every_other_as_it_was(self):
        self.task.responsible_supervisor = self.supervisor
        self.task.description = 'Treadmill stops'
        self.task.save(update_fields=['responsible_supervisor', 'description'])
        token = self.token_for('supervisor1')
        fields = {f['name']: f for f in self.client.get(f'/api/tasks/{self.task.pk}/edit/', **self.auth(token)).json()['fields']}
        self.assertEqual(fields['description']['value'], 'Treadmill stops')
        before = {name: getattr(self.task, name) for name in ('priority', 'billing_type', 'is_warranty', 'task_type_id')}
        response = self.client.post(f'/api/tasks/{self.task.pk}/edit/', json.dumps({'description': 'Belt slips'}),
                                    content_type='application/json', **self.auth(token))
        self.assertEqual(response.status_code, 200, response.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.description, 'Belt slips')
        self.assertEqual({name: getattr(self.task, name) for name in before}, before)

    def test_a_technician_adds_no_office_documents(self):
        upload = SimpleUploadedFile('note.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        response = self.client.post(f'/api/tasks/{self.task.pk}/documents/', {'file': upload, 'purpose': 'other'},
                                    **self.auth(self.token_for('tech1')))
        self.assertEqual(response.status_code, 403)


class VisitTimeRuleTests(TeamTaskApiTests):
    """The website's rule in the app: a time a manager fixed is moved by asking, not by the supervisor."""

    def test_a_fixed_time_is_asked_for_not_changed(self):
        self.task.responsible_supervisor = self.supervisor
        self.task.schedule_time_locked = True
        self.task.scheduled_for = timezone.now() + timedelta(days=1)
        self.task.save(update_fields=['responsible_supervisor', 'schedule_time_locked', 'scheduled_for'])
        token = self.token_for('supervisor1')
        actions = self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()['actions']
        self.assertFalse(actions['schedule'])
        self.assertTrue(actions['request_schedule_change'])
        when = timezone.localtime(timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        self.assertEqual(self._post(token, 'assign', {'action': 'schedule', 'scheduled_for': when}).status_code, 400)
        self.assertTrue(self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(self.token_for('manager1'))).json()['actions']['schedule'])


class LineBrandTests(TeamTaskApiTests):
    """Each machine line has its own brand; none given: the task's."""

    def test_a_line_takes_the_task_brand_unless_given_its_own(self):
        from reference.models import Brand
        from tasks.views import save_planned_lines
        from tasks.forms import TaskProductForm
        panatta, technogym = Brand.objects.create(name='Panatta'), Brand.objects.create(name='Technogym')
        self.task.brand = panatta
        self.task.save(update_fields=['brand'])
        rows = []
        for data in ({'product_code': 'BENCH-01', 'quantity': 1}, {'product_code': 'RUN-700', 'quantity': 1, 'brand': technogym.pk}):
            form = TaskProductForm(data)
            self.assertTrue(form.is_valid(), form.errors)
            rows.append(form.cleaned_data)
        save_planned_lines(self.task, rows)
        self.assertEqual(dict(self.task.products.values_list('product_code', 'brand__name')),
                         {'BENCH-01': 'Panatta', 'RUN-700': 'Technogym'})


class TellTheCustomerTheTimeTests(TeamTaskApiTests):
    """The customer is told a time once, again only after it moves — never while a change awaits a manager."""

    def test_offered_only_when_needed(self):
        from tasks.models import ScheduleChangeRequest
        self.task.responsible_supervisor = self.supervisor
        self.task.scheduled_for = timezone.now() + timedelta(days=1)
        self.task.save(update_fields=['responsible_supervisor', 'scheduled_for'])
        self.site.contact_email = 'gym@example.test'
        self.site.save(update_fields=['contact_email'])
        token = self.token_for('supervisor1')
        offered = lambda: self.client.get(f'/api/tasks/{self.task.pk}/', **self.auth(token)).json()['actions']['notify_schedule']
        self.assertTrue(offered())
        self.assertEqual(self._post(token, 'action', {'action': 'notify_schedule'}).status_code, 200)
        self.assertFalse(offered())  # the customer has this time
        TaskEvent.objects.create(task=self.task, event_type=TaskEvent.EventType.RESCHEDULED, occurred_at=timezone.now(),
                                 actor=self.supervisor_user)
        self.assertTrue(offered())  # the time moved
        ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self.supervisor, requested_scheduled_for=timezone.now() + timedelta(days=3),
            created_at=timezone.now(),
        )
        self.assertFalse(offered())
        self.assertEqual(self._post(token, 'action', {'action': 'notify_schedule'}).status_code, 400)


class UnreadMessagesOnListsTests(TaskMessagesApiTests):
    def test_the_list_counts_new_messages_until_read(self):
        tech, supervisor = self.token_for('tech1'), self.token_for('supervisor1')
        self.task.responsible_supervisor = self.supervisor_user.technician
        self.task.save(update_fields=['responsible_supervisor'])
        url = f'/api/tasks/{self.task.pk}/messages/'
        self.client.post(url, json.dumps({'message': 'Bring the belt.'}), content_type='application/json', **self.auth(supervisor))
        mine = {t['id']: t for t in self.client.get('/api/my-tasks/', **self.auth(tech)).json()}
        self.assertEqual(mine[self.task.pk]['unread_messages'], 1)
        self.client.get(url, **self.auth(tech))  # reading the thread
        mine = {t['id']: t for t in self.client.get('/api/my-tasks/', **self.auth(tech)).json()}
        self.assertEqual(mine[self.task.pk]['unread_messages'], 0)


class BellApiTests(TaskMessagesApiTests):
    """The app's bell: the website bell's items for the same person, worded and openable."""

    def test_a_team_message_is_in_the_technicians_bell(self):
        self.client.post(f'/api/tasks/{self.task.pk}/messages/', json.dumps({'message': 'Call the gym first.'}),
                         content_type='application/json', **self.auth(self.token_for('supervisor1')))
        bell = self.client.get('/api/bell/', **self.auth(self.token_for('tech1'))).json()
        self.assertEqual(bell['count'], 1)
        item = bell['items'][0]
        self.assertEqual((item['kind'], item['opens']), ('task_message', {'screen': 'task', 'id': self.task.pk}))
        sender = self.supervisor_user.technician.full_name
        self.assertEqual(item['text'], f'New message from {sender}')  # who wrote it


class TeamListPagesAndFiltersTests(TeamTaskApiTests):
    """The app's All in the country: the website task list's filters, 30 at a time."""

    def test_pages_of_thirty_and_older_apps_get_the_list(self):
        for n in range(35):
            Task.objects.create(
                task_number=f'AE-{8000 + n}', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
                billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            )
        token = self.token_for('supervisor1')
        first = self.client.get('/api/tasks/?page=1', **self.auth(token)).json()
        self.assertEqual((first['count'], len(first['results']), first['has_more']), (36, 30, True))
        second = self.client.get('/api/tasks/?page=2', **self.auth(token)).json()
        self.assertEqual((len(second['results']), second['has_more']), (6, False))
        self.assertIsInstance(self.client.get('/api/tasks/', **self.auth(token)).json(), list)
        no_lead = self.client.get('/api/tasks/?page=1&lead=none', **self.auth(token)).json()
        self.assertEqual(no_lead['count'], 35)  # the website's "no technician" filter

    def test_search_finds_a_task_by_its_technician(self):
        token = self.token_for('supervisor1')
        found = self.client.get('/api/tasks/?page=1&q=Tarek', **self.auth(token)).json()
        self.assertEqual([t['id'] for t in found['results']], [self.task.pk])

    def test_the_filter_choices(self):
        choices = self.client.get('/api/tasks/filters/', **self.auth(self.token_for('supervisor1'))).json()
        self.assertIn('blocked', [s['value'] for s in choices['statuses']])
        self.assertIn('Tarek Tech', [t['label'] for t in choices['technicians']])


class CustomerPhotosOnTheMachineCardTests(ApiTestCase):
    """The customer's serial plate and fault photo, on the machine's card: the
    technician confirms them ("Use this") instead of taking them again; After
    is always theirs (tasks.views.confirm_customer_files)."""

    def setUp(self):
        import tempfile

        from django.test import override_settings
        media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        media.enable()
        self.addCleanup(media.disable)
        super().setUp()
        from tasks.models import CustomerTicketAttachment
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0901', customer=self.customer, site=self.site,
            company_name='Fitness First', contact_name='Ali', contact_phone='0501234567',
            description='-', submitted_at=timezone.now(), task=self.task, status=CustomerTicket.Status.CONVERTED,
        )
        machine = TicketMachine.objects.create(
            ticket=ticket, asset=self.asset, problem='noise', decision='visit', task=self.task,
            serial_photo=SimpleUploadedFile('plate.jpg', REAL_JPEG, content_type='image/jpeg'),
        )
        CustomerTicketAttachment.objects.create(
            ticket=ticket, machine=machine, uploaded_at=timezone.now(),
            file=SimpleUploadedFile('fault.jpg', REAL_JPEG, content_type='image/jpeg'),
        )
        self.token = self.token_for('tech1')

    def card(self):
        (card,) = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(self.token)).json()['machine_cards']
        return card

    def test_the_technician_confirms_the_customers_photos_once_each(self):
        offered = {item['slot']: item['name'] for item in self.card()['customer_files']}
        self.assertEqual(set(offered), {'serial_plate', 'before'})
        response = self.client.post(f'/api/my-tasks/{self.task.pk}/confirm-photo/', {
            'asset': self.asset.pk, 'name': offered['serial_plate'],
        }, **self.auth(self.token))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['source'], response.json()['purpose']), ('customer', 'serial_plate'))
        self.assertEqual(response.json()['uploaded_by_name'], 'Tarek Tech')
        # Once: it's in its slot now, and no longer offered.
        card = self.card()
        self.assertEqual([photo['purpose'] for photo in card['photos']], ['serial_plate'])
        self.assertEqual([item['slot'] for item in card['customer_files']], ['before'])
        again = self.client.post(f'/api/my-tasks/{self.task.pk}/confirm-photo/', {
            'asset': self.asset.pk, 'name': offered['serial_plate'],
        }, **self.auth(self.token))
        self.assertEqual(again.status_code, 400)

    def test_the_serial_plate_and_after_are_photos_and_before_can_be_a_video(self):
        from spots.test_files import REAL_MP4
        url = f'/api/my-tasks/{self.task.pk}/attachments/'
        for purpose, status in (('after', 400), ('serial_plate', 400), ('before', 201)):
            video = SimpleUploadedFile('clip.mp4', REAL_MP4, content_type='video/mp4')
            response = self.client.post(url, {'file': video, 'purpose': purpose, 'asset': self.asset.pk}, **self.auth(self.token))
            self.assertEqual(response.status_code, status, (purpose, response.content))

    def test_use_these_confirms_every_fault_photo_and_a_new_plate_replaces_the_old(self):
        from tasks.models import CustomerTicketAttachment
        machine = TicketMachine.objects.get(ticket__ticket_number='AE-T0901')
        CustomerTicketAttachment.objects.create(
            ticket=machine.ticket, machine=machine, uploaded_at=timezone.now(),
            file=SimpleUploadedFile('noise.jpg', REAL_JPEG, content_type='image/jpeg'),
        )
        url = f'/api/my-tasks/{self.task.pk}/confirm-photo/'
        # One decision for the slot: both fault photos at once.
        response = self.client.post(url, {'asset': self.asset.pk, 'slot': 'before'}, **self.auth(self.token))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([item['purpose'] for item in response.json()], ['before', 'before'])
        # The serial plate: one photo — the technician's own replaces the customer's.
        self.client.post(url, {'asset': self.asset.pk, 'slot': 'serial_plate'}, **self.auth(self.token))
        own = SimpleUploadedFile('mine.jpg', REAL_JPEG, content_type='image/jpeg')
        self.client.post(f'/api/my-tasks/{self.task.pk}/attachments/', {
            'file': own, 'purpose': 'serial_plate', 'asset': self.asset.pk,
        }, **self.auth(self.token))
        plates = TaskAttachment.objects.filter(task=self.task, purpose='serial_plate')
        self.assertEqual([(plate.source, plate.customer_file) for plate in plates], [('technician', '')])
        card = self.card()
        self.assertEqual(sorted(photo['purpose'] for photo in card['photos']), ['before', 'before', 'serial_plate'])
        # The customer's plate, replaced, is no longer in the slot: it shows once, below ("Also sent").
        self.assertEqual([item['slot'] for item in card['customer_files']], ['serial_plate'])


class OnTheWayNoticeTests(ApiTestCase):
    """The technician sets off: the customer is told — an email to the branch
    contact and their portal bell — once a visit day (tasks.views.tell_customer_on_the_way);
    their portal shows "On the way", then "On site"."""

    def setUp(self):
        super().setUp()
        from customers.models import PortalUser
        self.customer.contact_email = 'ali@fitnessfirst.example'
        self.customer.save(update_fields=['contact_email'])
        PortalUser.objects.create(customer=self.customer, user=User.objects.create_user('ali@fitnessfirst.example'))
        self.token = self.token_for('tech1')

    def act(self, action):
        return self.client.post(f'/api/my-tasks/{self.task.pk}/action/', json.dumps({'action': action}),
                                content_type='application/json', **self.auth(self.token))

    def notices(self):
        return [m for m in mail.outbox if 'on the way' in m.subject]

    def test_the_customer_hears_once_and_sees_the_progress(self):
        from customers.models import CustomerNotification
        from customers.progress import ON_SITE, ON_THE_WAY, visit_progress
        self.act('accept')
        self.act('en_route')
        (notice,) = self.notices()
        self.assertEqual(notice.to, ['ali@fitnessfirst.example'])
        self.assertIn('Tarek Tech', notice.body)
        self.assertTrue(CustomerNotification.objects.filter(task=self.task, kind='on_the_way').exists())
        self.assertEqual(visit_progress(self.task), ON_THE_WAY)
        # Undone and tapped again: not a second email.
        self.act('undo')
        self.act('en_route')
        self.assertEqual(len(self.notices()), 1)
        self.act('arrive')
        self.assertEqual(visit_progress(self.task), ON_SITE)

    def test_signed_by_customer_service_with_replies_to_its_mailbox(self):
        from django.conf import settings
        self.act('accept')
        self.act('en_route')
        (notice,) = self.notices()
        self.assertEqual(notice.reply_to, [settings.CUSTOMER_SERVICE_EMAIL])
        self.assertIn('Profit Sports Solutions | Customer Service', notice.body)
        self.assertIn(settings.CUSTOMER_SERVICE_PHONE, notice.body)
        html = notice.alternatives[0][0]
        # The signature's picture is loaded from the website, as in the team's own emails: nothing that looks like an attachment.
        self.assertIn('https://profitsports.solutions/wp-content/uploads/2026/06/email-signature-design.png', html)
        self.assertIn('use your request in our customer portal', html)
        self.assertNotIn('email_signature.png', [part.get_filename() for part in notice.attachments if hasattr(part, 'get_filename')])
        # A staff email is not.
        from tasks.views import send_password_changed_email
        self.tech_user.email = 'tarek@example.com'
        self.tech_user.save(update_fields=['email'])
        send_password_changed_email(self.tech_user)
        staff = mail.outbox[-1]
        self.assertEqual(staff.reply_to, [])
        self.assertNotIn('Customer Service', staff.body)

    def test_one_standard_their_request_number_in_both_languages(self):
        """tasks.customer_emails: their request's number (not our visit's), English and Arabic
        for a customer in an Arab country, the inbox preview, one request's emails grouped."""
        from tasks.models import CustomerTicket
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0901', customer=self.customer, site=self.site,
            company_name='Fitness First', contact_name='Ali', contact_phone='0501234567',
            description='-', submitted_at=timezone.now(), task=self.task, status=CustomerTicket.Status.CONVERTED,
        )
        self.act('accept')
        self.act('en_route')
        (notice,) = self.notices()
        self.assertIn('Request AE-T0901', notice.subject)
        self.assertNotIn(self.task.task_number, notice.subject)
        self.assertIn('الطلب AE-T0901', notice.subject)
        self.assertIn('Dear ', notice.body)
        self.assertIn('في الطريق', notice.body)
        self.assertEqual(notice.extra_headers['References'], '<request-AE-T0901@profitsports.solutions>')
        html = notice.alternatives[0][0]
        self.assertIn('dir="rtl"', html)
        self.assertIn('mso-hide:all', html)  # the preview line
        self.assertIn('للرد، يرجى استخدام طلبكم', html)  # the footer, in Arabic too
        self.assertIn('للرد، يرجى استخدام طلبكم', notice.body)
        # The signature stays left to right inside an Arabic email.
        self.assertIn('dir="ltr" width="600"', html)

    def test_a_visit_from_no_request_keeps_its_own_number(self):
        self.act('accept')
        self.act('en_route')
        (notice,) = self.notices()
        self.assertIn(f'Visit {self.task.task_number}', notice.subject)

    def test_the_customer_can_switch_the_email_off(self):
        from customers.models import CustomerNotification
        self.customer.notify_on_the_way = False
        self.customer.save(update_fields=['notify_on_the_way'])
        self.act('accept')
        self.act('en_route')
        self.assertEqual(self.notices(), [])
        self.assertTrue(CustomerNotification.objects.filter(task=self.task, kind='on_the_way').exists())  # the bell still

    def test_never_for_our_own_warehouses(self):
        self.customer.is_internal = True
        self.customer.save(update_fields=['is_internal'])
        self.act('accept')
        self.act('en_route')
        self.assertEqual(self.notices(), [])


class PartsNeededApiTests(ApiTestCase):
    """The repair's parts from the app, as on the website: its owner adds and
    changes them; the warehouse sees the ones all in, and marks them prepared;
    the technician sees them, and the report starts with them."""

    def setUp(self):
        super().setUp()
        from people.models import Technician
        self.task.responsible_supervisor = Technician.objects.get(user__username='supervisor1')
        self.task.save(update_fields=['responsible_supervisor'])
        self.url = f'/api/tasks/{self.task.pk}/parts-needed/'

    def test_an_edit_without_a_part_adds_nothing(self):
        from people.models import Technician
        Technician.objects.create(user=User.objects.create_user('manager7', password='pass12345'), country=self.country,
                                  full_name='Mona Manager', language='en', role=Technician.Role.MANAGER,
                                  employment_type='staff')
        response = self.client.post(self.url, {'action': 'edit', 'product_code': 'X-1', 'quantity': 1, 'source': 'ordered'},
                                    **self.auth(self.token_for('manager7')))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.task.parts_needed.exists())

    def test_the_owner_adds_and_the_warehouse_prepares(self):
        from people.models import Technician
        owner = self.token_for('supervisor1')
        added = self.client.post(self.url, {'product_code': 'BELT-01', 'description': 'Belt', 'quantity': 2, 'source': 'ordered'},
                                 **self.auth(owner))
        self.assertEqual(added.status_code, 200, added.content)
        (line,) = added.json()['parts_needed']
        self.assertEqual((line['state'], added.json()['can_prepare_parts']), ('waiting', False))
        # A technician can't change them.
        self.assertEqual(self.client.post(self.url, {'line': line['id'], 'action': 'arrived'},
                                          **self.auth(self.token_for('tech1'))).status_code, 403)
        self.client.post(self.url, {'line': line['id'], 'action': 'arrived'}, **self.auth(owner))
        warehouse = User.objects.create_user('wh1', password='pass12345')
        Technician.objects.create(user=warehouse, country=self.country, full_name='Wael Warehouse', language='en',
                                  role=Technician.Role.WAREHOUSE_MANAGER, employment_type='staff')
        token = self.token_for('wh1')
        (row,) = self.client.get('/api/parts-to-prepare/', **self.auth(token)).json()
        self.assertEqual(row['task_id'], self.task.pk)
        self.assertEqual(self.client.post(f'/api/tasks/{self.task.pk}/parts-prepared/', **self.auth(token)).status_code, 200)
        self.assertEqual(self.client.get('/api/parts-to-prepare/', **self.auth(token)).json(), [])
        self.assertEqual(self.client.get('/api/parts-to-prepare/', **self.auth(self.token_for('tech1'))).status_code, 403)
        # The technician sees them, prepared; the report starts with them.
        detail = self.client.get(f'/api/my-tasks/{self.task.pk}/', **self.auth(self.token_for('tech1'))).json()
        self.assertEqual(([part['code'] for part in detail['parts_needed']], detail['parts_prepared']), (['BELT-01'], True))
