from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from people.models import Technician
from reports.models import CustomerFeedback, WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment
from tasks.tests import TaskTestCase

User = get_user_model()

# Covered elsewhere: the Django admin, the token-authenticated mobile API
# (api/tests.py), and the password-reset confirm link (its uidb64/token
# can't be faked here — Django's own views).
SKIPPED_PREFIXES = ('admin/', 'api/', 'accounts/reset/')


def _all_page_routes():
    """Every (route, url name) in the URLconf, walked recursively — so a
    page added later is smoke-tested without anyone listing it here."""
    def walk(patterns, prefix='', namespace=''):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                ns = pattern.namespace or namespace
                yield from walk(pattern.url_patterns, route, ns)
            elif isinstance(pattern, URLPattern) and pattern.name:
                name = f'{namespace}:{pattern.name}' if namespace else pattern.name
                yield route, name
    return [(r, n) for r, n in walk(get_resolver().url_patterns) if not r.startswith(SKIPPED_PREFIXES)]


@override_settings(AXES_ENABLED=False)
class PageSmokeTests(TaskTestCase):
    """Open every page in the web app as every kind of user and check none
    of them crashes (a 500). Refusals (403/404) and redirects are fine —
    whether each role is *allowed* in is checked by the feature tests;
    this only guarantees nothing blows up before production."""

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, task_type=self.task_type, brand=self.brand,
            priority=Task.Priority.NORMAL, source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=self.task, findings='Belt worn', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali', submitted_at=timezone.now(),
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First',
            site_description='Marina Branch', contact_name='Ali', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        self.feedback = CustomerFeedback.objects.create(
            task=self.task, requested_at=timezone.now(), requested_by=self.supervisor_user,
        )
        self.portal_user = User.objects.create_user('portal1', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])

    def _kwargs_for(self, route, name):
        """Real objects for each URL's placeholders."""
        kwargs = {}
        if '<int:pk>' in route:
            if name.startswith('customers:site_'):
                kwargs['pk'] = self.site.pk
            elif name.startswith('customers:'):
                kwargs['pk'] = self.customer.pk
            elif name.startswith('tasks:technician_'):
                kwargs['pk'] = self.technician.pk
            elif name == 'tasks:machine_detail':
                kwargs['pk'] = self.asset.pk
            elif name == 'tasks:ticket_review':
                kwargs['pk'] = self.ticket.pk
            else:
                kwargs['pk'] = self.task.pk
        if '<str:token>' in route:
            kwargs['token'] = self.feedback.token if name.startswith('reports:') else self.ticket.token
        return kwargs

    def _users(self):
        users = [('signed out', None), ('customer', 'portal1')]
        for role in Technician.Role:
            username = f'smoke_{role.value}'
            user = User.objects.create_user(username, password='pass12345')
            Technician.objects.create(
                user=user, country=self.country, full_name=f'Smoke {role.label}',
                language='en', role=role, employment_type='staff',
            )
            users.append((role.label, username))
        users.append(('technician, Arabic', 'tech1'))
        return users

    def test_no_page_crashes_for_anyone(self):
        from django.urls import reverse

        routes = _all_page_routes()
        self.assertGreater(len(routes), 50)
        for who, username in self._users():
            self.client.logout()
            if username:
                self.client.login(username=username, password='pass12345')
            for route, name in routes:
                url = reverse(name, kwargs=self._kwargs_for(route, name))
                with self.subTest(user=who, url=url):
                    response = self.client.get(url)
                    self.assertLess(response.status_code, 500)
