"""The action matrix: the actions that change who can do what — staff logins,
bringing someone back, approving reports, a task's documents, internal tasks,
creating customers, availability — pressed by every role (and a role made on
Roles & permissions, another country's Manager, a customer, someone signed
out), against the expected result kept in tasks/action_matrix.json, reviewed
against docs/access_rules.md ("not more, not less").

The result is read from the database, not the page: done, done-stepped-in (a
change recorded as stepping in for the owner), no-change, refused, not-found,
sign-in. Each press is undone before the next (a savepoint), so they never
affect each other. Website and app twins must agree (test_web_and_app_agree).

A change to who can do what fails here until it's deliberately approved:

    ACTION_MATRIX_WRITE=1 python manage.py test tasks.test_action_matrix
"""
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from customers.models import Customer, PortalUser
from people.models import CustomRole, RolePermission, Technician
from reference.models import Country
from reports.models import WorkReport
from tasks.models import CustomerTicket, TicketInternalNote, Task, TaskAttachment, TaskEvent
from tasks.test_access_matrix import ROLES
from tasks.tests import TaskTestCase

User = get_user_model()
EXPECTED = Path(__file__).with_name('action_matrix.json')
ACTORS = ['anonymous', *ROLES, 'custom_manager', 'egypt_manager', 'customer']


def login_active(pk):
    return User.objects.filter(technician__pk=pk, is_active=True).exists()


class ActionMatrixTests(TaskTestCase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.users = {'technician': self.tech_user, 'supervisor': self.supervisor_user}
        for role in ROLES:
            if role not in self.users:
                self.users[role] = self.person(role, role)
        # A role made on Roles & permissions, based on Manager, with every one of the Manager's switches.
        custom = CustomRole.objects.create(name='Coordinator', based_on=Technician.Role.MANAGER)
        for row in RolePermission.objects.filter(role=Technician.Role.MANAGER):
            RolePermission.objects.update_or_create(role=custom.key, permission=row.permission,
                                                    defaults={'allowed': row.allowed})
        self.users['custom_manager'] = self.person('custom_manager', Technician.Role.MANAGER, custom_role=custom)
        egypt = Country.objects.create(name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP')
        self.users['egypt_manager'] = self.person('egypt_manager', Technician.Role.MANAGER, country=egypt)
        self.users['customer'] = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.users['customer'])

        # What the actions are done to.
        self.ids = {
            'operations_manager': self.users['operations_manager'].technician.pk,
            'coo': self.users['coo'].technician.pk,
            'technician': self.technician.pk,
            'egypt_supervisor': self.person('egypt_supervisor', Technician.Role.SUPERVISOR, country=egypt).technician.pk,
        }
        left = self.person('left_technician', Technician.Role.TECHNICIAN)
        left.is_active = False
        left.save()
        left.technician.is_active = False
        left.technician.save()
        self.ids['left'] = left.technician.pk
        task_kwargs = dict(site=self.site, task_type=self.task_type, priority=Task.Priority.NORMAL,
                           source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=now,
                           created_by=self.supervisor_user, responsible_supervisor=self.supervisor_user.technician)
        review = Task.objects.create(task_number='AE-0101', status=Task.Status.PENDING_SUPERVISOR_REVIEW, **task_kwargs)
        WorkReport.objects.create(task=review, resolved=True, labour_hours=1, customer_name='Ali', submitted_at=now)
        self.ids['review_task'] = review.pk
        self.ids['own_task'] = Task.objects.create(task_number='AE-0102', **task_kwargs).pk
        self.ids['internal'] = CustomerTicket.objects.create(
            ticket_number='AE-T0101', kind=CustomerTicket.Kind.INTERNAL, subject='Order belts', country=self.country,
            company_name='Profit', contact_name='-', contact_phone='-', description='-', submitted_at=now,
            assigned_to=self.supervisor_user.technician,
        ).pk

    def person(self, username, role, country=None, custom_role=None):
        user = User.objects.create_user(f'{username}1', password='pass12345')
        Technician.objects.create(user=user, country=country or self.country, full_name=username.replace('_', ' ').title(),
                                  language='en', role=role, employment_type='staff', custom_role=custom_role)
        return user

    # ---- the actions: (press it, what changed) ----
    def actions(self):
        ids = self.ids

        def staff(target, action):
            return (lambda: self.client.post('/tasks/technicians/accounts/', {'technician': ids[target], 'action': action}),
                    lambda: login_active(ids[target]))

        def api(path, data):
            return lambda: self.client.post(path, data, content_type='application/json', **self.token_header())

        return {
            'staff_disable_operations_manager': staff('operations_manager', 'disable'),
            'staff_disable_coo': staff('coo', 'disable'),
            'staff_disable_technician': staff('technician', 'disable'),
            'staff_disable_egypt_supervisor': staff('egypt_supervisor', 'disable'),
            'staff_enable_left_technician': staff('left', 'enable'),
            'api_staff_disable_technician': (
                api(f"/api/staff-accounts/{ids['technician']}/", {'action': 'disable'}),
                lambda: login_active(ids['technician'])),
            'reactivate_left_technician': (
                lambda: self.client.post('/tasks/technicians/', {'action': 'reactivate', 'technician_id': ids['left']}),
                lambda: Technician.objects.get(pk=ids['left']).is_active),
            'approve_report_as_supervisor': (
                lambda: self.client.post(f"/tasks/{ids['review_task']}/", {'action': 'approve_report_supervisor'}),
                lambda: Task.objects.get(pk=ids['review_task']).status),
            'api_approve_report_as_supervisor': (
                api(f"/api/tasks/{ids['review_task']}/approve/", {}),
                lambda: Task.objects.get(pk=ids['review_task']).status),
            'upload_task_document': (
                lambda: self.client.post(f"/tasks/{ids['own_task']}/", {
                    'action': 'upload_document', 'purpose': TaskAttachment.Purpose.OTHER,
                    'file': SimpleUploadedFile('note.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
                }),
                lambda: TaskAttachment.objects.filter(task_id=ids['own_task']).count()),
            'internal_task_note': (
                lambda: self.client.post(f"/tasks/tickets/internal/{ids['internal']}/",
                                         {'action': 'add_note', 'message': 'Ordered'}),
                lambda: TicketInternalNote.objects.filter(ticket_id=ids['internal']).count()),
            'api_internal_task_note': (
                api(f"/api/internal-tickets/{ids['internal']}/", {'action': 'note', 'message': 'Ordered'}),
                lambda: TicketInternalNote.objects.filter(ticket_id=ids['internal']).count()),
            'create_customer': (
                lambda: self.client.post('/customers/new/', {
                    'name': 'Brand New Gym', 'segment': Customer.Segment.GYM, 'country': self.country.pk,
                    'branch_name': 'Main', 'branch_address': 'Dubai', 'confirmed_new': 'on',
                }),
                lambda: Customer.objects.filter(name='Brand New Gym').exists()),
            'mark_technician_unavailable': (
                lambda: self.client.post('/tasks/technicians/availability/', {
                    'action': 'mark_unavailable', 'technician_id': ids['technician'], 'reason': 'sick',
                }),
                lambda: Technician.objects.get(pk=ids['technician']).is_available),
        }

    def token_header(self):
        user = getattr(self, '_actor', None)
        if user is None:
            return {}
        return {'HTTP_AUTHORIZATION': f'Token {Token.objects.get_or_create(user=user)[0].key}'}

    def press(self, press, probe):
        stepped_in = TaskEvent.objects.filter(event_type=TaskEvent.EventType.STEPPED_IN).count()
        before = probe()
        response = press()
        if probe() != before:
            if TaskEvent.objects.filter(event_type=TaskEvent.EventType.STEPPED_IN).count() > stepped_in:
                return 'done-stepped-in'
            return 'done'
        code = response.status_code
        if code in (401,) or (code == 302 and 'login' in response.get('Location', '')):
            return 'sign-in'
        return {403: 'refused', 404: 'not-found'}.get(code, 'no-change')

    def matrix(self):
        found = {}
        for name, (press, probe) in self.actions().items():
            for who in ACTORS:
                self._actor = self.users.get(who)
                self.client.logout()
                if self._actor is not None:
                    self.client.force_login(self._actor)
                savepoint = transaction.savepoint()
                try:
                    found.setdefault(name, {})[who] = self.press(press, probe)
                finally:
                    transaction.savepoint_rollback(savepoint)
                    self.client.logout()
        return found

    def test_every_action_by_every_role(self):
        found = self.matrix()
        if os.environ.get('ACTION_MATRIX_WRITE'):
            EXPECTED.write_text(json.dumps(found, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        self.assertEqual(found, json.loads(EXPECTED.read_text(encoding='utf-8')))

    def test_a_country_manager_creates_customers_in_their_own_country(self):
        self.client.force_login(self.users['egypt_manager'])
        self.client.post('/customers/new/', {
            'name': 'Cairo Fitness', 'segment': Customer.Segment.GYM, 'country': self.country.pk,
            'branch_name': 'Main', 'branch_address': 'Cairo', 'confirmed_new': 'on',
        })
        self.assertEqual(Customer.objects.get(name='Cairo Fitness').country.iso_code, 'EG')

    def test_web_and_app_agree(self):
        found = json.loads(EXPECTED.read_text(encoding='utf-8'))
        for web, app in (('staff_disable_technician', 'api_staff_disable_technician'),
                         ('approve_report_as_supervisor', 'api_approve_report_as_supervisor'),
                         ('internal_task_note', 'api_internal_task_note')):
            for who in ACTORS:
                if who in ('anonymous', 'customer'):
                    continue  # signed out / a customer: the website and the app say so differently
                blocked = ('refused', 'not-found', 'no-change')
                self.assertEqual(found[web][who] in blocked, found[app][who] in blocked, f'{web} vs {app} for {who}')
