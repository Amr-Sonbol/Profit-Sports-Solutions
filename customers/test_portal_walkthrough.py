"""What the portal walkthrough found (docs/role_walkthrough.md), kept from coming back."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from customers.models import PortalUser
from tasks.models import Task
from tasks.tests import TaskTestCase

User = get_user_model()


class PortalWalkthroughTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.portal_user)
        self.client.force_login(self.portal_user)
        self.two_days_ago = timezone.localdate() - timedelta(days=2)

    def visit(self, status):
        return Task.objects.create(
            task_number=f'AE-{Task.objects.count() + 1:04d}', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, status=status, scheduled_date=self.two_days_ago,
        )

    def test_a_visit_under_way_keeps_its_day_everywhere(self):
        # On site since two days ago (a job over several days): its day, not "Date to be set".
        task = self.visit(Task.Status.IN_PROGRESS)
        day = self.two_days_ago
        visits = self.client.get('/customers/portal/visits/').content.decode()
        self.assertIn(f'{day:%d} {day:%b} {day:%Y}'.lstrip('0'), visits)
        self.assertNotIn('Date to be set', visits)
        self.assertNotIn('Date to be set', self.client.get(f'/customers/portal/visits/{task.pk}/').content.decode())
        home = self.client.get('/customers/portal/')
        self.assertEqual(home.context['next_visit'], task)
        self.assertContains(home, 'Your visit now')

    def test_a_day_that_passed_with_the_work_not_done_is_to_be_set(self):
        self.visit(Task.Status.ASSIGNED)
        self.assertContains(self.client.get('/customers/portal/visits/'), 'Date to be set')
        self.assertIsNone(self.client.get('/customers/portal/').context['next_visit'])

    def test_a_request_without_a_machine_reopens_on_the_machine_step(self):
        page = self.client.post('/customers/portal/tickets/new/', {
            'site': self.site.pk, 'about': 'machines', 'need': 'repair', 'contact_name': 'Ali',
            'contact_phone': '0501234567', 'machines-TOTAL_FORMS': '1', 'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0', 'machines-MAX_NUM_FORMS': '10',
        }).content.decode()
        # The server's message sits on the machine step (static/js/report-steps.js opens it there),
        # not only at the top where the steps can't point to it.
        self.assertIn('<p class="field__error">Please add at least one machine.</p>', page)
        self.assertIn('Choose the branch', page)
        self.assertNotIn('Select…', page)
