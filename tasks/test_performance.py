"""Query budgets: the busy screens' database queries don't grow with the data.

Each screen is opened, then the data grows — more tasks, technicians, history,
requests, customers' visits — and it's opened again: the number of queries must
stay the same. A screen that asks once per row (an "N+1") fails here long before
it's slow on the live server (docs/go_live_plan.md: performance).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token

from customers.models import PortalUser, Site
from people.models import RolePermission, Technician
from reports.models import WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskEvent, TaskNotification
from tasks.tests import TaskTestCase

User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False)
class QueryBudgetTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        RolePermission.objects.update_or_create(
            role=Technician.Role.MANAGER, permission=RolePermission.Permission.VIEW_DASHBOARD, defaults={'allowed': True},
        )
        for permission in (RolePermission.Permission.VIEW_TECHNICIANS, RolePermission.Permission.MANAGE_TICKETS):
            RolePermission.objects.update_or_create(
                role=Technician.Role.MANAGER, permission=permission, defaults={'allowed': True},
            )
        self.manager = Technician.objects.create(
            user=User.objects.create_user('manager1', password='pass12345'), country=self.country,
            full_name='Mona Manager', language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.portal_user = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.portal_user)
        self.made = 0
        self.grow()

    def grow(self, tasks=4):
        """More of everything a busy screen lists: a technician, their tasks (open, done,
        reported, with taps today), a request, new-task notices for the bell."""
        self.made += 1
        user = User.objects.create_user(f'grow{self.made}')
        technician = Technician.objects.create(
            user=user, country=self.country, full_name=f'Tech {self.made}', language='en',
            role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        site = Site.objects.create(customer=self.customer, name=f'Branch {self.made}', address='Dubai')
        now = timezone.now()
        for number in range(tasks):
            status = [Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.COMPLETED, Task.Status.CLOSED][number % 4]
            task = Task.objects.create(
                task_number=f'AE-{self.made:02d}{number:02d}', site=site, priority=Task.Priority.NORMAL,
                source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=now - timedelta(days=number),
                created_by=self.supervisor_user, status=status, scheduled_for=now + timedelta(hours=number),
            )
            for who in (technician, self.technician):
                TaskAssignment.objects.create(
                    task=task, technician=who, assigned_at=now, is_active=True,
                    role=TaskAssignment.Role.LEAD if who == technician else TaskAssignment.Role.HELPER,
                )
            TaskEvent.objects.create(task=task, event_type=TaskEvent.EventType.CREATED, occurred_at=now, actor=user)
            TaskEvent.objects.create(task=task, event_type=TaskEvent.EventType.EN_ROUTE, occurred_at=now, actor=user)
            if status in (Task.Status.COMPLETED, Task.Status.CLOSED):
                WorkReport.objects.create(task=task, resolved=True, labour_hours=2, customer_name='Ali', submitted_at=now)
                TaskEvent.objects.create(task=task, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=now,
                                         actor=user)
            TaskNotification.objects.create(task=task, created_at=now)
        CustomerTicket.objects.create(
            country=self.country, ticket_number=f'AE-T{self.made:04d}', customer=self.customer, site=site,
            company_name='Fitness First', site_description=site.name, contact_name='Ali', contact_phone='0501234567',
            description='-', submitted_at=now,
        )

    def queries(self, open_screen):
        with CaptureQueriesContext(connection) as captured:
            response = open_screen()
        self.assertEqual(response.status_code, 200, getattr(response, 'content', b'')[:300])
        return len(captured)

    def assert_flat(self, open_screen):
        """The same number of queries with twice the data (and more again)."""
        open_screen()  # first-use lookups (caches) aside
        before = self.queries(open_screen)
        self.grow()
        self.grow()
        after = self.queries(open_screen)
        self.assertEqual(after, before, f'{after - before} more queries with more data: one per row somewhere')

    def web(self, username, url):
        self.client.login(username=username, password='pass12345')
        return lambda: self.client.get(url)

    def app(self, user, url):
        token, _ = Token.objects.get_or_create(user=user)
        return lambda: self.client.get(url, HTTP_AUTHORIZATION=f'Token {token.key}')

    # ---- the website ----
    def test_web_dashboard(self):
        self.assert_flat(self.web('manager1', '/tasks/dashboard/'))

    def test_web_team_progress(self):
        self.assert_flat(self.web('manager1', '/tasks/technicians/progress/'))

    def test_web_task_list(self):
        self.assert_flat(self.web('manager1', '/tasks/'))

    def test_web_ticket_list(self):
        self.assert_flat(self.web('manager1', '/tasks/tickets/'))

    def test_web_technician_list(self):
        self.assert_flat(self.web('manager1', '/tasks/technicians/'))

    def test_web_my_schedule(self):
        self.assert_flat(self.web('tech1', '/tasks/my-week/'))

    # ---- the app ----
    def test_app_dashboard(self):
        self.assert_flat(self.app(self.manager.user, '/api/dashboard/'))

    def test_app_team_progress(self):
        self.assert_flat(self.app(self.manager.user, '/api/team-progress/'))

    def test_app_team_today(self):
        self.assert_flat(self.app(self.manager.user, '/api/team-today/'))

    def test_app_tasks_page(self):
        self.assert_flat(self.app(self.manager.user, '/api/tasks/?page=1'))

    def test_app_bell(self):
        self.assert_flat(self.app(self.manager.user, '/api/bell/'))

    def test_app_my_today(self):
        self.assert_flat(self.app(self.tech_user, '/api/my-today/'))

    def test_app_my_tasks(self):
        self.assert_flat(self.app(self.tech_user, '/api/my-tasks/'))

    def test_app_my_schedule(self):
        self.assert_flat(self.app(self.tech_user, '/api/my-schedule/'))

    # ---- the customer portal ----
    def test_portal_home(self):
        self.assert_flat(self.web('ali@fitnessfirst.example', '/customers/portal/'))

    def test_portal_visits(self):
        self.assert_flat(self.web('ali@fitnessfirst.example', '/customers/portal/visits/'))

    def test_portal_requests(self):
        self.assert_flat(self.web('ali@fitnessfirst.example', '/customers/portal/requests/'))
