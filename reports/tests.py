from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Brand, Country
from tasks.models import Task, TaskAsset, TaskAssignment, TaskEvent

from .models import WorkReport

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
