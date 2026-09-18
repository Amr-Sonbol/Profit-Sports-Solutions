from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from customers.models import Customer, Site
from people.models import Technician
from reference.models import Country
from tasks.models import Task, TaskAssignment

from .models import CustomerFeedback, WorkReport

User = get_user_model()


class FeedbackFormTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(
            name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
        )
        self.customer = Customer.objects.create(country=self.country, name='Fitness First', segment='gym')
        self.site = Site.objects.create(customer=self.customer, name='Marina Branch', address='Dubai Marina')

        self.supervisor_user = User.objects.create_user('supervisor1', password='pass12345')
        Technician.objects.create(
            user=self.supervisor_user, country=self.country, full_name='Sara Super',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )

        self.tech_user = User.objects.create_user('tech1', password='pass12345')
        self.technician = Technician.objects.create(
            user=self.tech_user, country=self.country, full_name='Tarek Tech',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.CLOSED,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.report = WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        self.feedback = CustomerFeedback.objects.create(
            task=self.task, requested_at=timezone.now(), requested_by=self.supervisor_user,
        )
        self.url = f'/reports/feedback/{self.feedback.token}/'

    def test_anonymous_can_view_the_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_unknown_token_gives_404(self):
        response = self.client.get('/reports/feedback/does-not-exist/')
        self.assertEqual(response.status_code, 404)

    def test_submitting_rating_and_comment_saves_them(self):
        response = self.client.post(self.url, {'rating': 4, 'comment': 'Quick and professional.'})
        self.assertEqual(response.status_code, 302)

        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.rating, 4)
        self.assertEqual(self.feedback.comment, 'Quick and professional.')
        self.assertIsNotNone(self.feedback.submitted_at)

    def test_already_submitted_feedback_cannot_be_overwritten(self):
        self.client.post(self.url, {'rating': 5, 'comment': 'Great.'})
        self.client.post(self.url, {'rating': 1, 'comment': 'Changed my mind.'})

        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.rating, 5)
        self.assertEqual(self.feedback.comment, 'Great.')
