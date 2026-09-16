from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Brand, Country
from tasks.models import Task, TaskAsset, TaskAssignment, TaskEvent

from .models import CustomerFeedback, WorkReport

User = get_user_model()


class ReportReviewTestCase(TestCase):
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
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.COMPLETED,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.report = WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        self.url = f'/reports/{self.report.pk}/'


class ReportListTests(ReportReviewTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 403)

    def test_pending_report_shows_by_default(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/reports/')
        self.assertContains(response, 'AE-0001')

    def test_approved_report_is_excluded_from_pending(self):
        self.report.approved_at = timezone.now()
        self.report.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/reports/')
        self.assertNotContains(response, 'AE-0001')

        response = self.client.get('/reports/', {'status': 'approved'})
        self.assertContains(response, 'AE-0001')

    def test_rejected_report_shows_under_rejected_not_pending(self):
        self.report.rejection_reason = 'Missing serial photo'
        self.report.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/reports/')
        self.assertNotContains(response, 'AE-0001')

        response = self.client.get('/reports/', {'status': 'rejected'})
        self.assertContains(response, 'AE-0001')


class ReportReviewTests(ReportReviewTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_approve_closes_task_and_records_event(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve'})
        self.assertEqual(response.status_code, 302)

        self.report.refresh_from_db()
        self.assertIsNotNone(self.report.approved_at)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)

        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.REPORT_APPROVED)
        self.assertEqual(event.actor, self.supervisor_user)

    def test_reject_requires_a_reason(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reject', 'rejection_reason': ''})
        self.assertEqual(response.status_code, 200)

        self.report.refresh_from_db()
        self.assertEqual(self.report.rejection_reason, '')
        self.assertIsNone(self.report.approved_at)

    def test_reject_sets_reason_without_closing_task(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'reject', 'rejection_reason': 'Missing serial plate photo.',
        })
        self.assertEqual(response.status_code, 302)

        self.report.refresh_from_db()
        self.assertEqual(self.report.rejection_reason, 'Missing serial plate photo.')
        self.assertIsNone(self.report.approved_at)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.REPORT_REJECTED)

    def test_approved_report_cannot_be_changed_again(self):
        self.report.approved_at = timezone.now()
        self.report.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'reject', 'rejection_reason': 'Too late.',
        })
        self.assertEqual(response.status_code, 200)

        self.report.refresh_from_db()
        self.assertEqual(self.report.rejection_reason, '')

    def test_helpers_and_machines_covered_are_shown(self):
        helper_user = User.objects.create_user('helper1', password='pass12345')
        helper = Technician.objects.create(
            user=helper_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=self.task, technician=helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        brand = Brand.objects.create(name='Technogym')
        asset = Asset.objects.create(site=self.site, brand=brand, model_name='Excite Run 700')
        TaskAsset.objects.create(task=self.task, asset=asset, outcome=TaskAsset.Outcome.REPAIRED)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)

        self.assertEqual(response.context['helper_technicians'], [helper])
        self.assertContains(response, 'Omar Helper')
        self.assertContains(response, 'Excite Run 700')

    def test_cannot_request_feedback_before_approval(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())

    def test_requesting_feedback_without_a_contact_email_shows_an_error(self):
        self.report.approved_at = timezone.now()
        self.report.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'}, follow=True)

        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())
        self.assertContains(response, 'Add a contact email')

    def test_requesting_feedback_creates_it_and_sends_an_email(self):
        self.report.approved_at = timezone.now()
        self.report.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'})
        self.assertEqual(response.status_code, 302)

        feedback = CustomerFeedback.objects.get(task=self.task)
        self.assertEqual(feedback.requested_by, self.supervisor_user)
        self.assertIsNone(feedback.submitted_at)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['manager@fitnessfirst.example'])
        self.assertIn(feedback.token, mail.outbox[0].body)

    def test_resending_updates_requested_at_without_duplicating(self):
        self.report.approved_at = timezone.now()
        self.report.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        first_request_time = timezone.now() - timedelta(days=1)
        feedback = CustomerFeedback.objects.create(
            task=self.task, requested_at=first_request_time, requested_by=self.supervisor_user,
        )

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'send_feedback_request'})

        self.assertEqual(CustomerFeedback.objects.filter(task=self.task).count(), 1)
        feedback.refresh_from_db()
        self.assertGreater(feedback.requested_at, first_request_time)


class FeedbackFormTests(ReportReviewTestCase):
    def setUp(self):
        super().setUp()
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
