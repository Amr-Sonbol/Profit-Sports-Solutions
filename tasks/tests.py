import tempfile
from unittest.mock import patch
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from customers.models import PortalUser, Asset, Customer, Site
from people.models import (
    RELIABLE_LEVEL, NotificationSettings, PushDevice, RolePermission, Technician, TechnicianConduct,
    TechnicianConductAssessment, TechnicianSkill, TechnicianSkillAssessment,
)
from rest_framework.authtoken.models import Token
from reference.models import Brand, ConductArea, Country, Skill, TaskType
from reports.models import CustomerFeedback, PartUsed, WorkReport

from .location import record_tap
from .models import (
    CustomerTicket, ScheduleChangeRequest, Task, TaskAssignment, TaskAsset, TaskAttachment, TaskEvent,
    TaskMessage, TaskMessageRecipient, TaskNotification, TaskProduct, TicketEscalation, TicketInternalNote,
    TicketMachine, TicketNotification, TicketReply,
)
from spots.test_files import REAL_JPEG, REAL_MP4, REAL_PNG

User = get_user_model()


def dubai_time(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo('Asia/Dubai'))


class TaskTestCase(TestCase):
    """Shared reference/customer/people fixtures for the tasks screens."""

    def setUp(self):
        self.country = Country.objects.create(
            name='UAE', name_ar='الإمارات', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
            weekend_days='5,6',  # Saturday–Sunday, so its week starts on Monday
        )
        self.brand = Brand.objects.create(name='Technogym')
        self.skill = Skill.objects.create(name='Treadmill repair', name_ar='إصلاح جهاز الجري')
        self.task_type = TaskType.objects.create(
            code='pm', name='Preventive maintenance', name_ar='صيانة وقائية', category='maintenance',
        )
        self.customer = Customer.objects.create(country=self.country, name='Fitness First', segment='gym')
        self.site = Site.objects.create(customer=self.customer, name='Marina Branch', address='Dubai Marina')
        self.asset = Asset.objects.create(site=self.site, brand=self.brand, model_name='Excite Run 700')

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


class TaskDetailTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='UAE-0001', site=self.site, task_type=self.task_type, brand=self.brand,
            required_skill=self.skill, min_level=2, description='Belt making noise',
            priority=Task.Priority.HIGH, source=Task.Source.PHONE, billing_type=Task.BillingType.CONTRACT,
            reported_at=timezone.now(), promised_at=timezone.now(),
            created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.CREATED, occurred_at=timezone.now(),
            actor=self.supervisor_user, note='Created from phone call',
        )
        TaskAttachment.objects.create(
            task=self.task, storage_kind=TaskAttachment.StorageKind.LINK, url='https://example.com/photo.jpg',
            media_type=TaskAttachment.MediaType.PHOTO, purpose=TaskAttachment.Purpose.BEFORE,
            source=TaskAttachment.Source.TECHNICIAN, uploaded_by=self.tech_user, uploaded_at=timezone.now(),
        )
        TaskAsset.objects.create(task=self.task, asset=self.asset, outcome=TaskAsset.Outcome.REPAIRED)
        self.report = WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        PartUsed.objects.create(
            report=self.report, part_code='BELT-01', description='Running belt',
            quantity=1, unit_cost='120.00', currency_code='AED',
        )

    def test_a_longer_history_and_more_machines_cost_no_more_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.login(username='supervisor1', password='pass12345')
        url = f'/tasks/{self.task.pk}/'
        self.client.get(url)
        with CaptureQueriesContext(connection) as before:
            self.client.get(url)
        for actor in (self.tech_user, self.supervisor_user, self.tech_user):
            TaskEvent.objects.create(
                task=self.task, event_type=TaskEvent.EventType.CREATED, occurred_at=timezone.now(), actor=actor,
            )
            asset = Asset.objects.create(site=self.site, brand=self.brand, model_name='Excite Run 700')
            TaskAsset.objects.create(task=self.task, asset=asset, outcome=TaskAsset.Outcome.REPAIRED)
        with self.assertNumQueries(len(before)):
            self.client.get(url)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertEqual(response.status_code, 302)

    def test_the_login_page_has_no_back_link_but_other_pages_do(self):
        self.assertNotContains(self.client.get('/accounts/login/'), 'page-back')
        self.assertContains(self.client.get('/accounts/password-reset/'), 'page-back')

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_missing_task_gives_404(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/999999/')
        self.assertEqual(response.status_code, 404)

    def test_other_country_task_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        other_task = Task.objects.create(
            task_number='EG-0001', site=other_site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{other_task.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_supervisor_sees_full_detail(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('UAE-0001', content)
        self.assertIn('Tarek Tech', content)
        self.assertIn('Belt worn out', content)
        self.assertIn('BELT-01', content)

    def test_task_list_links_to_detail(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertContains(response, f'/tasks/{self.task.pk}/')

    def test_supervisor_does_not_see_documents(self):
        """Quotation/factory offer/invoice/delivery note are manager-tier
        — a supervisor sees the customer's own photos/videos, not these.
        """
        self.task.quotation = SimpleUploadedFile('quote.pdf', b'%PDF-1.4', content_type='application/pdf')
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertNotContains(response, 'Quotation')

    def test_manager_sees_documents(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task.quotation = SimpleUploadedFile('quote.pdf', b'%PDF-1.4', content_type='application/pdf')
        self.task.save()

        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(response, 'Quotation')

    def test_supervisor_sees_only_the_invoice_when_made_visible(self):
        # invoice_visible_to_supervisor opens up the invoice specifically
        # — not quotation/factory offer/delivery note — for a supervisor
        # collecting cash on site for this one task.
        self.task.quotation = SimpleUploadedFile('quote.pdf', b'%PDF-1.4', content_type='application/pdf')
        self.task.invoice = SimpleUploadedFile('invoice.pdf', b'%PDF-1.4', content_type='application/pdf')
        self.task.invoice_visible_to_supervisor = True
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(response, 'Invoice')
        self.assertNotContains(response, 'Quotation')

    def test_supervisor_does_not_see_invoice_by_default(self):
        self.task.invoice = SimpleUploadedFile('invoice.pdf', b'%PDF-1.4', content_type='application/pdf')
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertNotContains(response, 'Invoice')

    def test_notify_requires_a_scheduled_time(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_schedule'}, follow=True)

        self.assertIsNone(self.task.schedule_notified_at)
        self.assertContains(response, 'Please set a scheduled time')
        self.assertEqual(len(mail.outbox), 0)

    def test_notify_requires_a_contact_email(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_schedule'}, follow=True)

        self.task.refresh_from_db()
        self.assertIsNone(self.task.schedule_notified_at)
        self.assertContains(response, 'Please add a contact email')
        self.assertEqual(len(mail.outbox), 0)

    def test_notify_sends_email_and_records_who_and_when(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_schedule'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.schedule_notified_at)
        self.assertEqual(self.task.schedule_notified_by, self.supervisor_user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['manager@fitnessfirst.example'])
        self.assertIn('UAE-0001', mail.outbox[0].subject)

    def test_delay_notice_requires_a_reason(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(
            f'/tasks/{self.task.pk}/', {'action': 'notify_delay', 'delay_reason': '  '}, follow=True,
        )

        self.assertContains(response, 'Please state the reason for the delay')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.task.events.filter(event_type=TaskEvent.EventType.DELAY_NOTICE).exists())

    def test_delay_notice_requires_a_contact_email(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(
            f'/tasks/{self.task.pk}/', {'action': 'notify_delay', 'delay_reason': 'Traffic'}, follow=True,
        )

        self.assertContains(response, 'Please add a contact email')
        self.assertEqual(len(mail.outbox), 0)

    def test_delay_notice_sends_email_and_logs_an_event(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {
            'action': 'notify_delay', 'delay_reason': 'Heavy traffic on Sheikh Zayed Road.',
        })
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['manager@fitnessfirst.example'])
        self.assertIn('UAE-0001', mail.outbox[0].subject)
        self.assertIn('Heavy traffic', mail.outbox[0].body)

        event = self.task.events.get(event_type=TaskEvent.EventType.DELAY_NOTICE)
        self.assertEqual(event.note, 'Heavy traffic on Sheikh Zayed Road.')
        self.assertEqual(event.actor, self.supervisor_user)

    def test_technician_cannot_send_a_delay_notice(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {
            'action': 'notify_delay', 'delay_reason': 'Traffic',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_supervisor_can_upload_a_staff_document(self):
        pdf = SimpleUploadedFile('delivery-note.pdf', b'%PDF-1.4 not a real pdf', content_type='application/pdf')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {
            'action': 'upload_document', 'file': pdf, 'purpose': TaskAttachment.Purpose.DELIVERY_NOTE,
        })
        self.assertEqual(response.status_code, 302)

        attachment = self.task.attachments.get(purpose=TaskAttachment.Purpose.DELIVERY_NOTE)
        self.assertEqual(attachment.source, TaskAttachment.Source.SUPERVISOR)
        self.assertEqual(attachment.uploaded_by, self.supervisor_user)
        self.assertEqual(attachment.media_type, TaskAttachment.MediaType.DOCUMENT)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_technician_with_view_tasks_still_cannot_upload_a_staff_document(self):
        # can_upload_staff_document is a fixed role check, independent of
        # the configurable view_tasks permission — granting a technician
        # view_tasks (so they can reach this page at all) must not also
        # open the door to this staff-only action.
        RolePermission.objects.update_or_create(
            role=Technician.Role.TECHNICIAN, permission=RolePermission.Permission.VIEW_TASKS,
            defaults={'allowed': True},
        )
        pdf = SimpleUploadedFile('report.pdf', b'%PDF-1.4 not a real pdf', content_type='application/pdf')

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {
            'action': 'upload_document', 'file': pdf, 'purpose': TaskAttachment.Purpose.WRITTEN_REPORT,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.task.attachments.filter(purpose=TaskAttachment.Purpose.WRITTEN_REPORT).exists())


class TaskDetailFeedbackRequestTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
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
        WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/{self.task.pk}/'

    def test_cannot_request_feedback_before_task_is_closed(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())

    def test_requesting_feedback_without_a_contact_email_shows_an_error(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'}, follow=True)

        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())
        self.assertContains(response, 'Please add a contact email')

    def test_requesting_feedback_creates_it_and_sends_an_email(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'})
        self.assertEqual(response.status_code, 302)

        feedback = CustomerFeedback.objects.get(task=self.task)
        self.assertEqual(feedback.requested_by, self.manager_user)
        self.assertIsNone(feedback.submitted_at)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['manager@fitnessfirst.example'])
        self.assertIn(feedback.token, mail.outbox[0].body)

    def test_resending_updates_requested_at_without_duplicating(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        first_request_time = timezone.now() - timedelta(days=1)
        feedback = CustomerFeedback.objects.create(
            task=self.task, requested_at=first_request_time, requested_by=self.manager_user,
        )

        self.client.login(username='manager1', password='pass12345')
        self.client.post(self.url, {'action': 'send_feedback_request'})

        self.assertEqual(CustomerFeedback.objects.filter(task=self.task).count(), 1)
        feedback.refresh_from_db()
        self.assertGreater(feedback.requested_at, first_request_time)

    def test_technician_cannot_request_feedback(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())

    def test_supervisor_cannot_request_feedback(self):
        """Manager-only, a fixed floor like approve_report/close_directly
        — not configurable via role_permission.
        """
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'send_feedback_request'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())


class TaskEditTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='UAE-0001', site=self.site, task_type=self.task_type, brand=self.brand,
            required_skill=self.skill, min_level=2, description='Belt making noise',
            priority=Task.Priority.HIGH, source=Task.Source.PHONE, billing_type=Task.BillingType.CONTRACT,
            reported_at=timezone.now(), promised_at=timezone.now(),
            created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
        )
        self.url = f'/tasks/{self.task.pk}/edit/'

    def _management_form(self, prefix, total, initial=0):
        return {
            f'{prefix}-TOTAL_FORMS': str(total),
            f'{prefix}-INITIAL_FORMS': str(initial),
            f'{prefix}-MIN_NUM_FORMS': '0',
            f'{prefix}-MAX_NUM_FORMS': '1000',
        }

    def _payload(self, **overrides):
        payload = {
            'priority': Task.Priority.HIGH, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CONTRACT, 'description': 'Belt making noise', 'is_warranty': '',
            **self._management_form('products', 0),
        }
        payload.update(overrides)
        return payload

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_moving_a_task_into_its_teams_other_job_warns(self):
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        other = Task.objects.create(
            task_number='UAE-0002', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED, scheduled_for=dubai_time(2026, 9, 21, 9), estimated_hours=2,
        )
        TaskAssignment.objects.create(
            task=other, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(scheduled_for='2026-09-21T10:00'), follow=True)
        warnings = [str(m.message) for m in response.context['messages'] if m.level_tag == 'warning']
        self.assertEqual(len(warnings), 1)
        self.assertIn('UAE-0002', warnings[0])

    def test_a_weekend_visit_is_a_reminder_unless_its_an_emergency(self):
        # The test UAE's weekend is Saturday–Sunday; 2026-09-19 is a Saturday.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(scheduled_for='2026-09-19T09:00'), follow=True)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)  # saved all the same
        warnings = [m.message for m in response.context['messages'] if m.level_tag == 'warning']
        self.assertEqual(len(warnings), 1)
        self.assertIn('weekend work is normally for emergencies only', warnings[0])

        response = self.client.post(self.url, self._payload(
            scheduled_for='2026-09-20T09:00', priority=Task.Priority.EMERGENCY,
        ), follow=True)
        self.assertFalse([m for m in response.context['messages'] if m.level_tag == 'warning'])

    def test_get_shows_current_values(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Belt making noise')

    def test_supervisor_form_has_no_document_fields(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        for field_name in [
            'quotation', 'factory_offer', 'invoice', 'invoice_visible_to_supervisor', 'delivery_note',
        ]:
            self.assertNotIn(field_name, response.context['form'].fields)

    def test_manager_makes_the_invoice_visible_to_the_supervisor(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, self._payload(invoice_visible_to_supervisor='on'))
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertTrue(self.task.invoice_visible_to_supervisor)

    def test_other_country_task_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        other_task = Task.objects.create(
            task_number='EG-0001', site=other_site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{other_task.pk}/edit/')
        self.assertEqual(response.status_code, 404)

    def test_updating_priority_and_description(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(
            priority=Task.Priority.EMERGENCY, description='Belt snapped completely.',
        ))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.priority, Task.Priority.EMERGENCY)
        self.assertEqual(self.task.description, 'Belt snapped completely.')

    def test_rescheduling_creates_a_rescheduled_event(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(scheduled_for='2026-09-20T09:00'))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)
        self.assertTrue(
            self.task.events.filter(event_type=TaskEvent.EventType.RESCHEDULED).exists(),
        )

    def test_unrelated_edit_does_not_create_a_rescheduled_event(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(priority=Task.Priority.LOW))

        self.assertFalse(
            self.task.events.filter(event_type=TaskEvent.EventType.RESCHEDULED).exists(),
        )

    def test_first_edit_of_a_timed_visit_without_its_day_is_no_reschedule(self):
        # A visit made from a customer's request has its time but not yet
        # scheduled_date; saving the form fills that in — the customer
        # mustn't be told their visit was rescheduled.
        self.task.scheduled_for = dubai_time(2026, 9, 21, 10)
        self.task.save(update_fields=['scheduled_for'])
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        settings = NotificationSettings.load()
        settings.auto_notify_on_reschedule = True
        settings.save()

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(scheduled_for='2026-09-21T10:00', priority=Task.Priority.LOW))

        self.task.refresh_from_db()
        self.assertEqual(self.task.scheduled_date, date(2026, 9, 21))
        self.assertFalse(self.task.events.filter(event_type=TaskEvent.EventType.RESCHEDULED).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_min_level_above_scale_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(min_level='5'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('min_level'))

    def test_rescheduling_does_not_auto_notify_when_setting_is_off(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        NotificationSettings.objects.update_or_create(pk=1, defaults={'auto_notify_on_reschedule': False})

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(scheduled_for='2026-09-20T09:00'))

        self.task.refresh_from_db()
        self.assertIsNone(self.task.schedule_notified_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_rescheduling_auto_notifies_when_setting_is_on(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        settings = NotificationSettings.load()
        settings.auto_notify_on_reschedule = True
        settings.save()

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(scheduled_for='2026-09-20T09:00'))

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.schedule_notified_at)
        self.assertEqual(self.task.schedule_notified_by, self.supervisor_user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['manager@fitnessfirst.example'])

    def test_auto_notify_does_not_fire_without_a_contact_email(self):
        settings = NotificationSettings.load()
        settings.auto_notify_on_reschedule = True
        settings.save()

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(scheduled_for='2026-09-20T09:00'))

        self.task.refresh_from_db()
        self.assertIsNone(self.task.schedule_notified_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_auto_notify_does_not_fire_for_an_unrelated_edit(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        settings = NotificationSettings.load()
        settings.auto_notify_on_reschedule = True
        settings.save()

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(priority=Task.Priority.LOW))

        self.task.refresh_from_db()
        self.assertIsNone(self.task.schedule_notified_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_updating_pak_and_tracking_via_edit(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(
            pak_reference_number='PAK-42', shipping_tracking_number='TRACK-99',
        ))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.pak_reference_number, 'PAK-42')
        self.assertEqual(self.task.shipping_tracking_number, 'TRACK-99')

    def test_adding_products_via_edit(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(**{
            **self._management_form('products', 2),
            'products-0-product_code': 'PNL-100', 'products-0-serial_number': 'SN-1', 'products-0-quantity': '2',
            'products-1-product_code': 'PNL-200', 'products-1-serial_number': '', 'products-1-quantity': '1',
        }))
        self.assertEqual(response.status_code, 302)

        products = list(self.task.products.order_by('product_code'))
        self.assertEqual(len(products), 2)
        self.assertEqual(products[0].product_code, 'PNL-100')
        self.assertEqual(products[0].serial_number, 'SN-1')
        self.assertEqual(products[0].quantity, 2)
        self.assertEqual(products[1].product_code, 'PNL-200')
        self.assertEqual(products[1].serial_number, '')

    def test_product_row_requires_a_code(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': '', 'products-0-serial_number': 'SN-1', 'products-0-quantity': '1',
        }))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.task.products.exists())

    def test_product_row_defaults_quantity_to_one(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'PNL-100', 'products-0-serial_number': '', 'products-0-quantity': '',
        }))
        self.assertEqual(response.status_code, 302)

        product = self.task.products.get()
        self.assertEqual(product.quantity, 1)

    def test_a_machine_on_the_task_is_corrected_in_place_and_new_rows_add(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'PNL-OLD', 'products-0-serial_number': '', 'products-0-quantity': '1',
        }))
        line = self.task.products.get()

        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 2),
            'products-0-line_id': str(line.pk), 'products-0-product_code': 'PNL-FIXED', 'products-0-quantity': '1',
            'products-1-product_code': 'PNL-EXTRA', 'products-1-quantity': '1',
        }))

        self.assertEqual(list(self.task.products.order_by('pk').values_list('pk', 'product_code')),
                         [(line.pk, 'PNL-FIXED'), (self.task.products.get(product_code='PNL-EXTRA').pk, 'PNL-EXTRA')])

    def _replace_products_with(self, code):
        line = self.task.products.first()
        return self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-line_id': str(line.pk) if line else '',
            'products-0-product_code': code, 'products-0-serial_number': '', 'products-0-quantity': '1',
        }))

    def test_supervisor_cannot_change_products_once_the_task_is_finished(self):
        TaskProduct.objects.create(task=self.task, product_code='PNL-OLD', quantity=1)
        self.task.status = Task.Status.COMPLETED
        self.task.save(update_fields=['status'])

        self.client.login(username='supervisor1', password='pass12345')
        self.assertContains(self.client.get(self.url), 'can only be changed by a manager or admin')
        response = self._replace_products_with('PNL-NEW')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.task.products.values_list('product_code', flat=True)), ['PNL-OLD'])

    def test_manager_can_still_change_products_on_a_finished_task(self):
        TaskProduct.objects.create(task=self.task, product_code='PNL-OLD', quantity=1)
        self.task.status = Task.Status.CLOSED
        self.task.save(update_fields=['status'])
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

        self.client.login(username='manager1', password='pass12345')
        self._replace_products_with('PNL-NEW')
        self.assertEqual(list(self.task.products.values_list('product_code', flat=True)), ['PNL-NEW'])

    def test_product_row_saves_the_four_colours_black_unless_custom(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'BENCH-01', 'products-0-serial_number': 'SN-1', 'products-0-quantity': '1',
            'products-0-frame': 'Matte black', 'products-0-arm': 'Red', 'products-0-padding': '', 'products-0-trim': '',
        }))
        self.assertEqual(response.status_code, 302)
        product = self.task.products.get()
        self.assertEqual((product.frame, product.arm, product.padding, product.trim), ('Matte black', 'Red', 'Black', 'Black'))

    def test_planning_an_installation_makes_one_line_per_machine(self):
        self.task_type.category = TaskType.Category.INSTALLATION
        self.task_type.save(update_fields=['category'])
        self.task.task_type = self.task_type
        self.task.save(update_fields=['task_type'])
        plan = self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'BENCH-01', 'products-0-quantity': '3', 'task_type': str(self.task_type.pk),
        })
        # An installation's machines: the Manager's (or the Project Manager's, the Admin's) — a supervisor views.
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, plan)
        self.assertFalse(self.task.products.exists())
        Technician.objects.create(user=User.objects.create_user('manager8', password='pass12345'), country=self.country,
                                  full_name='Mona Manager', language='en', role=Technician.Role.MANAGER,
                                  employment_type='staff')
        self.assertTrue(self.client.login(username='manager8', password='pass12345'))
        response = self.client.post(self.url, plan)
        self.assertEqual(response.status_code, 302)
        lines = list(self.task.products.all())
        self.assertEqual([(line.product_code, line.quantity, line.status) for line in lines],
                         [('BENCH-01', 1, TaskProduct.Status.PENDING)] * 3)

    def test_no_one_removes_a_machine_once_it_is_on_the_task(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'BENCH-01', 'products-0-serial_number': 'SN-1',
            'products-0-quantity': '1', 'products-0-frame': 'Matte black',
        }))
        line = self.task.products.get()
        line.status = TaskProduct.Status.DONE  # checked off on site
        line.save(update_fields=['status'])
        self.assertNotContains(self.client.get(self.url), 'DELETE')  # no Delete on the page

        # A forged Delete, a blanked row, or the row left out entirely: the machine stays, with its status.
        for rows in (
            {'products-0-line_id': str(line.pk), 'products-0-product_code': 'BENCH-01', 'products-0-quantity': '1',
             'products-0-DELETE': 'on'},
            {'products-0-line_id': str(line.pk), 'products-0-product_code': '', 'products-0-quantity': ''},
            {},
        ):
            with self.subTest(rows=rows):
                response = self.client.post(self.url, self._payload(**{**self._management_form('products', 1), **rows}))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(list(self.task.products.values_list('pk', 'status')), [(line.pk, TaskProduct.Status.DONE)])

    def test_searching_tasks_by_product_serial_number(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'BENCH-01', 'products-0-serial_number': 'SN-TRACE-1',
            'products-0-quantity': '1',
        }))

        response = self.client.get('/tasks/', {'status': 'all', 'serial_number': 'SN-TRACE-1'})
        self.assertContains(response, self.task.task_number)


class TaskScheduleLockingTests(TaskTestCase):
    """Two-mode scheduling: a manager can lock a task to a day+time (only
    a manager can then move it, a supervisor must request a change) or
    leave it day-only (any supervisor who can edit the task can add the
    time themselves, no approval needed). A supervisor scheduling their
    own task from scratch, with no manager involved, is unaffected.
    """

    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='UAE-0002', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        self.edit_url = f'/tasks/{self.task.pk}/edit/'
        self.detail_url = f'/tasks/{self.task.pk}/'

    def _management_form(self, prefix, total, initial=0):
        return {
            f'{prefix}-TOTAL_FORMS': str(total),
            f'{prefix}-INITIAL_FORMS': str(initial),
            f'{prefix}-MIN_NUM_FORMS': '0',
            f'{prefix}-MAX_NUM_FORMS': '1000',
        }

    def _edit_payload(self, **overrides):
        payload = {
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE, 'description': '', 'is_warranty': '',
            **self._management_form('products', 0),
        }
        payload.update(overrides)
        return payload

    def test_manager_day_only_mode_leaves_scheduled_for_empty(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.edit_url, self._edit_payload(
            schedule_mode='day_only', scheduled_date_only='2026-10-01',
        ))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.scheduled_date.isoformat(), '2026-10-01')
        self.assertIsNone(self.task.scheduled_for)
        self.assertFalse(self.task.schedule_time_locked)

    def test_manager_full_mode_locks_the_schedule(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.edit_url, self._edit_payload(
            schedule_mode='full', scheduled_for='2026-10-01T09:00',
        ))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)
        self.assertEqual(self.task.scheduled_date.isoformat(), '2026-10-01')
        self.assertTrue(self.task.schedule_time_locked)

    def test_manager_can_schedule_without_touching_the_mode_radio(self):
        # schedule_mode and scheduled_for are two separate fields far
        # apart in the form; a manager who just fills in "Scheduled for"
        # without also clicking a radio button must still be able to save.
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.edit_url, self._edit_payload(scheduled_for='2026-10-01T09:00'))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)
        self.assertTrue(self.task.schedule_time_locked)

    def test_supervisor_can_still_schedule_their_own_task_freely(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.edit_url, self._edit_payload(scheduled_for='2026-10-01T09:00'))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)
        self.assertFalse(self.task.schedule_time_locked)

    def test_supervisor_can_set_time_for_a_day_only_pending_task(self):
        self.task.scheduled_date = date(2026, 10, 1)
        self.task.save(update_fields=['scheduled_date'])

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'set_schedule_time', 'scheduled_time': '14:30',
        })
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)
        self.assertFalse(self.task.schedule_time_locked)
        self.assertTrue(
            self.task.events.filter(event_type=TaskEvent.EventType.RESCHEDULED).exists(),
        )

    def test_manager_setting_time_for_a_day_only_pending_task_locks_it(self):
        self.task.scheduled_date = date(2026, 10, 1)
        self.task.save(update_fields=['scheduled_date'])

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'set_schedule_time', 'scheduled_time': '14:30',
        })
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.scheduled_for)
        self.assertTrue(self.task.schedule_time_locked)

    def test_supervisor_cannot_change_scheduled_for_directly_once_locked(self):
        self.task.scheduled_for = timezone.make_aware(datetime(2026, 10, 1, 9, 0))
        self.task.scheduled_date = date(2026, 10, 1)
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['scheduled_for', 'scheduled_date', 'schedule_time_locked'])
        original = self.task.scheduled_for

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.edit_url)
        self.assertNotContains(response, 'name="scheduled_for"')

        self.client.post(self.edit_url, self._edit_payload(scheduled_for='2026-11-11T11:00'))
        self.task.refresh_from_db()
        self.assertEqual(self.task.scheduled_for, original)

    def test_supervisor_can_request_a_change_when_locked(self):
        self.task.scheduled_for = timezone.make_aware(datetime(2026, 10, 1, 9, 0))
        self.task.scheduled_date = date(2026, 10, 1)
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['scheduled_for', 'scheduled_date', 'schedule_time_locked'])

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'request_schedule_change',
            'requested_scheduled_for': '2026-10-02T10:00',
            'reason': 'Customer asked to move it a day later.',
        })
        self.assertEqual(response.status_code, 302)

        request_obj = ScheduleChangeRequest.objects.get(task=self.task)
        self.assertEqual(request_obj.status, ScheduleChangeRequest.Status.PENDING)
        self.assertEqual(request_obj.requested_by, self._supervisor_technician())
        self.assertTrue(
            self.task.events.filter(
                event_type=TaskEvent.EventType.SCHEDULE_CHANGE_REQUESTED,
            ).exists(),
        )

    def test_cannot_request_a_change_when_not_locked(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'request_schedule_change', 'requested_scheduled_for': '2026-10-02T10:00',
        }, follow=True)
        self.assertFalse(ScheduleChangeRequest.objects.exists())
        self.assertContains(response, 'Please edit it directly')

    def test_cannot_file_a_second_pending_request(self):
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['schedule_time_locked'])
        ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self._supervisor_technician(),
            requested_scheduled_for=timezone.now(), created_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'request_schedule_change', 'requested_scheduled_for': '2026-10-02T10:00',
        }, follow=True)
        self.assertEqual(ScheduleChangeRequest.objects.count(), 1)
        self.assertContains(response, 'already a pending request')

    def _supervisor_technician(self):
        return Technician.objects.get(user=self.supervisor_user)

    def test_manager_can_approve_a_schedule_change(self):
        self.task.scheduled_for = timezone.make_aware(datetime(2026, 10, 1, 9, 0))
        self.task.scheduled_date = date(2026, 10, 1)
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['scheduled_for', 'scheduled_date', 'schedule_time_locked'])
        change_request = ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self._supervisor_technician(),
            requested_scheduled_for=timezone.make_aware(datetime(2026, 10, 2, 10, 0)),
            created_at=timezone.now(),
        )

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'approve_schedule_change', 'request_id': change_request.pk,
        })
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        change_request.refresh_from_db()
        self.assertEqual(self.task.scheduled_for, change_request.requested_scheduled_for)
        self.assertTrue(self.task.schedule_time_locked)
        self.assertEqual(change_request.status, ScheduleChangeRequest.Status.APPROVED)

    def test_manager_can_deny_a_schedule_change(self):
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['schedule_time_locked'])
        change_request = ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self._supervisor_technician(),
            requested_scheduled_for=timezone.now(), created_at=timezone.now(),
        )

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'deny_schedule_change', 'request_id': change_request.pk,
            'review_note': 'Technician already en route for the original time.',
        })
        self.assertEqual(response.status_code, 302)

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, ScheduleChangeRequest.Status.DENIED)

    def test_supervisor_cannot_approve_a_schedule_change(self):
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['schedule_time_locked'])
        change_request = ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self._supervisor_technician(),
            requested_scheduled_for=timezone.now(), created_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.detail_url, {
            'action': 'approve_schedule_change', 'request_id': change_request.pk,
        })
        self.assertEqual(response.status_code, 403)

    def test_manager_can_edit_a_locked_schedule_directly(self):
        self.task.scheduled_for = timezone.make_aware(datetime(2026, 10, 1, 9, 0))
        self.task.scheduled_date = date(2026, 10, 1)
        self.task.schedule_time_locked = True
        self.task.save(update_fields=['scheduled_for', 'scheduled_date', 'schedule_time_locked'])

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.edit_url, self._edit_payload(
            schedule_mode='full', scheduled_for='2026-11-05T13:00',
        ))
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(
            timezone.localtime(self.task.scheduled_for, ZoneInfo('Asia/Dubai')).isoformat()[:16],
            '2026-11-05T13:00',
        )


class TaskShippingNoticeTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        self.url = f'/tasks/{self.task.pk}/'

    def test_requires_a_tracking_number(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'notify_shipping'}, follow=True)

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'Please add a shipping tracking number')

    def test_requires_a_contact_email(self):
        self.task.shipping_tracking_number = 'TRACK-99'
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'notify_shipping'}, follow=True)

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'Please add a contact email')

    def test_sends_the_tracking_number_only(self):
        self.task.shipping_tracking_number = 'TRACK-99'
        self.task.pak_reference_number = 'PAK-SECRET'
        self.task.save()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'notify_shipping'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['manager@fitnessfirst.example'])
        self.assertIn('TRACK-99', mail.outbox[0].body)
        self.assertNotIn('PAK-SECRET', mail.outbox[0].body)


class TaskApprovalTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.COMPLETED,
        )
        WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/{self.task.pk}/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report'})
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report'})
        self.assertEqual(response.status_code, 403)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_the_technical_support_manager_approves_and_closes(self):
        # The final approval is the Technical Support Manager's (docs/access_rules.md): a Manager is refused.
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self.client.post(self.url, {'action': 'approve_report'}).status_code, 403)
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.SUPPORT_MANAGER)
        response = self.client.post(self.url, {'action': 'approve_report'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)

        event = self.task.events.get(event_type=TaskEvent.EventType.REPORT_APPROVED)
        self.assertEqual(event.actor, self.manager_user)

    def test_cannot_approve_a_task_with_no_report_awaiting_approval(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.SUPPORT_MANAGER)
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report'}, follow=True)

        self.assertContains(response, 'no report awaiting approval')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

    def test_admin_can_reopen_a_closed_task(self):
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.task.status = Task.Status.CLOSED
        self.task.save()

        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reopen'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)
        self.assertTrue(self.task.events.filter(event_type=TaskEvent.EventType.REOPENED).exists())

    def test_manager_cannot_reopen(self):
        self.task.status = Task.Status.CLOSED
        self.task.save()

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reopen'})
        self.assertEqual(response.status_code, 403)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)

    def test_cannot_reopen_a_task_that_is_not_closed(self):
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reopen'}, follow=True)

        self.assertContains(response, 'not closed')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_close_directly_bypasses_the_whole_chain_regardless_of_status(self):
        # admin/manager can always close directly, report or no report,
        # whatever status the task is currently sitting at.
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'close_directly', 'note': 'Customer cancelled.'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)


class TaskSupervisorApprovalTests(TaskTestCase):
    """A technician's own report needs their supervisor's sign-off before
    a manager ever sees it (pending_supervisor_review); a supervisor
    filing it themselves skips straight to completed, exactly as before
    this step existed.
    """

    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
            responsible_supervisor=self.supervisor_user.technician,
        )
        self.url = f'/tasks/{self.task.pk}/'
        self.report_url = f'/tasks/my/{self.task.pk}/report/'

    def _report_payload(self):
        return {
            'findings': 'Belt worn out', 'action_taken': 'Replaced belt', 'resolved': 'True',
            'labour_hours': '1.50', 'customer_name': 'Ali Manager',
            'existing-TOTAL_FORMS': '0', 'existing-INITIAL_FORMS': '0',
            'existing-MIN_NUM_FORMS': '0', 'existing-MAX_NUM_FORMS': '1000',
            'new-TOTAL_FORMS': '0', 'new-INITIAL_FORMS': '0',
            'new-MIN_NUM_FORMS': '0', 'new-MAX_NUM_FORMS': '1000',
            'parts-TOTAL_FORMS': '0', 'parts-INITIAL_FORMS': '0',
            'parts-MIN_NUM_FORMS': '0', 'parts-MAX_NUM_FORMS': '1000',
        }

    def _assign_lead(self, technician):
        TaskAssignment.objects.create(
            task=self.task, technician=technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

    def test_technicians_report_goes_to_pending_supervisor_review(self):
        self._assign_lead(self.technician)
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.report_url, self._report_payload())
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.PENDING_SUPERVISOR_REVIEW)

    def test_supervisors_own_report_skips_straight_to_completed(self):
        self._assign_lead(self.supervisor_user.technician)
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.report_url, self._report_payload())
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_responsible_supervisor_approves_a_technicians_report(self):
        self._assign_lead(self.technician)
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save(update_fields=['status'])
        WorkReport.objects.create(
            task=self.task, findings='Belt worn out', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report_supervisor'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)
        event = self.task.events.get(event_type=TaskEvent.EventType.SUPERVISOR_APPROVED)
        self.assertEqual(event.actor, self.supervisor_user)

    def test_a_different_supervisor_cannot_approve(self):
        other_user = User.objects.create_user('other_sup', password='pass12345')
        Technician.objects.create(
            user=other_user, country=self.country, full_name='Nadia Other',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save(update_fields=['status'])

        self.client.login(username='other_sup', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report_supervisor'})
        self.assertEqual(response.status_code, 403)

    def test_manager_can_also_approve_as_supervisor(self):
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save(update_fields=['status'])

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report_supervisor'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_manager_cannot_approve_report_while_pending_supervisor_review(self):
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save(update_fields=['status'])
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.SUPPORT_MANAGER)
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report'}, follow=True)

        self.assertContains(response, 'no report awaiting approval')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.PENDING_SUPERVISOR_REVIEW)


class TaskMessageTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.warehouse_user = User.objects.create_user('warehouse1', password='pass12345')
        Technician.objects.create(
            user=self.warehouse_user, country=self.country, full_name='Wael Warehouse',
            language='en', role=Technician.Role.WAREHOUSE_MANAGER, employment_type='staff',
        )
        self.helper_user = User.objects.create_user('helper1', password='pass12345')
        self.helper = Technician.objects.create(
            user=self.helper_user, country=self.country, full_name='Hani Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.uninvolved_user = User.objects.create_user('uninvolved_tech', password='pass12345')
        Technician.objects.create(
            user=self.uninvolved_user, country=self.country, full_name='Nora Nobody',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
            responsible_supervisor=self.supervisor_user.technician, pak_reference_number='PAK-42',
            # Stock-related work, which the Warehouse Manager sees (docs/access_rules.md).
            task_type=TaskType.objects.create(code='inst-msg', name='Installation', category='installation'),
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        self.url = f'/tasks/{self.task.pk}/'

    def test_warehouse_manager_gets_403_on_customers(self):
        self.client.login(username='warehouse1', password='pass12345')
        response = self.client.get('/customers/')
        self.assertEqual(response.status_code, 403)

    def test_warehouse_manager_gets_403_on_tickets(self):
        self.client.login(username='warehouse1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 403)

    def test_warehouse_manager_can_search_tasks_by_pak(self):
        self.client.login(username='warehouse1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'pak_reference': 'PAK-42'})
        self.assertEqual(response.status_code, 200)
        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [self.task.task_number])

    def test_warehouse_manager_sees_the_tracking_number(self):
        self.task.shipping_tracking_number = 'TRACK-99'
        self.task.save()
        self.client.login(username='warehouse1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'TRACK-99')

    def test_warehouse_manager_posts_a_message_and_notifies_the_team(self):
        self.client.login(username='warehouse1', password='pass12345')
        response = self.client.post(self.url, {'action': 'add_message', 'message': 'Part arrived today.'})
        self.assertEqual(response.status_code, 302)

        task_message = TaskMessage.objects.get(task=self.task)
        self.assertEqual(task_message.message, 'Part arrived today.')
        self.assertEqual(task_message.sent_by, self.warehouse_user)

        notified = set(
            TaskMessageRecipient.objects.filter(message=task_message).values_list('technician__full_name', flat=True),
        )
        # Lead + helper + responsible supervisor + creator (also the
        # supervisor here) + every manager-tier person in the country —
        # never the warehouse manager who just sent it, and never the
        # uninvolved technician.
        self.assertEqual(notified, {'Tarek Tech', 'Hani Helper', 'Sara Super', 'Maya Manager'})

    def test_sender_is_not_their_own_recipient(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'add_message', 'message': 'Heads up.'})
        task_message = TaskMessage.objects.get(task=self.task)
        self.assertFalse(
            TaskMessageRecipient.objects.filter(message=task_message, technician=self.supervisor_user.technician).exists(),
        )

    def test_uninvolved_technician_gets_no_notification(self):
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post(self.url, {'action': 'add_message', 'message': 'Part arrived today.'})
        task_message = TaskMessage.objects.get(task=self.task)
        uninvolved_technician = Technician.objects.get(full_name='Nora Nobody')
        self.assertFalse(
            TaskMessageRecipient.objects.filter(message=task_message, technician=uninvolved_technician).exists(),
        )

    def test_technician_sees_the_message_on_my_task_detail(self):
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post(self.url, {'action': 'add_message', 'message': 'Part arrived today.'})

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(f'/tasks/my/{self.task.pk}/')
        self.assertContains(response, 'Part arrived today.')

    def test_bell_shows_the_unseen_message_then_clears_on_open(self):
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post(self.url, {'action': 'add_message', 'message': 'Part arrived today.'})

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertEqual(response.context['unseen_notification_count'], 1)

        self.client.get(f'/tasks/my/{self.task.pk}/')
        response = self.client.get('/tasks/my-week/')
        self.assertNotIn('unseen_notification_count', response.context)

    def test_technician_can_reply_and_notifies_the_warehouse_manager(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(
            f'/tasks/my/{self.task.pk}/', {'action': 'add_message', 'message': 'Got it, thanks.'},
        )
        self.assertEqual(response.status_code, 302)
        task_message = TaskMessage.objects.get(task=self.task)
        self.assertEqual(task_message.sent_by, self.tech_user)
        # The warehouse manager isn't on the job, isn't the responsible
        # supervisor, and isn't manager-tier — a reply from the lead
        # doesn't re-notify them, same as any other non-participant.
        self.assertFalse(
            TaskMessageRecipient.objects.filter(
                message=task_message, technician__user=self.warehouse_user,
            ).exists(),
        )


class TaskListTests(TaskTestCase):
    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_filter_by_customer(self):
        other_customer = Customer.objects.create(country=self.country, name='Gold Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='JBR Branch', address='JBR')
        matching = self._make_task('AE-0001')
        self._make_task('AE-0002', site=other_site)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'customer': self.customer.pk})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [matching.task_number])

    def test_filter_by_task_number(self):
        matching = self._make_task('AE-0001')
        self._make_task('AE-0002')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'task_id': '0001'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [matching.task_number])

    def test_filter_by_site(self):
        other_site = Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR')
        matching = self._make_task('AE-0001')
        self._make_task('AE-0002', site=other_site)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'site': 'Marina'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [matching.task_number])

    def test_filter_by_site_also_matches_the_address_not_just_the_name(self):
        # self.site.address is 'Dubai Marina' (TaskTestCase.setUp) — a
        # city search shouldn't require it to be part of the site's own
        # name, just somewhere in the address.
        other_site = Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR, Abu Dhabi')
        matching = self._make_task('AE-0001')
        self._make_task('AE-0002', site=other_site)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'site': 'Dubai'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [matching.task_number])

    def test_filter_by_pak_reference(self):
        matching = self._make_task('AE-0001', pak_reference_number='PAK-99001')
        self._make_task('AE-0002', pak_reference_number='PAK-11111')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'pak_reference': '99001'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [matching.task_number])

    def test_filter_by_technician_matches_lead_or_helper(self):
        lead_task = self._make_task('AE-0001')
        TaskAssignment.objects.create(
            task=lead_task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        other_task = self._make_task('AE-0002')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'technician': self.technician.pk})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [lead_task.task_number])
        self.assertNotIn(other_task.task_number, tasks)

    def test_filter_by_scheduled_date_range(self):
        in_range = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 10, 9, 0))
        self._make_task('AE-0002', scheduled_for=dubai_time(2026, 9, 20, 9, 0))
        self._make_task('AE-0003', scheduled_for=None)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {
            'status': 'all', 'scheduled_from': '2026-09-08', 'scheduled_to': '2026-09-12',
        })

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [in_range.task_number])

    def test_search_by_machine_serial_number(self):
        matching = self._make_task('AE-0001')
        matching_asset = Asset.objects.create(site=self.site, brand=self.brand, serial_no='SN-99001')
        TaskAsset.objects.create(task=matching, asset=matching_asset, outcome=TaskAsset.Outcome.REPAIRED)

        other = self._make_task('AE-0002')
        other_asset = Asset.objects.create(site=self.site, brand=self.brand, serial_no='SN-11111')
        TaskAsset.objects.create(task=other, asset=other_asset, outcome=TaskAsset.Outcome.REPAIRED)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'serial_number': '99001'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [matching.task_number])

    def test_search_by_serial_number_does_not_duplicate_rows(self):
        task = self._make_task('AE-0001')
        for serial in ('SN-A', 'SN-B'):
            asset = Asset.objects.create(site=self.site, brand=self.brand, serial_no=serial)
            TaskAsset.objects.create(task=task, asset=asset, outcome=TaskAsset.Outcome.REPAIRED)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'serial_number': 'SN-'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [task.task_number])

    def test_filters_carry_across_status_tabs(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'customer': self.customer.pk})

        self.assertContains(response, f'customer={self.customer.pk}')

    def test_other_country_task_excluded(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        self._make_task('EG-0001', site=other_site, status=Task.Status.NEW)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all'})

        tasks = [task.task_number for task in response.context['page_obj']]
        self.assertEqual(tasks, [])

    def test_customer_and_technician_dropdowns_are_country_scoped(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/')

        self.assertNotIn('Cairo Gym', [c.name for c in response.context['customers']])
        self.assertNotIn('Nour Cairo', [t.full_name for t in response.context['technicians']])

    def _make_other_country_task(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        return self._make_task('EG-0001', site=other_site)

    def _login_manager(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')

    def test_a_manager_lists_their_country_and_a_project_manager_every_country(self):
        self._make_task('AE-0001')
        self._make_other_country_task()
        self._login_manager()
        # A Manager is one per country (docs/access_rules.md): ?country=all changes nothing.
        response = self.client.get('/tasks/', {'status': 'all', 'country': 'all'})
        self.assertEqual([t.task_number for t in response.context['page_obj']], ['AE-0001'])

        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.PROJECT_MANAGER)
        response = self.client.get('/tasks/', {'status': 'all', 'country': 'all'})
        self.assertEqual(
            sorted(t.task_number for t in response.context['page_obj']), ['AE-0001', 'EG-0001'],
        )
        self.assertContains(response, 'Egypt')

    def test_supervisor_cannot_list_every_country(self):
        self._make_other_country_task()
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'country': 'all'})
        self.assertNotContains(response, 'EG-0001')
        self.assertNotContains(response, 'All countries')


class TaskCreateTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/new/')
        self.assertEqual(response.status_code, 403)

    def test_dropdowns_exclude_other_country_data(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/new/')

        form = response.context['form']
        self.assertNotIn(other_site, form.fields['site'].queryset)
        # Adding a new site is admin-only (see below) — a supervisor's
        # form doesn't carry that field at all.
        self.assertNotIn('new_site_customer', form.fields)

    def test_supervisor_has_no_new_site_fields(self):
        # Creating a site is admin-only, same rule as
        # customers.views.customer_detail's "Add a site" form — a
        # supervisor gets the plain site dropdown only.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/new/')
        for field_name in [
            'new_site_customer', 'new_site_name', 'new_site_address',
            'new_site_contact_name', 'new_site_contact_phone', 'new_site_access_notes',
        ]:
            self.assertNotIn(field_name, response.context['form'].fields)

    def test_supervisor_cannot_create_a_new_site_this_way(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            new_site_customer=self.customer.pk, new_site_name='Downtown Branch',
            new_site_address='Downtown Dubai',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertFalse(Site.objects.filter(name='Downtown Branch').exists())
        self.assertTrue(response.context['form'].errors.get('site'))

    def test_admin_dropdown_excludes_other_country_customers(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/new/')
        self.assertNotIn(other_customer, response.context['form'].fields['new_site_customer'].queryset)

    def test_responsible_supervisor_dropdown_excludes_technicians_and_other_countries(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_sup', password='pass12345')
        other_supervisor = Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/new/')

        queryset = response.context['form'].fields['responsible_supervisor'].queryset
        supervisor = Technician.objects.get(user=self.supervisor_user)
        self.assertIn(supervisor, queryset)
        self.assertNotIn(self.technician, queryset)
        self.assertNotIn(other_supervisor, queryset)

    def test_creating_a_task_with_a_responsible_supervisor(self):
        supervisor = Technician.objects.get(user=self.supervisor_user)
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
            'responsible_supervisor': supervisor.pk,
        })
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.responsible_supervisor, supervisor)

    def test_the_lead_can_be_chosen_when_creating_a_task(self):
        self.client.login(username='supervisor1', password='pass12345')
        with patch('tasks.views.push_assigned') as pushed:
            response = self.client.post('/tasks/new/', self._base_new_task_payload(
                site=self.site.pk, lead=self.technician.pk,
            ))
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        lead = task.assignments.get(is_active=True)
        self.assertEqual((lead.technician, lead.role, task.status), (self.technician, 'lead', Task.Status.ASSIGNED))
        self.assertTrue(task.events.filter(event_type=TaskEvent.EventType.ASSIGNED).exists())
        pushed.assert_called_once()

    def test_no_lead_field_without_permission_to_assign(self):
        RolePermission.objects.filter(role='supervisor', permission='assign_tasks').update(allowed=False)
        self.client.login(username='supervisor1', password='pass12345')
        self.assertNotIn('lead', self.client.get('/tasks/new/').context['form'].fields)

    def test_creating_a_task_notifies_and_emails_managers_and_admins(self):
        manager_user = User.objects.create_user('manager1', email='dana@example.com', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
        })
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        notification = task.notifications.get()
        self.assertIsNone(notification.seen_at)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(task.task_number, mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [manager_user.email])

    def test_team_schedule_shows_who_is_already_booked(self):
        busy_task = Task.objects.create(
            task_number='AE-0002', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
            scheduled_for=timezone.now() + timedelta(days=1),
        )
        TaskAssignment.objects.create(
            task=busy_task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/new/')

        team = {p.pk: p for p in response.context['team_schedule']}
        self.assertEqual(team[self.technician.pk].next_scheduled_task, busy_task)
        supervisor = Technician.objects.get(user=self.supervisor_user)
        self.assertIsNone(team[supervisor.pk].next_scheduled_task)

    def test_manager_creating_a_task_with_a_schedule_locks_it(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'site': self.site.pk, 'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'scheduled_for': '2026-10-01T09:00',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Task.objects.get().schedule_time_locked)

    def test_supervisor_creating_a_task_with_a_schedule_leaves_it_open(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'site': self.site.pk, 'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'scheduled_for': '2026-10-01T09:00',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Task.objects.get().schedule_time_locked)

    def test_cannot_create_a_task_for_another_country_s_site(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'site': other_site.pk,
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('site'))

    def test_minimal_task_is_created_with_only_site_required(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
        })
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.site, self.site)
        self.assertEqual(task.status, Task.Status.NEW)
        self.assertEqual(task.created_by, self.supervisor_user)
        self.assertTrue(task.task_number.startswith('AE-'))
        self.assertIsNone(task.task_type)
        self.assertIsNone(task.brand)

        event = task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.CREATED)
        self.assertEqual(event.actor, self.supervisor_user)

    def test_reported_at_is_stored_in_utc_from_the_technicians_local_time(self):
        # The supervisor's country (UAE) is UTC+4, with no DST.
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post('/tasks/new/', {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
        })
        task = Task.objects.get()
        self.assertEqual(task.reported_at.isoformat(), '2026-09-06T06:00:00+00:00')

    def test_missing_site_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', {
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('site'))

    def test_task_numbers_increment_per_country(self):
        self.client.login(username='supervisor1', password='pass12345')
        for _ in range(2):
            self.client.post('/tasks/new/', {
                'site': self.site.pk,
                'priority': Task.Priority.NORMAL,
                'source': Task.Source.PHONE,
                'billing_type': Task.BillingType.CHARGEABLE,
                'reported_at': '2026-09-06T10:00',
                'is_warranty': '',
            })
        numbers = set(Task.objects.values_list('task_number', flat=True))
        self.assertEqual(numbers, {'AE-0001', 'AE-0002'})

    def test_task_number_uses_country_task_prefix_override(self):
        self.country.task_prefix = 'UAE'
        self.country.save()

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post('/tasks/new/', {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL,
            'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE,
            'reported_at': '2026-09-06T10:00',
            'is_warranty': '',
        })
        self.assertEqual(Task.objects.get().task_number, 'UAE-0001')

    def _base_new_task_payload(self, **overrides):
        payload = {
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00', 'is_warranty': '',
        }
        payload.update(overrides)
        return payload

    def test_new_site_is_created_under_the_chosen_customer(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            new_site_customer=self.customer.pk, new_site_name='Downtown Branch',
            new_site_address='Downtown Dubai',
        ))
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.site.name, 'Downtown Branch')
        self.assertEqual(task.site.customer, self.customer)
        self.assertEqual(task.site.address, 'Downtown Dubai')

    def test_duplicate_new_site_name_for_same_customer_is_rejected(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            new_site_customer=self.customer.pk, new_site_name=self.site.name,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_site_name'))

    def test_site_and_new_site_together_is_rejected(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_site_customer=self.customer.pk, new_site_name='Downtown Branch',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)

    def test_new_brand_is_created_and_used(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_brand_name='Life Fitness', new_brand_portal_url='https://portal.example.com',
        ))
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.brand.name, 'Life Fitness')
        self.assertEqual(task.brand.portal_url, 'https://portal.example.com')

    def test_duplicate_new_brand_name_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_brand_name=self.brand.name,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_brand_name'))

    def test_new_task_type_is_created_and_used(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_task_type_code='deep_clean', new_task_type_name='Deep clean',
            new_task_type_name_ar='تنظيف عميق', new_task_type_category='maintenance',
        ))
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.task_type.code, 'deep_clean')
        self.assertEqual(task.task_type.name_ar, 'تنظيف عميق')

    def test_incomplete_new_task_type_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_task_type_name='Deep clean',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)

    def test_oversized_new_site_name_is_rejected_cleanly(self):
        # Site.name is max_length=150 — this must fail as a normal form
        # error, not crash with a database "value too long" error.
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            new_site_customer=self.customer.pk, new_site_name='x' * 151,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_site_name'))

    def test_oversized_new_brand_name_is_rejected_cleanly(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_brand_name='x' * 101,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_brand_name'))

    def test_oversized_new_task_type_code_is_rejected_cleanly(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_task_type_code='x' * 51, new_task_type_name='Deep clean',
            new_task_type_name_ar='تنظيف عميق', new_task_type_category='maintenance',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_task_type_code'))

    def test_min_level_above_the_skill_scale_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, min_level='5',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('min_level'))

    def test_estimated_hours_over_48_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, estimated_hours='72',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('estimated_hours'))

    def test_scheduled_task_with_estimated_hours_has_an_estimated_finish(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, scheduled_for='2026-09-10T09:00', estimated_hours='2.5',
        ))
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.estimated_finish, task.scheduled_for + timedelta(hours=2.5))


class TicketFormTests(TaskTestCase):
    """The old public no-login ticket form is retired — every path now
    redirects to the customer portal login, whether anonymous or already
    signed in as staff/a customer, and whether GET or POST.
    """

    def test_anonymous_get_redirects_to_portal_login(self):
        response = self.client.get('/tasks/tickets/new/')
        self.assertRedirects(response, '/customers/portal/login/')

    def test_anonymous_post_redirects_without_creating_a_ticket(self):
        response = self.client.post('/tasks/tickets/new/', {'company_name': 'Fitness First'})
        self.assertRedirects(response, '/customers/portal/login/')
        self.assertEqual(CustomerTicket.objects.count(), 0)

    def test_logged_in_staff_also_redirects(self):
        # portal_login itself then bounces an already-authenticated staff
        # user onward to 'home' — a separate, pre-existing concern; this
        # only checks that ticket_form's own redirect target is right.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/tickets/new/')
        self.assertRedirects(response, '/customers/portal/login/', fetch_redirect_response=False)


class TicketListTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=self.support_user, country=self.country, full_name='Dana Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        # Tickets are technical-support-manager/admin-only now — a
        # supervisor gets no ticket access at all, not even read-only triage.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 403)

    def test_plain_manager_gets_403(self):
        # Plain Manager lost manage_tickets too — only the technical
        # support manager (and admin) has it now.
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 403)

    def test_support_manager_sees_new_tickets_by_default(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fitness First')

    def test_other_country_ticket_not_shown(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        CustomerTicket.objects.create(
            country=other_country, ticket_number='EG-T0001', company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )

        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/', {'status': 'all'})
        self.assertNotContains(response, 'Cairo Gym')

    def test_all_countries_shows_other_country_tickets(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        CustomerTicket.objects.create(
            country=other_country, ticket_number='EG-T0001', company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )

        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/', {'status': 'all', 'country': 'all'})
        self.assertContains(response, 'Cairo Gym')
        self.assertContains(response, 'Fitness First')
        self.assertContains(response, 'Egypt')

    def test_search_by_pak_reference_number(self):
        self.ticket.pak_reference_number = 'PAK-99001'
        self.ticket.save(update_fields=['pak_reference_number'])
        other = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0002', company_name='One Fit Gym', site_description='JBR Branch',
            contact_name='Sara', contact_phone='0509999999', pak_reference_number='PAK-11111',
            description='Bike display broken.', submitted_at=timezone.now(),
        )

        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/', {'status': 'all', 'pak': '99001'})
        self.assertContains(response, 'Fitness First')
        self.assertNotContains(response, 'One Fit Gym')

    def test_search_by_site_also_matches_the_address_not_just_the_description(self):
        self.ticket.site_address = 'Dubai Marina'
        self.ticket.save(update_fields=['site_address'])
        other = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0002', company_name='One Fit Gym', site_description='JBR Branch',
            site_address='JBR, Abu Dhabi', contact_name='Sara', contact_phone='0509999999',
            description='Bike display broken.', submitted_at=timezone.now(),
        )

        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/', {'status': 'all', 'site': 'Dubai'})
        self.assertContains(response, 'Fitness First')
        self.assertNotContains(response, 'One Fit Gym')


class TicketReviewTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=self.support_user, country=self.country, full_name='Dana Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/tickets/{self.ticket.pk}/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        # Tickets are technical-support-manager/admin-only now — a supervisor gets no
        # access at all, not even read-only (unless individually
        # assigned, and a supervisor can no longer be assigned either).
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_other_country_ticket_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_ticket = CustomerTicket.objects.create(
            country=other_country, ticket_number='EG-T0001', company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/tickets/{other_ticket.pk}/')
        self.assertEqual(response.status_code, 404)

    def _make_other_country_ticket(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        return CustomerTicket.objects.create(
            country=other_country, ticket_number='EG-T0001', company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )

    def _make_staff(self, username, role):
        user = User.objects.create_user(username, password='pass12345')
        Technician.objects.create(
            user=user, country=self.country, full_name=username.title(),
            language='en', role=role, employment_type='staff',
        )
        return user

    def test_support_manager_opens_and_dismisses_other_country_ticket(self):
        other_ticket = self._make_other_country_ticket()
        self.client.login(username='support1', password='pass12345')

        response = self.client.get(f'/tasks/tickets/{other_ticket.pk}/')
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            f'/tasks/tickets/{other_ticket.pk}/', {'action': 'dismiss', 'dismissal_reason': 'Duplicate report.'},
        )
        self.assertEqual(response.status_code, 302)
        other_ticket.refresh_from_db()
        self.assertEqual(other_ticket.status, CustomerTicket.Status.DISMISSED)

    def test_operations_manager_has_the_whole_ticket_desk(self):
        other_ticket = self._make_other_country_ticket()
        self._make_staff('ops1', Technician.Role.OPERATIONS_MANAGER)
        self.client.login(username='ops1', password='pass12345')

        self.assertEqual(self.client.get('/tasks/tickets/').status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(f'/tasks/tickets/{other_ticket.pk}/').status_code, 200)

    def test_plain_manager_cannot_open_tickets(self):
        other_ticket = self._make_other_country_ticket()
        self._make_staff('manager1', Technician.Role.MANAGER)
        self.client.login(username='manager1', password='pass12345')

        self.assertEqual(self.client.get(self.url).status_code, 403)
        # Another country's request: not even found for a Manager, who has one country.
        self.assertIn(self.client.get(f'/tasks/tickets/{other_ticket.pk}/').status_code, (403, 404))

    def test_support_manager_converts_other_country_ticket_without_switching(self):
        other_ticket = self._make_other_country_ticket()
        other_customer = Customer.objects.create(country=other_ticket.country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        # Converting needs only manage_tickets, whatever create_tasks says.
        RolePermission.objects.filter(
            role=Technician.Role.SUPPORT_MANAGER, permission=RolePermission.Permission.CREATE_TASKS,
        ).update(allowed=False)
        self.client.login(username='support1', password='pass12345')

        create_url = f'/tasks/new/?ticket={other_ticket.pk}'
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)
        sites = list(response.context['form'].fields['site'].queryset)
        self.assertIn(other_site, sites)
        self.assertNotIn(self.site, sites)

        response = self.client.post(create_url, {
            'site': other_site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Something broke.',
        })
        self.assertEqual(response.status_code, 302)
        other_ticket.refresh_from_db()
        self.assertEqual(other_ticket.status, CustomerTicket.Status.CONVERTED)
        self.assertEqual(other_ticket.task.site, other_site)

    def test_supervisor_still_cannot_convert_other_country_ticket(self):
        other_ticket = self._make_other_country_ticket()
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/new/?ticket={other_ticket.pk}')
        self.assertEqual(response.status_code, 404)

    def test_dismiss_requires_a_reason(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'dismiss', 'dismissal_reason': ''})
        self.assertEqual(response.status_code, 200)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)

    def test_dismiss_sets_status_and_reason(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'dismiss', 'dismissal_reason': 'Duplicate report.'})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.DISMISSED)
        self.assertEqual(self.ticket.dismissal_reason, 'Duplicate report.')
        self.assertEqual(self.ticket.reviewed_by, self.support_user)

    def test_a_request_is_closed_only_by_answering_it(self):
        # One way to close a request without a visit: "Answer without a visit" (its reason and
        # each machine's decision recorded). The old "Close request" is gone, and posting it does nothing.
        self.client.login(username='support1', password='pass12345')
        self.assertNotContains(self.client.get(self.url), 'name="action" value="close"')
        self.client.post(self.url, {'action': 'close', 'close_reason': 'Advice given by phone.'})
        self.ticket.refresh_from_db()
        self.assertNotEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_admin_can_reopen_a_closed_ticket(self):
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.ticket.status = CustomerTicket.Status.CLOSED
        self.ticket.close_reason = 'Handled by phone.'
        self.ticket.save(update_fields=['status', 'close_reason'])

        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reopen'})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)

    def test_support_manager_cannot_reopen(self):
        # Support manager can close a ticket, but reopening one is a step
        # further, admin-only — same fixed-floor pattern as require_admin.
        self.ticket.status = CustomerTicket.Status.CLOSED
        self.ticket.close_reason = 'Handled by phone.'
        self.ticket.save(update_fields=['status', 'close_reason'])

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reopen'})
        self.assertEqual(response.status_code, 200)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_reopen_a_ticket_that_is_not_closed_errors(self):
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reopen'}, follow=True)
        self.assertContains(response, 'not closed')

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)

    def test_editing_details_includes_customer_code_and_shipping_address(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'edit_details', 'company_name': self.ticket.company_name,
            'customer_code': 'ACC-42', 'site_description': self.ticket.site_description,
            'site_address': 'Dubai Marina', 'contact_name': self.ticket.contact_name,
            'contact_phone': self.ticket.contact_phone, 'contact_email': '',
            'shipping_address': 'Warehouse 3, Al Quoz', 'serial_numbers': 'SN-1',
            'description': self.ticket.description, 'notes': '',
        })
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.customer_code, 'ACC-42')
        self.assertEqual(self.ticket.shipping_address, 'Warehouse 3, Al Quoz')

    def test_updating_logistics(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'update_logistics', 'pak_reference_number': 'PAK-42',
            'shipping_tracking_number': 'TRACK-99',
        })
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.pak_reference_number, 'PAK-42')
        self.assertEqual(self.ticket.shipping_tracking_number, 'TRACK-99')

    def test_notify_shipping_requires_a_tracking_number(self):
        self.ticket.contact_email = 'ali@fitnessfirst.example'
        self.ticket.save()

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'notify_shipping'}, follow=True)

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'Please add a shipping tracking number')

    def test_notify_shipping_sends_the_tracking_number_only(self):
        self.ticket.shipping_tracking_number = 'TRACK-99'
        self.ticket.pak_reference_number = 'PAK-SECRET'
        self.ticket.contact_email = 'ali@fitnessfirst.example'
        self.ticket.save()

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'notify_shipping'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ali@fitnessfirst.example'])
        self.assertIn('TRACK-99', mail.outbox[0].body)
        self.assertNotIn('PAK-SECRET', mail.outbox[0].body)

    def test_converting_to_task_links_the_ticket(self):
        self.client.login(username='supervisor1', password='pass12345')

        create_url = f'/tasks/new/?ticket={self.ticket.pk}'
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fitness First')

        # The ticket's site_description happens to match an existing site
        # (the fixture's self.site) — the realistic case where the
        # supervisor recognizes it and picks the existing record rather
        # than creating a duplicate.
        response = self.client.post(create_url, {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Treadmill belt squeaking.',
        })
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CONVERTED)
        self.assertIsNotNone(self.ticket.task)
        self.assertEqual(self.ticket.task.source, Task.Source.PORTAL)
        self.assertEqual(self.ticket.reviewed_by, self.supervisor_user)

    def test_registered_site_locks_the_task_and_ignores_tampering(self):
        other_site = Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR')
        self.ticket.site = self.site
        self.ticket.save(update_fields=['site'])

        self.client.login(username='supervisor1', password='pass12345')
        create_url = f'/tasks/new/?ticket={self.ticket.pk}'
        response = self.client.get(create_url)
        self.assertContains(response, 'Only an administrator can change it')

        # A supervisor's edit form has the field disabled, so even a
        # crafted POST naming a different site is ignored server-side —
        # not just hidden in the UI.
        response = self.client.post(create_url, {
            'site': other_site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Treadmill belt squeaking.',
        })
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.task.site, self.site)

    def test_admin_can_override_the_registered_site(self):
        other_site = Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR')
        self.ticket.site = self.site
        self.ticket.save(update_fields=['site'])
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

        self.client.login(username='admin1', password='pass12345')
        create_url = f'/tasks/new/?ticket={self.ticket.pk}'
        response = self.client.get(create_url)
        self.assertContains(response, 'As an administrator, you may still change it')

        response = self.client.post(create_url, {
            'site': other_site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Treadmill belt squeaking.',
        })
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.task.site, other_site)

    def test_converting_carries_over_pak_and_tracking(self):
        self.ticket.pak_reference_number = 'PAK-42'
        self.ticket.shipping_tracking_number = 'TRACK-99'
        self.ticket.save()

        self.client.login(username='supervisor1', password='pass12345')
        create_url = f'/tasks/new/?ticket={self.ticket.pk}'
        self.client.post(create_url, {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Treadmill belt squeaking.',
        })

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.task.pak_reference_number, 'PAK-42')
        self.assertEqual(self.ticket.task.shipping_tracking_number, 'TRACK-99')

    def test_converting_carries_over_reported_serial_numbers(self):
        self.ticket.serial_numbers = 'SN-1234\nSN-5678'
        self.ticket.save()

        self.client.login(username='supervisor1', password='pass12345')
        create_url = f'/tasks/new/?ticket={self.ticket.pk}'
        self.client.post(create_url, {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Treadmill belt squeaking.',
        })

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.task.reported_serial_numbers, 'SN-1234\nSN-5678')

    def test_cannot_reconvert_an_already_converted_ticket(self):
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PORTAL, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        self.ticket.status = CustomerTicket.Status.CONVERTED
        self.ticket.task = task
        self.ticket.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/new/?ticket={self.ticket.pk}')
        self.assertEqual(response.status_code, 404)

    def test_support_manager_assigns_a_colleague(self):
        other_user = User.objects.create_user('support2', password='pass12345')
        other_manager = Technician.objects.create(
            user=other_user, country=self.country, full_name='Layla Lead',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'assign', 'assigned_to': other_manager.pk})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, other_manager)
        self.assertIsNotNone(self.ticket.assigned_at)

    def test_supervisors_are_not_offered_as_assignees_either(self):
        # Tickets are technical-support-manager/admin-only now — a supervisor can't be
        # handed one even individually, same as a technician never could.
        other_user = User.objects.create_user('supervisor2', password='pass12345')
        other_supervisor = Technician.objects.create(
            user=other_user, country=self.country, full_name='Layla Lead',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'assign', 'assigned_to': other_supervisor.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['assign_form'].errors.get('assigned_to'))

        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assigned_to)

    def test_technicians_are_not_offered_as_assignees(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'assign', 'assigned_to': self.technician.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['assign_form'].errors.get('assigned_to'))

        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assigned_to)

    def test_assignee_can_view_but_not_dismiss_or_convert(self):
        # The is_assignee read-only fallback still matters for a support manager
        # specifically (an admin could turn manage_tickets off for the Technical
        # Support Manager role but still delegate one ticket to one) — a
        # supervisor can no longer reach this at all, since one can't be
        # assigned a ticket in the first place.
        other_user = User.objects.create_user('support2', password='pass12345')
        other_manager = Technician.objects.create(
            user=other_user, country=self.country, full_name='Layla Lead',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        RolePermission.objects.filter(
            role=Technician.Role.SUPPORT_MANAGER, permission=RolePermission.Permission.MANAGE_TICKETS,
        ).update(allowed=False)
        self.ticket.assigned_to = other_manager
        self.ticket.save()

        self.client.login(username='support2', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(self.url, {'action': 'dismiss', 'dismissal_reason': 'Spam.'})
        self.assertEqual(response.status_code, 200)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)

    def test_unrelated_technician_still_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_other_country_supervisor_gets_404(self):
        # Country-scoped in the fetch itself, same as every other
        # cross-country lookup in this app — not a 403, a 404.
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_sup', password='pass12345')
        Technician.objects.create(
            user=other_user, country=other_country, full_name='Sara Cairo',
            language='ar', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )

        self.client.login(username='egypt_sup', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_support_manager_can_add_and_see_internal_notes(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'add_internal_note', 'message': 'Waiting on the part.'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TicketInternalNote.objects.filter(ticket=self.ticket).count(), 1)

        note = TicketInternalNote.objects.get(ticket=self.ticket)
        self.assertEqual(note.message, 'Waiting on the part.')
        self.assertEqual(note.author, self.support_user)

        response = self.client.get(self.url)
        self.assertContains(response, 'Internal notes')
        self.assertContains(response, 'Waiting on the part.')

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_support_manager_can_attach_files_to_an_internal_note(self):
        quote = SimpleUploadedFile('vendor_quote.pdf', b'%PDF-1.4 not a real pdf', content_type='application/pdf')
        photo = SimpleUploadedFile('part.jpg', REAL_JPEG, content_type='image/jpeg')

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'add_internal_note', 'message': 'Vendor quote attached.', 'attachments': [quote, photo],
        })
        self.assertEqual(response.status_code, 302)

        note = TicketInternalNote.objects.get(ticket=self.ticket)
        self.assertEqual(sorted(f.name.rsplit('/', 1)[-1] for f in note.all_files), ['part.jpg', 'vendor_quote.pdf'])

        # Each shows by its own name, with the paperclip.
        response = self.client.get(self.url)
        self.assertContains(response, '>vendor_quote.pdf</a>')
        self.assertContains(response, '>part.jpg</a>')

    def test_admin_can_add_internal_note(self):
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, {'action': 'add_internal_note', 'message': 'Escalated to the vendor.'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TicketInternalNote.objects.filter(ticket=self.ticket).count(), 1)

    def test_supervisor_cannot_add_internal_note(self):
        # Not just a manager-tier UI gate — a supervisor has no
        # manage_tickets at all now, so this is blocked before the
        # action dispatch ever runs, same as any other ticket action.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'add_internal_note', 'message': 'Sneaking this in.'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TicketInternalNote.objects.filter(ticket=self.ticket).count(), 0)

    def test_plain_reply_is_unaffected_and_uses_the_ordinary_subject(self):
        self.ticket.contact_email = 'ali@fitnessfirst.example'
        self.ticket.save(update_fields=['contact_email'])

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'add_reply', 'message': 'Just checking in.'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(TicketReply.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('New reply from our team', mail.outbox[0].subject)
        # Every email carries the inline logo now — no real (downloadable)
        # attachment for a plain reply, though.
        self.assertFalse(any(isinstance(a, tuple) for a in mail.outbox[0].attachments))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_reply_with_an_attachment_emails_it_to_the_customer(self):
        # No dedicated "quotation" flow anymore — whatever staff attaches
        # to any reply (quotation, report, photo) goes out on the email
        # itself, since the reply text already says what it's for.
        self.ticket.contact_email = 'ali@fitnessfirst.example'
        self.ticket.save(update_fields=['contact_email'])
        quote_file = SimpleUploadedFile('quote.pdf', b'%PDF-1.4 not a real pdf', content_type='application/pdf')

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'add_reply', 'message': 'Please see the attached quotation.', 'attachments': [quote_file],
        })
        self.assertEqual(response.status_code, 302)

        self.assertEqual(TicketReply.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('New reply from our team', mail.outbox[0].subject)
        # Two attachments now: the inline logo (every email gets one) plus
        # the file itself — pick it out by filename rather than assuming a
        # position or a plain tuple, since the logo attaches as a raw
        # MIMEImage, not a (name, content, type) tuple the way this does.
        pdf_names = [
            a[0] for a in mail.outbox[0].attachments
            if isinstance(a, tuple) and a[0].endswith('.pdf')
        ]
        self.assertEqual(len(pdf_names), 1)
        self.assertTrue(pdf_names[0].startswith('quote'))

    @override_settings(SEND_EMAILS_IN_BACKGROUND=True)
    def test_the_email_goes_out_after_the_page_answers(self):
        import threading
        self.ticket.contact_email = 'ali@fitnessfirst.example'
        self.ticket.save(update_fields=['contact_email'])
        self.client.login(username='support1', password='pass12345')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {'action': 'add_reply', 'message': 'On our way.'})
        self.assertEqual(response.status_code, 302)
        for thread in threading.enumerate():
            if thread is not threading.current_thread():
                thread.join(timeout=10)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('New reply from our team', mail.outbox[0].subject)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_a_reply_takes_several_files_up_to_the_limit(self):
        self.client.login(username='support1', password='pass12345')
        files = [SimpleUploadedFile(f'photo{n}.jpg', REAL_JPEG, content_type='image/jpeg') for n in range(11)]
        response = self.client.post(self.url, {'action': 'add_reply', 'message': 'Too many', 'attachments': files})
        self.assertContains(response, 'You can attach up to 10 files.')
        self.assertFalse(TicketReply.objects.exists())
        files = [SimpleUploadedFile(f'photo{n}.jpg', REAL_JPEG, content_type='image/jpeg') for n in range(3)]
        self.client.post(self.url, {'action': 'add_reply', 'message': 'Three photos', 'attachments': files})
        self.assertEqual(len(TicketReply.objects.get().all_files), 3)

    def test_customer_reply_creates_a_new_reply_notification(self):
        portal_user = User.objects.create_user('fitnessfirst_portal2', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user)
        self.ticket.customer = self.customer
        self.ticket.save(update_fields=['customer'])

        self.client.login(username='fitnessfirst_portal2', password='pass12345')
        self.client.post(f'/tasks/tickets/status/{self.ticket.token}/', {'message': 'Any update?'})

        notification = self.ticket.notifications.get()
        self.assertEqual(notification.kind, TicketNotification.Kind.NEW_REPLY)
        self.assertIsNone(notification.seen_at)

    def test_support_manager_opening_the_ticket_marks_notifications_seen(self):
        TicketNotification.objects.create(
            ticket=self.ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=timezone.now(),
        )

        self.client.login(username='support1', password='pass12345')
        self.client.get(self.url)

        notification = self.ticket.notifications.get()
        self.assertIsNotNone(notification.seen_at)

    def test_supervisor_cannot_open_the_ticket_to_clear_notifications(self):
        # A supervisor has no ticket access at all now, so this is moot
        # in practice — confirming it anyway: blocked entirely, and the
        # notification stays right where it was.
        TicketNotification.objects.create(
            ticket=self.ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

        notification = self.ticket.notifications.get()
        self.assertIsNone(notification.seen_at)


class TicketStatusAccessTests(TaskTestCase):
    """ticket_status used to be a public, no-login page (the token was the
    customer's whole identity). It's now login-required and scoped to the
    ticket's own customer — these lock that in.
    """
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst_portal', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.portal_user)
        self.ticket = CustomerTicket.objects.create(
            country=self.country, customer=self.customer,
            ticket_number='AE-T0001', company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/tickets/status/{self.ticket.token}/'

    def test_anonymous_visitor_is_redirected_to_the_portal_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/customers/portal/login/', response.url)

    def test_a_different_customer_is_told_the_link_is_for_another_account(self):
        other_customer = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        other_user = User.objects.create_user('other_portal', password='pass12345')
        PortalUser.objects.create(customer=other_customer, user=other_user)

        self.client.login(username='other_portal', password='pass12345')
        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], '/customers/portal/requests/')
        self.assertContains(response, 'That link is for a request on a different customer account.')
        self.assertNotContains(response, self.ticket.ticket_number)  # never whose, or which

    def test_staff_login_gets_403_too(self):
        # ticket_review is the staff-facing equivalent — a technician
        # account has no .customer, so it's turned away here regardless
        # of role.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_the_owning_customer_can_view_it(self):
        self.client.login(username='fitnessfirst_portal', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


class NotificationBellTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        TicketNotification.objects.create(
            ticket=self.ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=timezone.now(),
        )
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=self.support_user, country=self.country, full_name='Sara Support',
            language='en', role=Technician.Role.COUNTRY_SUPPORT, employment_type='staff',
        )
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

    def test_country_support_manager_sees_the_unseen_ticket_notification(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.context['unseen_notification_count'], 1)
        self.assertEqual(len(response.context['unseen_notifications']), 1)

    def test_plain_manager_does_not_see_ticket_notifications(self):
        # manage_tickets moved to the technical support manager — a
        # plain manager's bell stays empty until there's a task for them.
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertEqual(response.context['unseen_notification_count'], 0)

    def test_country_support_manager_does_not_see_task_notifications(self):
        # The country support manager isn't manager-tier, so the task
        # half of the bell stays off for them, symmetric to the above.
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        TaskNotification.objects.create(task=task, created_at=timezone.now())

        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.context['unseen_notification_count'], 1)
        kinds = {item['kind'] for item in response.context['unseen_notifications']}
        self.assertNotIn('new_task', kinds)

    def test_admin_sees_new_tasks_but_not_new_tickets(self):
        # New tickets go only to the roles chosen for them (Operations Manager,
        # Country Support Manager); an admin's bell keeps the task half.
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        TaskNotification.objects.create(task=task, created_at=timezone.now())

        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertEqual(response.context['unseen_notification_count'], 1)
        kinds = {item['kind'] for item in response.context['unseen_notifications']}
        self.assertIn('new_task', kinds)
        self.assertNotIn('new_ticket', kinds)

    def test_supervisor_gets_no_bell_context_at_all(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertNotIn('unseen_notification_count', response.context)

    def test_technician_gets_no_bell_context_at_all(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertNotIn('unseen_notification_count', response.context)

    def test_another_countrys_ticket_counts_only_for_the_company_wide_roles(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_ticket = CustomerTicket.objects.create(
            country=other_country, ticket_number='EG-T0001', company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )
        TicketNotification.objects.create(
            ticket=other_ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=timezone.now(),
        )

        # The Technical Support Manager is told of every country's new tickets: both count.
        technical = User.objects.create_user('technical1', password='pass12345')
        Technician.objects.create(user=technical, country=self.country, full_name='Tech Support', language='en',
                                  role=Technician.Role.SUPPORT_MANAGER, employment_type='staff')
        self.client.login(username='technical1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/tickets/').context['unseen_notification_count'], 2)
        # A Country Support Manager: its own country only.
        desk = User.objects.create_user('desk1', password='pass12345')
        Technician.objects.create(user=desk, country=self.country, full_name='Desk', language='en',
                                  role=Technician.Role.COUNTRY_SUPPORT, employment_type='staff')
        self.client.login(username='desk1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/tickets/').context['unseen_notification_count'], 1)

    def test_manager_opening_the_task_marks_its_notification_seen(self):
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        TaskNotification.objects.create(task=task, created_at=timezone.now())

        self.client.login(username='manager1', password='pass12345')
        self.client.get(f'/tasks/{task.pk}/')

        notification = task.notifications.get()
        self.assertIsNotNone(notification.seen_at)

    def test_supervisor_opening_the_task_does_not_mark_its_notification_seen(self):
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        TaskNotification.objects.create(task=task, created_at=timezone.now())

        self.client.login(username='supervisor1', password='pass12345')
        self.client.get(f'/tasks/{task.pk}/')

        notification = task.notifications.get()
        self.assertIsNone(notification.seen_at)


class TaskAssignTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, required_skill=self.skill,
            priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        self.helper_user = User.objects.create_user('helper1', password='pass12345')
        self.helper = Technician.objects.create(
            user=self.helper_user, country=self.country, full_name='Omar Helper',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=3, source=TechnicianSkill.Source.SUPERVISOR,
            set_by=self.technician, set_on=timezone.now().date(),
        )
        self.url = f'/tasks/{self.task.pk}/assign/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_other_country_task_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        other_task = Task.objects.create(
            task_number='EG-0001', site=other_site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{other_task.pk}/assign/')
        self.assertEqual(response.status_code, 404)

    def _other_job(self, number, start, hours, technician=None):
        job = Task.objects.create(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED, scheduled_for=start, estimated_hours=hours,
        )
        TaskAssignment.objects.create(
            task=job, technician=technician or self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        return job

    def test_each_candidate_shows_their_load_and_clashes_that_day(self):
        self.task.scheduled_for, self.task.estimated_hours = dubai_time(2026, 9, 9, 10), 2
        self.task.save(update_fields=['scheduled_for', 'estimated_hours'])
        clash = self._other_job('AE-0011', dubai_time(2026, 9, 9, 9), 2)       # 9–11: overlaps 10–12
        self._other_job('AE-0012', dubai_time(2026, 9, 9, 13), 3)               # 13–16: doesn't
        self._other_job('AE-0013', dubai_time(2026, 9, 10, 10), 2)              # another day
        self.client.login(username='supervisor1', password='pass12345')
        candidates = {c.pk: c for c in self.client.get(self.url).context['candidates']}
        tarek = candidates[self.technician.pk]
        self.assertEqual(len(tarek.day_jobs), 2)
        self.assertEqual(tarek.day_hours, 5)
        self.assertEqual(tarek.clashes, [clash])
        self.assertEqual((candidates[self.helper.pk].day_jobs, candidates[self.helper.pk].clashes), ([], []))

    def test_assigning_into_a_clash_saves_with_a_warning(self):
        self.task.scheduled_for, self.task.estimated_hours = dubai_time(2026, 9, 9, 10), 2
        self.task.save(update_fields=['scheduled_for', 'estimated_hours'])
        self._other_job('AE-0011', dubai_time(2026, 9, 9, 11), None)  # no estimate: counts as an hour
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk}, follow=True)
        self.assertTrue(self.task.assignments.filter(technician=self.technician, is_active=True).exists())
        warnings = [str(m.message) for m in response.context['messages'] if m.level_tag == 'warning']
        self.assertEqual(len(warnings), 1)
        self.assertIn('AE-0011', warnings[0])

    def test_no_day_yet_means_no_load(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertIsNone(response.context['task_day'])
        self.assertTrue(all(c.day_jobs == [] for c in response.context['candidates']))

    def test_set_lead_assigns_and_advances_status(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)
        assignment = self.task.assignments.get(is_active=True)
        self.assertEqual(assignment.technician, self.technician)
        self.assertEqual(assignment.role, TaskAssignment.Role.LEAD)
        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.ASSIGNED)

    def test_candidates_show_their_next_scheduled_task(self):
        busy_task = Task.objects.create(
            task_number='AE-0002', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
            scheduled_for=timezone.now() + timedelta(days=1),
        )
        TaskAssignment.objects.create(
            task=busy_task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)

        candidates = {c.pk: c for c in response.context['candidates']}
        self.assertEqual(candidates[self.technician.pk].next_scheduled_task, busy_task)
        self.assertIsNone(candidates[self.helper.pk].next_scheduled_task)

    def test_replace_lead_without_reason_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk})

        response = self.client.post(self.url, {'action': 'set_lead', 'technician': self.helper.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['set_lead_form'].errors)
        assignment = self.task.assignments.get(is_active=True)
        self.assertEqual(assignment.technician, self.technician)

    def test_replace_lead_with_reason_ends_old_assignment(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk})

        response = self.client.post(self.url, {
            'action': 'set_lead', 'technician': self.helper.pk,
            'end_reason': TaskAssignment.EndReason.OVERLOADED,
        })
        self.assertEqual(response.status_code, 302)

        old_assignment = self.task.assignments.get(technician=self.technician)
        self.assertFalse(old_assignment.is_active)
        self.assertEqual(old_assignment.end_reason, TaskAssignment.EndReason.OVERLOADED)

        new_assignment = self.task.assignments.get(is_active=True)
        self.assertEqual(new_assignment.technician, self.helper)
        self.assertEqual(
            list(self.task.events.values_list('event_type', flat=True)),
            [TaskEvent.EventType.ASSIGNED, TaskEvent.EventType.REASSIGNED],
        )

    def test_add_helper_requires_an_active_lead(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'add_helper', 'technician': self.helper.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TaskAssignment.objects.filter(role=TaskAssignment.Role.HELPER).count(), 0)

    def test_add_and_remove_helper(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk})
        response = self.client.post(self.url, {'action': 'add_helper', 'technician': self.helper.pk})
        self.assertEqual(response.status_code, 302)

        helper_assignment = self.task.assignments.get(role=TaskAssignment.Role.HELPER)
        self.assertTrue(helper_assignment.is_active)

        response = self.client.post(self.url, {
            'action': 'remove_helper', 'assignment_id': helper_assignment.pk,
            'end_reason': TaskAssignment.EndReason.SICK,
        })
        self.assertEqual(response.status_code, 302)
        helper_assignment.refresh_from_db()
        self.assertFalse(helper_assignment.is_active)
        self.assertEqual(helper_assignment.end_reason, TaskAssignment.EndReason.SICK)

    def test_remove_helper_without_reason_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk})
        self.client.post(self.url, {'action': 'add_helper', 'technician': self.helper.pk})
        helper_assignment = self.task.assignments.get(role=TaskAssignment.Role.HELPER)

        response = self.client.post(self.url, {
            'action': 'remove_helper', 'assignment_id': helper_assignment.pk,
        })
        self.assertEqual(response.status_code, 200)
        helper_assignment.refresh_from_db()
        self.assertTrue(helper_assignment.is_active)

    def test_assignment_is_locked_once_in_progress(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.client.login(username='supervisor1', password='pass12345')

        response = self.client.get(self.url)
        self.assertTrue(response.context['locked'])

        response = self.client.post(self.url, {'action': 'set_lead', 'technician': self.helper.pk})
        self.assertEqual(response.status_code, 200)
        assignment = self.task.assignments.get(is_active=True)
        self.assertEqual(assignment.technician, self.technician)

    def test_candidates_exclude_technicians_already_on_the_task(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {'action': 'set_lead', 'technician': self.technician.pk})

        response = self.client.get(self.url)
        candidate_ids = {t.pk for t in response.context['candidates']}
        self.assertNotIn(self.technician.pk, candidate_ids)
        self.assertIn(self.helper.pk, candidate_ids)

    def test_candidates_are_restricted_to_the_task_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        candidate_names = {t.full_name for t in response.context['candidates']}
        self.assertNotIn('Nour Cairo', candidate_names)

    def test_unavailable_technician_still_shown_but_not_selectable(self):
        self.helper.is_available = False
        self.helper.unavailable_reason = Technician.UnavailableReason.SICK
        self.helper.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)

        candidate_ids = {t.pk for t in response.context['candidates']}
        self.assertIn(self.helper.pk, candidate_ids)

        response = self.client.post(self.url, {'action': 'set_lead', 'technician': self.helper.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.task.assignments.filter(technician=self.helper).exists())

    def test_status_column_has_no_toggle_controls(self):
        # Moved to technician_availability — this screen only displays
        # status now, it can't change it.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Mark unavailable')
        self.assertNotContains(response, 'Mark available')


class TechnicianAvailabilityTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.url = '/tasks/technicians/availability/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_mark_unavailable_requires_a_reason(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'mark_unavailable', 'technician_id': self.technician.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.technician.refresh_from_db()
        self.assertTrue(self.technician.is_available)

    def test_mark_unavailable_sets_reason(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'mark_unavailable', 'technician_id': self.technician.pk, 'reason': 'sick',
        })
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertFalse(self.technician.is_available)
        self.assertEqual(self.technician.unavailable_reason, 'sick')

    def test_mark_available_clears_reason(self):
        self.technician.is_available = False
        self.technician.unavailable_reason = Technician.UnavailableReason.HOLIDAY
        self.technician.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'mark_available', 'technician_id': self.technician.pk,
        })
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertTrue(self.technician.is_available)
        self.assertEqual(self.technician.unavailable_reason, '')

    def test_cannot_mark_unavailable_technician_from_another_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech2', password='pass12345')
        other_technician = Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo 2',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'mark_unavailable', 'technician_id': other_technician.pk, 'reason': 'sick',
        })
        self.assertEqual(response.status_code, 404)


class TaskOwnershipTests(TaskTestCase):
    """Once a task has a responsible supervisor, only they (or a manager)
    can edit it, manage its assignment, or act on it from the detail page
    — see _require_task_owner in views.py. An unowned task stays open to
    any supervisor, which the rest of TaskEditTests/TaskAssignTests already
    cover by never setting responsible_supervisor on their fixture tasks.
    """

    def setUp(self):
        super().setUp()
        self.owner_user = User.objects.create_user('supervisor2', password='pass12345')
        self.owner = Technician.objects.create(
            user=self.owner_user, country=self.country, full_name='Youssef Owner',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
            responsible_supervisor=self.owner,
        )

    def test_non_owning_supervisor_gets_403_editing(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/edit/')
        self.assertEqual(response.status_code, 403)

    def test_owning_supervisor_can_edit(self):
        self.client.login(username='supervisor2', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/edit/')
        self.assertEqual(response.status_code, 200)

    def test_manager_can_edit_regardless_of_owner(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/edit/')
        self.assertEqual(response.status_code, 200)

    def test_non_owning_supervisor_gets_403_on_assign_screen(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/{self.task.pk}/assign/')
        self.assertEqual(response.status_code, 403)

    def test_non_owning_supervisor_gets_403_notifying_customer(self):
        self.task.scheduled_for = timezone.now()
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_schedule'})
        self.assertEqual(response.status_code, 403)

    def test_non_owning_supervisor_gets_403_notifying_shipping(self):
        self.task.shipping_tracking_number = 'TRACK-99'
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_shipping'})
        self.assertEqual(response.status_code, 403)

    def test_manager_can_reassign_the_responsible_supervisor(self):
        other_owner_user = User.objects.create_user('supervisor3', password='pass12345')
        other_owner = Technician.objects.create(
            user=other_owner_user, country=self.country, full_name='Lina Other',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/edit/', {
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE, 'is_warranty': '',
            'responsible_supervisor': other_owner.pk,
            'products-TOTAL_FORMS': '0', 'products-INITIAL_FORMS': '0',
            'products-MIN_NUM_FORMS': '0', 'products-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.responsible_supervisor, other_owner)

    def test_any_supervisor_can_claim_an_unowned_task(self):
        self.task.responsible_supervisor = None
        self.task.save()

        supervisor = Technician.objects.get(user=self.supervisor_user)
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/edit/', {
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CHARGEABLE, 'is_warranty': '',
            'responsible_supervisor': supervisor.pk,
            'products-TOTAL_FORMS': '0', 'products-INITIAL_FORMS': '0',
            'products-MIN_NUM_FORMS': '0', 'products-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.responsible_supervisor, supervisor)


class TaskWeekTests(TaskTestCase):
    def test_the_schedule_has_a_year_view(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'view': 'year', 'year': '2026'})
        self.assertEqual(response.context['view'], 'year')
        self.assertEqual(len(response.context['year_months']), 12)

    # 2026-09-07 is a Monday.
    WEEK_START = date(2026, 9, 7)

    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/week/')
        self.assertEqual(response.status_code, 403)

    def test_tasks_grouped_by_scheduled_local_day(self):
        monday_task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0))
        wednesday_task = self._make_task('AE-0002', scheduled_for=dubai_time(2026, 9, 9, 14, 0))

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(list(days[date(2026, 9, 7)]), [monday_task])
        self.assertEqual(list(days[date(2026, 9, 9)]), [wednesday_task])
        self.assertEqual(list(days[date(2026, 9, 8)]), [])

    def test_task_outside_window_is_excluded(self):
        self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 20, 9, 0))

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})

        all_tasks = [task for day in response.context['days'] for task in day['tasks']]
        self.assertEqual(all_tasks, [])

    def test_open_unscheduled_task_appears_in_unscheduled_bucket(self):
        task = self._make_task('AE-0001', scheduled_for=None, status=Task.Status.NEW)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})

        self.assertEqual(list(response.context['unscheduled']), [task])

    def test_closed_unscheduled_task_is_excluded(self):
        self._make_task('AE-0001', scheduled_for=None, status=Task.Status.CLOSED)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})

        self.assertEqual(list(response.context['unscheduled']), [])

    def test_default_start_is_a_monday(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/')
        self.assertEqual(response.context['start'].weekday(), 0)

    def test_week_navigation(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})
        self.assertEqual(response.context['prev_start'], date(2026, 8, 31))
        self.assertEqual(response.context['next_start'], date(2026, 9, 14))

    def test_other_country_task_excluded(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        self._make_task('EG-0001', site=other_site, scheduled_for=dubai_time(2026, 9, 7, 9, 0))

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})

        all_tasks = [task for day in response.context['days'] for task in day['tasks']]
        self.assertEqual(all_tasks, [])


    def test_a_task_booked_by_day_only_shows_on_its_day_after_timed_ones(self):
        timed = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 8, 9, 0))
        day_only = self._make_task('AE-0002', scheduled_for=None, scheduled_date=date(2026, 9, 8))
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'start': '2026-09-07'})
        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(days[date(2026, 9, 8)], [timed, day_only])
        self.assertNotIn(day_only, list(response.context['unscheduled']))
        self.assertContains(response, 'Time to be confirmed')

    def test_month_view_covers_whole_weeks_and_folds_extra_tasks(self):
        for number in range(5):
            self._make_task(f'AE-01{number}', scheduled_for=dubai_time(2026, 9, 15, 8 + number, 0))
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/week/', {'view': 'month', 'month': '2026-09'})
        self.assertEqual(response.context['view'], 'month')
        days = response.context['days']
        # September 2026 starts on a Tuesday and ends on a Wednesday.
        self.assertEqual((days[0]['date'], days[-1]['date']), (date(2026, 8, 31), date(2026, 10, 4)))
        self.assertTrue(all(len(week) == 7 for week in response.context['weeks']))
        day = next(day for day in days if day['date'] == date(2026, 9, 15))
        self.assertEqual((len(day['shown']), day['more']), (3, 2))
        self.assertContains(response, '+2 more')
        self.assertEqual(response.context['prev_month'], date(2026, 8, 1))
        self.assertEqual(response.context['next_month'], date(2026, 10, 1))


class MyWeekTests(TaskTestCase):
    WEEK_START = date(2026, 9, 7)

    def setUp(self):
        super().setUp()
        self.other_user = User.objects.create_user('other_tech', password='pass12345')
        self.other_technician = Technician.objects.create(
            user=self.other_user, country=self.country, full_name='Nour Other',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-week/')
        self.assertEqual(response.status_code, 302)

    def test_shows_task_where_i_am_lead(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0))
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(list(days[date(2026, 9, 7)]), [task])
        self.assertEqual(days[date(2026, 9, 7)][0].my_role, TaskAssignment.Role.LEAD)

    def test_shows_task_where_i_am_helper(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0))
        TaskAssignment.objects.create(
            task=task, technician=self.other_technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(days[date(2026, 9, 7)][0].my_role, TaskAssignment.Role.HELPER)

    def test_does_not_show_other_technicians_tasks(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0))
        TaskAssignment.objects.create(
            task=task, technician=self.other_technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})

        all_tasks = [t for day in response.context['days'] for t in day['tasks']]
        self.assertEqual(all_tasks, [])

    def test_shows_task_where_i_am_responsible_supervisor(self):
        supervisor = Technician.objects.get(user=self.supervisor_user)
        task = self._make_task(
            'AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0), responsible_supervisor=supervisor,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(list(days[date(2026, 9, 7)]), [task])
        self.assertEqual(days[date(2026, 9, 7)][0].my_role, 'responsible')

    def test_responsible_supervisor_task_not_duplicated_when_also_assigned(self):
        supervisor = Technician.objects.get(user=self.supervisor_user)
        task = self._make_task(
            'AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0), responsible_supervisor=supervisor,
        )
        TaskAssignment.objects.create(
            task=task, technician=supervisor, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(len(days[date(2026, 9, 7)]), 1)
        self.assertEqual(days[date(2026, 9, 7)][0].my_role, TaskAssignment.Role.LEAD)

    def test_unscheduled_responsible_task_appears(self):
        supervisor = Technician.objects.get(user=self.supervisor_user)
        task = self._make_task(
            'AE-0001', scheduled_for=None, status=Task.Status.NEW, responsible_supervisor=supervisor,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'view': 'week'})

        self.assertEqual(list(response.context['unscheduled']), [task])
        self.assertEqual(response.context['unscheduled'][0].my_role, 'responsible')

    def test_unscheduled_open_task_of_mine_appears(self):
        task = self._make_task('AE-0001', scheduled_for=None, status=Task.Status.NEW)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'view': 'week'})

        self.assertEqual(list(response.context['unscheduled']), [task])

    def test_unscheduled_completed_task_of_mine_still_appears(self):
        """A rejected report leaves the task at 'completed' — the technician

        still needs to resubmit it, so it must not vanish from their week
        just because it's no longer literally in-progress.
        """
        task = self._make_task('AE-0001', scheduled_for=None, status=Task.Status.COMPLETED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'view': 'week'})

        self.assertEqual(list(response.context['unscheduled']), [task])

    def _lead(self, task):
        self.technician.language = 'en'  # the page text is checked in English
        self.technician.save(update_fields=['language'])
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        return task

    def test_a_task_with_only_a_day_shows_on_that_day(self):
        task = self._lead(self._make_task('AE-0701', scheduled_date=date(2026, 9, 9), status=Task.Status.ASSIGNED))
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})
        wednesday = next(day for day in response.context['days'] if day['date'] == date(2026, 9, 9))
        self.assertEqual(wednesday['tasks'], [task])
        self.assertNotIn(task, response.context['unscheduled'])
        self.assertContains(response, 'Any time that day')

    def test_a_missed_job_shows_as_overdue_whatever_the_week(self):
        missed = self._lead(self._make_task(
            'AE-0702', scheduled_for=timezone.now() - timedelta(days=10), status=Task.Status.ACCEPTED,
        ))
        self._lead(self._make_task(  # done, waiting for review: not overdue
            'AE-0703', scheduled_for=timezone.now() - timedelta(days=10), status=Task.Status.COMPLETED,
        ))
        self.client.login(username='tech1', password='pass12345')
        for week in ({}, {'start': '2030-01-07'}):
            response = self.client.get('/tasks/my-week/', week)
            self.assertEqual(response.context['overdue'], [missed])
        self.assertContains(response, 'Overdue')

    def test_a_cancelled_task_does_not_show(self):
        self._lead(self._make_task(
            'AE-0704', scheduled_for=dubai_time(2026, 9, 9, 9, 0), status=Task.Status.CANCELLED,
        ))
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-07'})
        self.assertEqual(sum(len(day['tasks']) for day in response.context['days']), 0)

    def test_the_week_starts_on_the_countrys_first_working_day(self):
        # Egypt and Saudi Arabia: only Friday off, so the week runs Saturday to Thursday.
        self.country.weekend_days = '4'
        self.country.save(update_fields=['weekend_days'])
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'start': '2026-09-09'})  # a Wednesday
        days = response.context['days']
        self.assertEqual(days[0]['date'], date(2026, 9, 5))  # Saturday
        self.assertEqual([day['date'] for day in days if day['is_weekend']], [date(2026, 9, 11)])  # Friday

    def test_today_is_the_default_with_the_job_under_way_first(self):
        dubai = ZoneInfo('Asia/Dubai')  # the page's "today" is the technician's own country's
        today = timezone.localtime(timezone.now(), dubai).date()
        today_at = lambda hour: datetime(today.year, today.month, today.day, hour, tzinfo=dubai)
        early = self._lead(self._make_task('AE-0801', scheduled_for=today_at(8), status=Task.Status.ASSIGNED))
        under_way = self._lead(self._make_task('AE-0802', scheduled_for=today_at(10), status=Task.Status.IN_PROGRESS))
        done = self._lead(self._make_task('AE-0803', scheduled_for=today_at(7), status=Task.Status.COMPLETED))
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertEqual(response.context['view'], 'today')
        self.assertEqual(response.context['next_job'], under_way)
        self.assertEqual(response.context['later_today'], [early])
        self.assertEqual(response.context['done_today'], [done])
        self.assertEqual(response.context['today_count'], 3)
        self.assertContains(response, 'Under way')
        # The week is one tap away.
        self.assertEqual(self.client.get('/tasks/my-week/', {'view': 'week'}).context['view'], 'week')

    def test_the_next_job_has_its_tap_and_directions(self):
        self.site.latitude, self.site.longitude = Decimal('25.080000'), Decimal('55.140000')
        self.site.save(update_fields=['latitude', 'longitude'])
        task = self._lead(self._make_task(
            'AE-0804', scheduled_for=datetime.combine(
                timezone.localtime(timezone.now(), ZoneInfo('Asia/Dubai')).date(), time(23), ZoneInfo('Asia/Dubai'),
            ), status=Task.Status.ASSIGNED,
        ))
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertEqual(response.context['next_job'].next_action, 'accept')
        self.assertIn('query=25.080000%2C55.140000', response.context['next_job'].directions_url)
        self.assertContains(response, 'Confirm task')

        # Tapping it on the card comes back to Today.
        response = self.client.post(f'/tasks/my/{task.pk}/', {'action': 'accept', 'next': 'today'})
        self.assertRedirects(response, '/tasks/my-week/', fetch_redirect_response=False)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    def test_a_quiet_day_says_so(self):
        self.technician.language = 'en'
        self.technician.save(update_fields=['language'])
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertIsNone(response.context['next_job'])
        self.assertContains(response, 'Nothing left to do today.')

    def test_the_year_shows_every_visit_like_a_quarterly_maintenance(self):
        # A preventive maintenance visit every 3 months.
        for number, month in (('AE-0901', 1), ('AE-0902', 4), ('AE-0903', 7), ('AE-0904', 10)):
            self._lead(self._make_task(number, scheduled_for=dubai_time(2027, month, 12, 9), status=Task.Status.ASSIGNED))
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'view': 'year', 'year': '2027'})
        months = response.context['year_months']
        self.assertEqual(len(months), 12)
        self.assertEqual([month['count'] for month in months], [1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0])
        busy_day = next(cell for cell in months[3]['cells'] if cell and cell['tasks'])
        self.assertEqual(busy_day['date'], date(2027, 4, 12))
        self.assertEqual(busy_day['tasks'][0].my_role, 'lead')
        self.assertContains(response, 'My schedule')

    def test_the_month_shows_my_tasks_only(self):
        mine = self._lead(self._make_task('AE-0905', scheduled_for=dubai_time(2026, 9, 9, 9)))
        self._make_task('AE-0906', scheduled_for=dubai_time(2026, 9, 10, 9))  # not mine
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/', {'view': 'month', 'month': '2026-09'})
        booked = [task for day in response.context['days'] for task in day['tasks']]
        self.assertEqual(booked, [mine])
        self.assertContains(response, f'href="/tasks/my/{mine.pk}/"')

    def test_supervisor_can_view_their_own_week_too(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertEqual(response.status_code, 200)


class MyProgressTests(TaskTestCase):
    # 2026-09-07 is a Monday.
    WEEK_START = date(2026, 9, 7)

    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-progress/')
        self.assertEqual(response.status_code, 302)

    def test_skill_shows_level_when_rated(self):
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=3, source=TechnicianSkill.Source.SUPERVISOR,
            set_by=self.technician, set_on=date(2026, 9, 1),
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        levels = {skill: skill.current.level for skill in response.context['skills'] if skill.current}
        self.assertEqual(levels[self.skill], 3)

    def test_unrated_skill_has_no_current_rating(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        skills_by_pk = {skill.pk: skill for skill in response.context['skills']}
        self.assertIsNone(skills_by_pk[self.skill.pk].current)

    def test_inactive_skill_not_shown(self):
        self.skill.is_active = False
        self.skill.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertNotIn(self.skill, response.context['skills'])

    def test_assigned_count_includes_scheduled_task_this_week(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 9, 9, 0))
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/', {'start': '2026-09-07'})

        self.assertEqual(response.context['assigned_count'], 1)

    def test_assigned_count_excludes_other_technicians_tasks(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 9, 9, 0))
        other_user = User.objects.create_user('other_tech', password='pass12345')
        other_technician = Technician.objects.create(
            user=other_user, country=self.country, full_name='Nour Other',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=task, technician=other_technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/', {'start': '2026-09-07'})

        self.assertEqual(response.context['assigned_count'], 0)

    def test_completed_count_counts_this_weeks_completed_events(self):
        task = self._make_task('AE-0001')
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.COMPLETED,
            occurred_at=dubai_time(2026, 9, 9, 9, 0), actor=self.tech_user,
        )
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.COMPLETED,
            occurred_at=dubai_time(2026, 9, 20, 9, 0), actor=self.tech_user,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/', {'start': '2026-09-07'})

        self.assertEqual(response.context['completed_count'], 1)

    def test_a_manager_sees_a_technicians_progress_and_the_team(self):
        Technician.objects.create(
            user=User.objects.create_user('manager1', password='pass12345'), country=self.country,
            full_name='Maya Manager', language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        task = Task.objects.create(
            task_number='AE-0960', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=timezone.now(), actor=self.tech_user,
        )
        self.client.login(username='manager1', password='pass12345')

        response = self.client.get(f'/tasks/technicians/{self.technician.pk}/progress/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['viewing_other'])
        self.assertEqual(response.context['trend_chart']['this_month'], 1)
        self.assertContains(response, 'Tasks completed per month')
        self.assertNotContains(response, 'Self-assess a skill')

        team = self.client.get('/tasks/technicians/progress/').context['rows']
        row = next(row for row in team if row['technician'] == self.technician)
        self.assertEqual((row['completed'], row['change']), (1, 1))
        self.assertEqual(len(row['trend']['line_points'].split()), len(row['trend']['periods']))

    def test_a_technician_cannot_see_someone_elses_progress(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/technicians/progress/').status_code, 403)
        self.assertEqual(self.client.get(f'/tasks/technicians/{self.technician.pk}/progress/').status_code, 403)

    def test_my_progress_charts_my_own_tasks_per_month(self):
        now = timezone.now()
        mine, other = [
            Task.objects.create(
                task_number=number, site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
                billing_type=Task.BillingType.CHARGEABLE, reported_at=now, created_by=self.supervisor_user,
            )
            for number in ('AE-0901', 'AE-0902')
        ]
        TaskAssignment.objects.create(
            task=mine, technician=self.technician, role=TaskAssignment.Role.LEAD, assigned_at=now, is_active=True,
        )
        for task in (mine, other):  # someone else's task doesn't count
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=now, actor=self.tech_user,
            )
        # On the system for over a year: the chart shows the last year.
        self.tech_user.date_joined = now - timedelta(days=500)
        self.tech_user.save(update_fields=['date_joined'])
        self.client.login(username='tech1', password='pass12345')
        chart = self.client.get('/tasks/my-progress/').context['trend_chart']
        self.assertEqual(len(chart['periods']), 12)
        self.assertEqual(chart['periods'][-1]['completed'], 1)
        self.assertEqual((chart['this_month'], chart['change']), (1, 1))
        # Twelve points, the last at the top of the scale.
        points = chart['line_points'].split()
        self.assertEqual(len(points), 12)
        self.assertEqual(points[-1].split(',')[1], str(100 - chart['periods'][-1]['completed_pct']))

    def test_the_chart_starts_the_month_the_account_was_created(self):
        this_month = timezone.localdate().replace(day=1)
        three_months_ago = this_month
        for _month in range(3):
            three_months_ago = (three_months_ago - timedelta(days=1)).replace(day=1)
        self.tech_user.date_joined = timezone.make_aware(datetime.combine(three_months_ago, datetime.min.time()))
        self.tech_user.save(update_fields=['date_joined'])
        self.client.login(username='tech1', password='pass12345')
        chart = self.client.get('/tasks/my-progress/').context['trend_chart']
        self.assertEqual([period['start'] for period in chart['periods']][0], three_months_ago)
        self.assertEqual(len(chart['periods']), 4)

        # Their very first month: one point, nothing to compare with yet.
        self.tech_user.date_joined = timezone.now()
        self.tech_user.save(update_fields=['date_joined'])
        chart = self.client.get('/tasks/my-progress/').context['trend_chart']
        self.assertEqual(len(chart['periods']), 1)
        self.assertIsNone(chart['change'])

    def test_my_progress_shows_a_chosen_past_month_but_never_a_future_one(self):
        self.client.login(username='tech1', password='pass12345')
        this_month = timezone.localdate().replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)

        response = self.client.get('/tasks/my-progress/', {'month': last_month.strftime('%Y-%m')})
        self.assertEqual(response.context['month_first'], last_month)
        self.assertEqual(response.context['next_month'], this_month)
        self.assertFalse(response.context['is_this_month'])

        response = self.client.get('/tasks/my-progress/', {'month': '2999-01'})
        self.assertEqual(response.context['month_first'], this_month)
        self.assertIsNone(response.context['next_month'])

    def test_supervisor_can_view_their_own_progress_too(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertEqual(response.status_code, 200)

    def test_certified_once_every_non_cardio_skill_and_conduct_area_is_confirmed(self):
        # Isolate to just this one non-cardio skill, so seeded reference
        # data (other brands' skills) can't half-satisfy the bar.
        Skill.objects.exclude(pk=self.skill.pk).filter(category=Skill.Category.OTHER).update(is_active=False)

        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=3, source=TechnicianSkill.Source.SUPERVISOR,
            set_by=self.technician, set_on=date(2026, 9, 1),
        )
        for area in ConductArea.objects.filter(is_active=True):
            TechnicianConduct.objects.create(
                technician=self.technician, conduct_area=area, level=3,
                source=TechnicianConduct.Source.SUPERVISOR, set_by=self.technician, set_on=date(2026, 9, 1),
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertTrue(response.context['certification']['is_certified'])

    def test_not_certified_while_a_conduct_area_is_missing(self):
        Skill.objects.exclude(pk=self.skill.pk).filter(category=Skill.Category.OTHER).update(is_active=False)

        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=3, source=TechnicianSkill.Source.SUPERVISOR,
            set_by=self.technician, set_on=date(2026, 9, 1),
        )
        # Confirm all but one conduct area.
        for area in ConductArea.objects.filter(is_active=True)[1:]:
            TechnicianConduct.objects.create(
                technician=self.technician, conduct_area=area, level=3,
                source=TechnicianConduct.Source.SUPERVISOR, set_by=self.technician, set_on=date(2026, 9, 1),
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertFalse(response.context['certification']['is_certified'])

    def test_self_rated_skill_does_not_count_toward_certification(self):
        Skill.objects.exclude(pk=self.skill.pk).filter(category=Skill.Category.OTHER).update(is_active=False)

        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=3, source=TechnicianSkill.Source.SELF,
            set_by=self.technician, set_on=date(2026, 9, 1),
        )
        for area in ConductArea.objects.filter(is_active=True):
            TechnicianConduct.objects.create(
                technician=self.technician, conduct_area=area, level=3,
                source=TechnicianConduct.Source.SUPERVISOR, set_by=self.technician, set_on=date(2026, 9, 1),
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertFalse(response.context['certification']['is_certified'])
        self.assertEqual(response.context['certification']['non_cardio_certified'], 0)

    def test_ninety_day_progress_is_none_without_a_hire_date(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertIsNone(response.context['ninety_day'])

    def test_on_track_when_fully_certified_well_within_90_days(self):
        Skill.objects.exclude(pk=self.skill.pk).filter(category=Skill.Category.OTHER).update(is_active=False)
        # "Today" in the view is computed with the technician's own country
        # timezone active (see spots/middleware.py) — using the process
        # default (UTC) here instead is flaky for a few hours each day
        # whenever Dubai's calendar date has already advanced past UTC's.
        today_in_dubai = timezone.localtime(timezone.now(), ZoneInfo('Asia/Dubai')).date()
        self.technician.hired_on = today_in_dubai - timedelta(days=10)
        self.technician.save()

        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=3, source=TechnicianSkill.Source.SUPERVISOR,
            set_by=self.technician, set_on=date(2026, 9, 1),
        )
        for area in ConductArea.objects.filter(is_active=True):
            TechnicianConduct.objects.create(
                technician=self.technician, conduct_area=area, level=3,
                source=TechnicianConduct.Source.SUPERVISOR, set_by=self.technician, set_on=date(2026, 9, 1),
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        ninety_day = response.context['ninety_day']
        self.assertTrue(ninety_day['on_track'])
        self.assertEqual(ninety_day['day_count'], 10)

    def test_behind_pace_when_far_along_with_nothing_confirmed(self):
        today_in_dubai = timezone.localtime(timezone.now(), ZoneInfo('Asia/Dubai')).date()
        self.technician.hired_on = today_in_dubai - timedelta(days=60)
        self.technician.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        ninety_day = response.context['ninety_day']
        self.assertFalse(ninety_day['on_track'])
        self.assertEqual(ninety_day['days_remaining'], 30)


class ReliabilityStatsTests(TaskTestCase):
    """Plain facts, never a combined score — see _reliability_stats.
    RELIABILITY_MIN_SAMPLE (20) gates every rate, not the plain counts.
    """

    def _led_task(self, number, *, promised_at, arrived_at=None, status=Task.Status.CLOSED, brand=None):
        task = Task.objects.create(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), promised_at=promised_at, created_by=self.supervisor_user,
            status=status, brand=brand,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        if arrived_at:
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.ARRIVED, occurred_at=arrived_at, actor=self.tech_user,
            )
        return task

    def test_plain_counts_shown_below_the_sample_threshold(self):
        for i in range(3):
            self._led_task(
                f'AE-000{i}', promised_at=dubai_time(2026, 9, 10, 12, 0),
                arrived_at=dubai_time(2026, 9, 10, 11, 0),
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        reliability = response.context['reliability']

        self.assertEqual(reliability['total_completed'], 3)
        self.assertIsNone(reliability['on_time_percent'])
        self.assertEqual(reliability['on_time_sample_size'], 3)

    def test_helped_count_only_counts_helper_role(self):
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.CLOSED,
        )
        other_user = User.objects.create_user('other_tech', password='pass12345')
        other_tech = Technician.objects.create(
            user=other_user, country=self.country, full_name='Other Tech',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=task, technician=other_tech, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        reliability = response.context['reliability']

        self.assertEqual(reliability['total_completed'], 0)
        self.assertEqual(reliability['total_helped'], 1)

    def test_on_time_percent_shown_once_sample_threshold_is_met(self):
        for i in range(15):
            self._led_task(
                f'AE-{1000 + i}', promised_at=dubai_time(2026, 9, 10, 12, 0),
                arrived_at=dubai_time(2026, 9, 10, 11, 0),
            )
        for i in range(5):
            self._led_task(
                f'AE-{2000 + i}', promised_at=dubai_time(2026, 9, 10, 12, 0),
                arrived_at=dubai_time(2026, 9, 10, 13, 0),
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        reliability = response.context['reliability']

        self.assertEqual(reliability['total_completed'], 20)
        self.assertEqual(reliability['on_time_percent'], 75)

    def test_blocked_tasks_excluded_from_on_time_stats(self):
        for i in range(20):
            self._led_task(
                f'AE-{1000 + i}', promised_at=dubai_time(2026, 9, 10, 12, 0),
                arrived_at=dubai_time(2026, 9, 10, 11, 0), status=Task.Status.BLOCKED,
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        reliability = response.context['reliability']

        self.assertEqual(reliability['on_time_sample_size'], 0)
        self.assertIsNone(reliability['on_time_percent'])

    def test_brand_breakdown_groups_completed_tasks_by_brand(self):
        for i in range(3):
            self._led_task(
                f'AE-{1000 + i}', promised_at=dubai_time(2026, 9, 10, 12, 0),
                arrived_at=dubai_time(2026, 9, 10, 11, 0), brand=self.brand,
            )
        self._led_task(
            'AE-9999', promised_at=dubai_time(2026, 9, 10, 12, 0),
            arrived_at=dubai_time(2026, 9, 10, 11, 0), brand=None,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        by_brand = {row['brand']: row['completed'] for row in response.context['reliability']['by_brand']}

        self.assertEqual(by_brand[self.brand], 3)
        self.assertEqual(by_brand[None], 1)


class MySkillsTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        # The page text is checked in English.
        self.technician.language = 'en'
        self.technician.save(update_fields=['language'])

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-skills/')
        self.assertEqual(response.status_code, 302)

    def _evidence(self, name='proof.jpg', content=REAL_JPEG, content_type='image/jpeg'):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def test_self_rate_skill_creates_self_sourced_rating(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 2,
            'evidence': self._evidence(), 'note': 'Done in 8 minutes.',
        })
        self.assertEqual(response.status_code, 302)

        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.level, 2)
        self.assertEqual(rating.source, TechnicianSkill.Source.SELF)
        self.assertEqual(rating.set_by, self.technician)
        self.assertTrue(rating.evidence)
        self.assertEqual(rating.note, 'Done in 8 minutes.')
        assessment = TechnicianSkillAssessment.objects.get(
            technician=self.technician, skill=self.skill, source=TechnicianSkill.Source.SELF,
        )
        self.assertTrue(assessment.evidence)

    def test_self_rating_a_skill_without_evidence_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 2,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TechnicianSkill.objects.filter(technician=self.technician, skill=self.skill).exists())

    def test_a_self_rating_can_be_replaced_until_a_supervisor_reviews_it(self):
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=2, source=TechnicianSkill.Source.SELF,
            set_by=self.technician, set_on=timezone.now().date(),
        )
        self.client.login(username='tech1', password='pass12345')
        self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 3,
            'evidence': self._evidence('better.mp4', content=REAL_MP4, content_type='video/mp4'),
        })
        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.level, 3)
        self.assertTrue(rating.evidence.name.endswith('.mp4'))
        # Both stay in the history.
        self.assertEqual(TechnicianSkillAssessment.objects.filter(skill=self.skill).count(), 1)

    def test_a_supervisor_confirmed_level_cannot_be_changed_by_the_technician(self):
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=2, source=TechnicianSkill.Source.SUPERVISOR,
            set_by=self.supervisor_user.technician, set_on=timezone.now().date(), note='Watch the torque.',
        )
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 4, 'evidence': self._evidence(),
        }, follow=True)
        self.assertEqual(TechnicianSkill.objects.get(technician=self.technician, skill=self.skill).level, 2)
        self.assertContains(response, 'only a supervisor can change it now')
        # The supervisor's note is shown with the confirmed level.
        self.assertContains(response, 'Watch the torque.')

    def test_a_failed_submission_says_so_and_keeps_the_chosen_level(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 3, 'note': 'Took 10 minutes',
        })
        self.assertContains(response, 'Your self-rating was not saved')
        self.assertContains(response, f'id="skill-{self.skill.pk}"')
        self.assertContains(response, '<details class="rate-form" open>')
        self.assertContains(response, '<option value="3" selected>', html=False)
        self.assertContains(response, 'value="Took 10 minutes"')

    def test_the_page_explains_the_levels_and_shows_progress(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-skills/')
        self.assertContains(response, 'Has led this work alone, repeatedly, with no callbacks.')
        self.assertContains(response, 'Needed for certification')
        self.assertIn('non_cardio_total', response.context['certification'])
        self.assertEqual(response.context['waiting_count'], 0)

    def test_self_rate_conduct_area_creates_self_sourced_rating(self):
        area = ConductArea.objects.filter(is_active=True).first()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {
            'action': 'rate_conduct', 'conduct_area_id': area.pk, 'level': 3,
        })
        self.assertEqual(response.status_code, 302)

        rating = TechnicianConduct.objects.get(technician=self.technician, conduct_area=area)
        self.assertEqual(rating.level, 3)
        self.assertEqual(rating.source, TechnicianConduct.Source.SELF)
        self.assertTrue(
            TechnicianConductAssessment.objects.filter(
                technician=self.technician, conduct_area=area, source=TechnicianConduct.Source.SELF,
            ).exists(),
        )

    def test_missing_level_does_not_create_a_rating(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {'action': 'rate_skill', 'skill_id': self.skill.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TechnicianSkill.objects.filter(technician=self.technician, skill=self.skill).exists())


class MyProfileTests(TaskTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-profile/')
        self.assertEqual(response.status_code, 302)

    def test_get_shows_the_profile_and_password_forms(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('profile_form', response.context)
        self.assertIn('password_form', response.context)

    def test_shows_country_and_certification_overview(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        # tech1's language is Arabic (see TaskTestCase.setUp), so the
        # country name shows via display_name in Arabic, not English.
        self.assertContains(response, self.country.name_ar)
        self.assertFalse(response.context['certification']['is_certified'])
        # The only technician-role fixture in this country, so always #1 of 1 —
        # the leaderboard ranks every active technician, points or not.
        self.assertEqual(response.context['rank'], 1)
        self.assertEqual(response.context['leaderboard_size'], 1)

    def test_an_admin_sees_their_account_without_field_staff_sections(self):
        Technician.objects.create(
            user=User.objects.create_user('admin1', password='pass12345'), country=self.country,
            full_name='Adam Admin', language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_field_staff'])
        self.assertNotIn('certification', response.context)
        self.assertNotContains(response, 'Professional skills')
        self.assertContains(response, 'admin1')

    def test_confirmed_skill_shows_up_in_the_overview(self):
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=RELIABLE_LEVEL,
            source=TechnicianSkill.Source.SUPERVISOR, set_by=self.technician, set_on=date(2026, 1, 1),
        )
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertEqual(response.context['certification']['non_cardio_certified'], 1)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_technician_updates_own_photo_and_language(self):
        self.client.login(username='tech1', password='pass12345')
        photo = SimpleUploadedFile('me.jpg', REAL_JPEG, content_type='image/jpeg')
        response = self.client.post('/tasks/my-profile/', {
            'action': 'save_profile', 'photo': photo, 'language': Technician.Language.EN,
        })
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertTrue(self.technician.photo.name.endswith('.jpg'))
        self.assertEqual(self.technician.language, Technician.Language.EN)

    def test_rejects_a_disallowed_photo_extension(self):
        self.client.login(username='tech1', password='pass12345')
        photo = SimpleUploadedFile('me.svg', b'not a real image', content_type='image/svg+xml')
        response = self.client.post('/tasks/my-profile/', {
            'action': 'save_profile', 'photo': photo, 'language': Technician.Language.EN,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['profile_form'].errors.get('photo'))

    def test_technician_updates_own_phone_but_not_the_email_here(self):
        # The email changes only through its confirmation link (StaffAccountSettingsTests).
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-profile/', {
            'action': 'save_profile', 'language': Technician.Language.EN,
            'phone': '0501234567', 'email': 'tarek@example.com',
        })
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.tech_user.refresh_from_db()
        self.assertEqual(self.technician.phone, '0501234567')
        self.assertEqual(self.tech_user.email, '')

    def test_get_prefills_email_from_the_linked_user(self):
        self.tech_user.email = 'tarek@example.com'
        self.tech_user.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertContains(response, 'tarek@example.com')  # shown in "Sign-in email"

    def test_technician_changes_own_password(self):
        Token.objects.create(user=self.tech_user)
        PushDevice.objects.create(technician=self.technician, token='ExponentPushToken[old]', created_at=timezone.now())
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-profile/', {
            'action': 'change_password', 'old_password': 'pass12345',
            'new_password1': 'new-pass-98765', 'new_password2': 'new-pass-98765',
        })
        self.assertEqual(response.status_code, 302)

        # The session survives the password change (update_session_auth_hash) —
        # a fresh request with the same client is still authenticated.
        response = self.client.get('/tasks/my-profile/')
        self.assertEqual(response.status_code, 200)

        # Their own change: their phone stays signed in and keeps getting pushes.
        self.assertTrue(Token.objects.filter(user=self.tech_user).exists())
        self.assertTrue(PushDevice.objects.filter(technician=self.technician).exists())

        self.client.logout()
        self.assertTrue(self.client.login(username='tech1', password='new-pass-98765'))

    def test_password_form_shows_plain_rules_and_a_show_passwords_switch(self):
        self.technician.language = 'en'  # tech1's fixture language is Arabic
        self.technician.save(update_fields=['language'])
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertContains(response, 'At least 8 characters')
        self.assertContains(response, 'data-show-password')
        self.assertNotContains(response, 'can’t be too similar')

    def test_wrong_old_password_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-profile/', {
            'action': 'change_password', 'old_password': 'wrong-password',
            'new_password1': 'new-pass-98765', 'new_password2': 'new-pass-98765',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['password_form'].errors.get('old_password'))

        self.client.logout()
        self.assertTrue(self.client.login(username='tech1', password='pass12345'))


class TechnicianListTests(TaskTestCase):
    def _staff(self, username, role):
        return Technician.objects.create(
            user=User.objects.create_user(username, password='pass12345'), country=self.country,
            full_name=username.title(), language='en', role=role, employment_type='staff',
        )

    def test_each_level_sees_the_people_below_it(self):
        manager = self._staff('manager1', Technician.Role.MANAGER)
        admin = self._staff('admin1', Technician.Role.ADMIN)
        supervisor = self.supervisor_user.technician

        def listed(username):
            self.client.login(username=username, password='pass12345')
            response = self.client.get('/tasks/technicians/')
            return response, {row['technician'] for row in response.context['rows']}

        response, people = listed('supervisor1')
        self.assertEqual(people, {self.technician})
        self.assertEqual(response.context['page_title'], 'Technicians')

        response, people = listed('manager1')
        self.assertEqual(people, {self.technician, supervisor})
        self.assertEqual(response.context['page_title'], 'My team')

        response, people = listed('admin1')
        self.assertEqual(people, {self.technician, supervisor, manager, admin})
        self.assertEqual(response.context['page_title'], 'Users')

    def test_someone_outside_your_scope_cant_be_opened(self):
        self._staff('manager1', Technician.Role.MANAGER)
        supervisor = self.supervisor_user.technician
        other_supervisor = self._staff('supervisor2', Technician.Role.SUPERVISOR)
        self.client.login(username='supervisor1', password='pass12345')
        for page in ('progress', 'board', 'skills'):
            self.assertEqual(
                self.client.get(f'/tasks/technicians/{other_supervisor.pk}/{page}/').status_code, 404, page,
            )
            self.assertEqual(self.client.get(f'/tasks/technicians/{self.technician.pk}/{page}/').status_code, 200, page)
        # A manager can open a supervisor's pages, and sees them on Team progress.
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self.client.get(f'/tasks/technicians/{supervisor.pk}/progress/').status_code, 200)
        team = {row['technician'] for row in self.client.get('/tasks/technicians/progress/').context['rows']}
        self.assertIn(supervisor, team)

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/technicians/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_sees_technicians_in_their_country(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/')
        self.assertEqual(response.status_code, 200)

        technicians = [row['technician'] for row in response.context['rows']]
        self.assertIn(self.technician, technicians)

    def test_other_country_technician_not_listed(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        other_technician = Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/')

        technicians = [row['technician'] for row in response.context['rows']]
        self.assertNotIn(other_technician, technicians)

    def test_search_by_name(self):
        other_user = User.objects.create_user('other_tech', password='pass12345')
        Technician.objects.create(
            user=other_user, country=self.country, full_name='Omar Khaled',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/', {'q': 'tarek'})

        technicians = [row['technician'] for row in response.context['rows']]
        self.assertEqual(technicians, [self.technician])

    def test_search_with_no_matches(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/', {'q': 'nonexistent'})
        self.assertContains(response, 'No technicians match your search.')


class DashboardManagerMixin:
    # The dashboard is for managers and above (people migration 0050);
    # a plain manager has it without handling tickets.

    def _login_manager(self):
        user = User.objects.create_user('dash_manager', password='pass12345')
        self.manager = Technician.objects.create(
            user=user, country=self.country, full_name='Maya Manager', language='en',
            role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='dash_manager', password='pass12345')


class DashboardTests(DashboardManagerMixin, TaskTestCase):
    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_only_managers_and_above_can_view_it(self):
        # Technicians and supervisors have My week and My progress instead.
        for username in ('tech1', 'supervisor1'):
            self.client.login(username=username, password='pass12345')
            self.assertEqual(self.client.get('/tasks/dashboard/').status_code, 403, username)

    def test_shows_technician_availability(self):
        self.technician.is_available = False
        self.technician.unavailable_reason = Technician.UnavailableReason.SICK
        self.technician.save()

        self._login_manager()
        response = self.client.get('/tasks/dashboard/')

        self.assertEqual(response.status_code, 200)
        technicians = list(response.context['technicians'])
        self.assertEqual(technicians, [self.technician])
        self.assertFalse(technicians[0].is_available)

    def test_excludes_technicians_from_other_countries(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self._login_manager()
        response = self.client.get('/tasks/dashboard/')

        self.assertEqual(list(response.context['technicians']), [self.technician])

    def test_workload_splits_work_to_do_into_today_upcoming_and_overdue(self):
        now = timezone.now()
        for number, when, status in [
            ('AE-0001', now, Task.Status.ASSIGNED),                         # today
            ('AE-0002', now - timedelta(days=3), Task.Status.ACCEPTED),     # overdue
            ('AE-0003', None, Task.Status.NEW),                             # no date yet: upcoming
            ('AE-0004', now + timedelta(days=5), Task.Status.ASSIGNED),     # upcoming
            ('AE-0005', now, Task.Status.CLOSED),                           # done: not counted
        ]:
            task = self._make_task(number, scheduled_for=when, status=status)
            TaskAssignment.objects.create(
                task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                assigned_at=now, is_active=True,
            )

        self._login_manager()
        response = self.client.get('/tasks/dashboard/')

        technician = response.context['technicians'][0]
        self.assertEqual(technician.workload, {'today': 1, 'upcoming': 2, 'overdue': 1})

    def test_shows_open_tasks_with_lead_and_schedule(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 9, 9, 0), status=Task.Status.ASSIGNED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self._login_manager()
        response = self.client.get('/tasks/dashboard/')

        tasks = list(response.context['tasks'])
        self.assertEqual(tasks, [task])
        self.assertEqual(tasks[0].lead_technician, self.technician)

    def test_closed_task_excluded(self):
        self._make_task('AE-0001', status=Task.Status.CLOSED)

        self._login_manager()
        response = self.client.get('/tasks/dashboard/')

        self.assertEqual(list(response.context['tasks']), [])

    def test_other_country_task_excluded(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        self._make_task('EG-0001', site=other_site, status=Task.Status.NEW)

        self._login_manager()
        response = self.client.get('/tasks/dashboard/')

        self.assertEqual(list(response.context['tasks']), [])


class TechnicianBoardTests(TaskTestCase):
    WEEK_START = date(2026, 9, 7)

    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{self.technician.pk}/board/')
        self.assertEqual(response.status_code, 403)

    def test_other_country_technician_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        other_technician = Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{other_technician.pk}/board/')
        self.assertEqual(response.status_code, 404)

    def test_shows_a_supervisors_responsible_tasks(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        manager = Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        supervisor = Technician.objects.get(user=self.supervisor_user)
        task = self._make_task(
            'AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0), responsible_supervisor=supervisor,
        )

        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{supervisor.pk}/board/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(list(days[date(2026, 9, 7)]), [task])
        self.assertEqual(days[date(2026, 9, 7)][0].my_role, 'responsible')

    def test_shows_scheduled_task_with_role_and_estimated_finish(self):
        task = self._make_task(
            'AE-0001', scheduled_for=dubai_time(2026, 9, 7, 9, 0), estimated_hours='2.00',
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{self.technician.pk}/board/', {'start': '2026-09-07'})

        days = {day['date']: day['tasks'] for day in response.context['days']}
        self.assertEqual(list(days[date(2026, 9, 7)]), [task])
        self.assertEqual(days[date(2026, 9, 7)][0].estimated_finish, task.scheduled_for + timedelta(hours=2))

    def test_shows_unscheduled_task(self):
        task = self._make_task('AE-0001', scheduled_for=None, status=Task.Status.NEW)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{self.technician.pk}/board/')

        self.assertEqual(list(response.context['unscheduled']), [task])
        self.assertEqual(response.context['unscheduled'][0].my_role, TaskAssignment.Role.HELPER)


class MachineListTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=self.support_user, country=self.country, full_name='Dana Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/machines/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_sees_the_asset(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/machines/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.asset.model_name)

    def test_support_manager_sees_the_asset(self):
        # view_machines is granted to every office role, not just
        # whoever also has manage_tickets or view_tasks.
        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/machines/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.asset.model_name)

    def test_other_country_asset_is_excluded(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        Asset.objects.create(site=other_site, brand=self.brand, model_name='Other Country Machine')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/machines/')
        self.assertNotContains(response, 'Other Country Machine')

    def test_search_by_serial_number(self):
        self.asset.serial_no = 'SN-4242'
        self.asset.save()
        Asset.objects.create(site=self.site, brand=self.brand, model_name='Unrelated Machine', serial_no='SN-9999')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/machines/', {'q': 'SN-4242'})

        model_names = [asset.model_name for asset in response.context['page_obj']]
        self.assertEqual(model_names, [self.asset.model_name])


class MachineDetailTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, task_type=self.task_type, brand=self.brand,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.CLOSED,
        )
        self.task_asset = TaskAsset.objects.create(
            task=self.task, asset=self.asset, outcome=TaskAsset.Outcome.REPAIRED,
        )
        self.url = f'/tasks/machines/{self.asset.pk}/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_shows_task_history(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.task_number)
        self.assertContains(response, 'Repaired')

    def test_shows_the_originating_ticket_once_recorded_against_this_asset(self):
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First',
            site_description='Marina Branch', contact_name='Ali', contact_phone='0501234567',
            description='Belt squeaking.', submitted_at=timezone.now(),
            task=self.task, status=CustomerTicket.Status.CONVERTED,
        )
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertContains(response, ticket.ticket_number)

    def test_other_country_asset_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        other_asset = Asset.objects.create(site=other_site, brand=self.brand, model_name='Other Country Machine')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/machines/{other_asset.pk}/')
        self.assertEqual(response.status_code, 404)


class TechnicianSkillsTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.url = f'/tasks/technicians/{self.technician.pk}/skills/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_missing_technician_gives_404(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/999999/skills/')
        self.assertEqual(response.status_code, 404)

    def test_other_country_technician_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        other_technician = Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{other_technician.pk}/skills/')
        self.assertEqual(response.status_code, 404)

    def test_supervisor_confirms_a_skill_level(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'review_skill', 'skill_id': self.skill.pk, 'level': 4, 'note': 'Led 6 jobs alone',
        })
        self.assertEqual(response.status_code, 302)

        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.level, 4)
        self.assertEqual(rating.source, TechnicianSkill.Source.SUPERVISOR)
        self.assertEqual(rating.note, 'Led 6 jobs alone')

    def test_supervisor_overrides_a_self_rating_and_keeps_history(self):
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=2, source=TechnicianSkill.Source.SELF,
            set_by=self.technician, set_on=timezone.now().date(),
        )
        TechnicianSkillAssessment.objects.create(
            technician=self.technician, skill=self.skill, level=2, source=TechnicianSkill.Source.SELF,
            set_by=self.technician, set_on=timezone.now().date(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {
            'action': 'review_skill', 'skill_id': self.skill.pk, 'level': 3, 'note': '',
        })

        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.level, 3)
        self.assertEqual(rating.source, TechnicianSkill.Source.SUPERVISOR)
        self.assertEqual(
            TechnicianSkillAssessment.objects.filter(technician=self.technician, skill=self.skill).count(), 2,
        )

    def test_confirming_a_self_rating_keeps_its_evidence(self):
        evidence = SimpleUploadedFile('proof.jpg', REAL_JPEG, content_type='image/jpeg')
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=2, source=TechnicianSkill.Source.SELF,
            set_by=self.technician, set_on=timezone.now().date(), evidence=evidence,
        )

        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, {
            'action': 'review_skill', 'skill_id': self.skill.pk, 'level': 3, 'note': '',
        })

        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.source, TechnicianSkill.Source.SUPERVISOR)
        self.assertTrue(rating.evidence)

    def test_supervisor_confirms_a_conduct_area(self):
        area = ConductArea.objects.filter(is_active=True).first()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'review_conduct', 'conduct_area_id': area.pk, 'level': 3, 'note': '',
        })
        self.assertEqual(response.status_code, 302)

        rating = TechnicianConduct.objects.get(technician=self.technician, conduct_area=area)
        self.assertEqual(rating.level, 3)
        self.assertEqual(rating.source, TechnicianConduct.Source.SUPERVISOR)


class TechnicianCreateTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.url = '/tasks/technicians/new/'
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

    def _payload(self, **overrides):
        payload = {
            'full_name': 'Nour New', 'phone': '0501112222', 'language': Technician.Language.EN,
            'role': Technician.Role.TECHNICIAN, 'employment_type': Technician.EmploymentType.STAFF,
            'country': self.country.pk,
        }
        payload.update(overrides)
        return payload

    def test_a_new_staff_member_from_invitation_to_their_first_day(self):
        # The whole journey, as with a customer: the Admin adds them; they get an
        # email with a one-time link (no password sent); they choose a password;
        # they're in, on the website and the app, seeing only what their role
        # allows; the link then stops working; it's all in the Audit log.
        import re

        from django.contrib.admin.models import LogEntry
        from django.core import mail

        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, self._payload(
            full_name='Nour Supervisor', role=Technician.Role.SUPERVISOR, country=self.country.pk,
            email='nour@example.com',
        ))
        nour = Technician.objects.get(full_name='Nour Supervisor')
        self.assertTrue(LogEntry.objects.filter(change_message__contains='setup link emailed to nour@example.com').exists())

        invitation = mail.outbox[-1]
        self.assertEqual(invitation.to, ['nour@example.com'])
        self.assertNotIn('password:', invitation.body.lower())  # a link, never a password
        link = re.search(r'http://testserver(/tasks/setup/[^/\s]+/)', invitation.body).group(1)

        newcomer = self.client_class()
        response = newcomer.post(link, {'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        nour.refresh_from_db()
        self.assertFalse(nour.must_change_password)
        # Signed in on the website: their own pages, not an Admin's.
        self.assertEqual(newcomer.get('/').status_code, 200)
        self.assertEqual(newcomer.get('/tasks/technicians/new/').status_code, 403)

        # The link worked once.
        self.assertEqual(self.client_class().get(link).status_code, 400)

        # The same email and password in the phone app.
        app = self.client_class()
        response = app.post('/api/login/', {'username': 'nour@example.com', 'password': 'br4nd-New-Pass!'})
        self.assertEqual(response.status_code, 200)
        token = response.json()['token']
        me = app.get('/api/me/', HTTP_AUTHORIZATION=f'Token {token}').json()
        self.assertEqual(me['role'], Technician.Role.SUPERVISOR)

    def test_a_job_title_is_what_they_are_called_the_role_what_they_can_do(self):
        # A General Manager with the Operations Manager's access: listed by title everywhere,
        # the role itself where access is managed.
        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, self._payload(
            full_name='Karim Boss', role=Technician.Role.OPERATIONS_MANAGER, job_title='General Manager',
            country=self.country.pk, email='karim@example.com',
        ))
        karim = Technician.objects.get(full_name='Karim Boss')
        self.assertEqual((karim.role, karim.title), (Technician.Role.OPERATIONS_MANAGER, 'General Manager'))
        self.assertContains(self.client.get(f'/tasks/technicians/{karim.pk}/'), 'General Manager')
        panel = self.client.get(f'/tasks/technicians/accounts/?manage={karim.pk}')
        self.assertContains(panel, 'Operations Manager')  # the access role, where access is managed
        self.assertContains(panel, 'General Manager')
        # The app shows the title too.
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=karim.user)
        me = self.client_class().get('/api/me/', HTTP_AUTHORIZATION=f'Token {token.key}').json()
        self.assertEqual((me['role'], me['role_display']), ('operations_manager', 'General Manager'))
        # They see it themselves, on their profile and in the app — not the access role behind it.
        karim.user.set_password('pass12345')
        karim.user.save()
        self.client.login(username='karim@example.com', password='pass12345')
        profile = self.client.get('/tasks/my-profile/')
        self.assertContains(profile, 'Job title')
        self.assertContains(profile, 'General Manager')
        self.assertNotContains(profile, 'Operations Manager')
        # Without one, the role's name.
        self.assertEqual(Technician(role=Technician.Role.SUPERVISOR).title, 'Supervisor')

    def test_one_step_with_their_country_and_login(self):
        # The country is chosen, not whichever one the admin is viewing; the
        # email becomes their login, with a setup link — no password sent.
        from django.core import mail
        qatar = Country.objects.create(name='Qatar', name_ar='قطر', iso_code='QA', timezone='Asia/Qatar', currency_code='QAR')
        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, self._payload(
            full_name='Hany Warehouse', role=Technician.Role.WAREHOUSE_MANAGER, country=qatar.pk, email='hany@example.com',
        ))
        hany = Technician.objects.get(full_name='Hany Warehouse')
        self.assertEqual((hany.country, hany.user.username, hany.must_change_password), (qatar, 'hany@example.com', True))
        self.assertFalse(hany.user.has_usable_password())
        self.assertEqual(mail.outbox[-1].to, ['hany@example.com'])
        response = self.client.post(self.url, self._payload(full_name='Twin', email='HANY@example.com'))
        self.assertContains(response, 'An account with this email address already exists.')

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        # Creating a technician is where its role gets set — a privilege
        # grant — so it's admin-only now, not just manage_technicians
        # (which supervisors have by default). See technician_edit's own
        # role field for the matching restriction on an existing record.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Technician.objects.filter(full_name='Nour New').exists())

    def test_manager_gets_403(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Technician.objects.filter(full_name='Nour New').exists())

    def test_admin_creates_a_technician(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 302)

        technician = Technician.objects.get(full_name='Nour New')
        self.assertEqual(technician.country, self.country)
        self.assertEqual(technician.role, Technician.Role.TECHNICIAN)
        self.assertIsNone(technician.user)
        self.assertTrue(technician.is_active)

    def test_admin_can_create_any_role(self):
        for role in [
            Technician.Role.SUPERVISOR, Technician.Role.MANAGER, Technician.Role.SUPPORT_MANAGER,
            Technician.Role.WAREHOUSE_MANAGER, Technician.Role.ADMIN,
        ]:
            with self.subTest(role=role):
                self.client.login(username='admin1', password='pass12345')
                response = self.client.post(self.url, self._payload(full_name=f'New {role}', role=role))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(Technician.objects.get(full_name=f'New {role}').role, role)

    def test_missing_full_name_is_rejected(self):
        self.client.login(username='admin1', password='pass12345')
        payload = self._payload()
        del payload['full_name']
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Technician.objects.filter(full_name='').count(), 0)

    def test_the_form_starts_on_the_country_being_viewed_but_the_choice_counts(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )

        self.client.login(username='admin1', password='pass12345')
        self.client.post('/tasks/active-country/', {'country': other_country.pk})
        self.assertEqual(self.client.get(self.url).context['form']['country'].initial, other_country)

        response = self.client.post(self.url, self._payload(country=self.country.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Technician.objects.get(full_name='Nour New').country, self.country)


class TechnicianEditTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.url = f'/tasks/technicians/{self.technician.pk}/edit/'

    def _photo(self, name='photo.jpg', content=REAL_JPEG, content_type='image/jpeg'):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def _base_payload(self, **overrides):
        """The full set of fields TechnicianEditForm now covers, at the
        technician's current values — a manager-only field posted by a
        supervisor is simply ignored (not present in that form), so this
        same payload works for either role.
        """
        payload = {
            'full_name': self.technician.full_name,
            'phone': self.technician.phone,
            'language': self.technician.language,
            'role': self.technician.role,
            'employment_type': self.technician.employment_type,
            'country': self.technician.country_id,
        }
        payload.update(overrides)
        return payload

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_other_country_technician_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_user = User.objects.create_user('egypt_tech', password='pass12345')
        other_technician = Technician.objects.create(
            user=other_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{other_technician.pk}/edit/')
        self.assertEqual(response.status_code, 404)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_supervisor_uploads_a_photo(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(photo=self._photo()))
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertTrue(self.technician.photo.name.endswith('.jpg'))

    def test_rejects_a_disallowed_extension(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(
            photo=self._photo(name='photo.svg', content_type='image/svg+xml'),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('photo'))

    def test_rejects_a_photo_over_the_size_limit(self):
        self.client.login(username='supervisor1', password='pass12345')
        oversized = self._photo(content=b'x' * (5 * 1024 * 1024 + 1))
        response = self.client.post(self.url, self._base_payload(photo=oversized))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('photo'))

    def test_manager_relocates_a_technician(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(country=other_country.pk))
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertEqual(self.technician.country, other_country)

    def test_supervisor_cannot_relocate_a_technician(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(country=other_country.pk))
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertEqual(self.technician.country, self.country)

    def test_manager_cannot_change_a_technicians_role(self):
        # Role is a privilege grant — narrower than the rest of the
        # manager-only fields (country, employment type, ...), admin-only
        # instead, same reasoning as technician_create.
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(role=Technician.Role.ADMIN))
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertEqual(self.technician.role, Technician.Role.TECHNICIAN)

    def test_admin_changes_a_technicians_role(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(role=Technician.Role.SUPERVISOR))
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.assertEqual(self.technician.role, Technician.Role.SUPERVISOR)

    def test_relocated_technician_drops_off_the_old_countrys_roster(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        self.client.login(username='manager1', password='pass12345')
        self.client.post(self.url, self._base_payload(country=other_country.pk))

        response = self.client.get('/tasks/technicians/')
        technicians = [row['technician'] for row in response.context['rows']]
        self.assertNotIn(self.technician, technicians)


class TechnicianLoginTests(TaskTestCase):
    """A manager creating a login for a technician added without one —
    same UserCreationForm/SetPasswordForm pattern as a customer's own
    'Portal login' section on their edit screen (customers/views.py).
    """

    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.helper = Technician.objects.create(
            country=self.country, full_name='Hani Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.url = f'/tasks/technicians/{self.helper.pk}/edit/'

    def test_manager_can_create_a_login(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'create_login', 'email': 'hani@example.com'})
        self.assertEqual(response.status_code, 302)
        self.helper.refresh_from_db()
        self.assertIsNotNone(self.helper.user_id)
        self.assertEqual(self.helper.user.username, 'hani@example.com')
        self.assertEqual(self.helper.user.email, 'hani@example.com')
        self.assertTrue(self.helper.must_change_password)
        # No password at all until they use the setup link, and none emailed.
        self.assertFalse(self.helper.user.has_usable_password())
        self.assertIsNotNone(self.helper.setup_link_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['hani@example.com'])
        self.assertIn('/tasks/setup/', mail.outbox[0].body)
        self.assertNotIn('password:', mail.outbox[0].body.lower())

    def test_supervisor_cannot_create_a_login(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'create_login', 'email': 'hani@example.com'})
        self.assertEqual(response.status_code, 200)
        self.helper.refresh_from_db()
        self.assertIsNone(self.helper.user_id)

    def test_cannot_create_a_second_login_with_the_same_email(self):
        self.client.login(username='manager1', password='pass12345')
        self.client.post(self.url, {'action': 'create_login', 'email': 'hani@example.com'})

        other_helper = Technician.objects.create(
            country=self.country, full_name='Other Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        response = self.client.post(
            f'/tasks/technicians/{other_helper.pk}/edit/', {'action': 'create_login', 'email': 'hani@example.com'},
        )
        self.assertEqual(response.status_code, 200)
        other_helper.refresh_from_db()
        self.assertIsNone(other_helper.user_id)

    def test_nobody_types_in_someone_elses_password(self):
        # Not even the Admin: a forgotten password is fixed on Staff accounts.
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.ADMIN)
        self.client.login(username='manager1', password='pass12345')
        self.client.post(f'/tasks/technicians/{self.technician.pk}/edit/', {
            'action': 'reset_password', 'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.tech_user.refresh_from_db()
        self.assertFalse(self.tech_user.check_password('br4nd-New-Pass!'))
        page = self.client.get(f'/tasks/technicians/{self.technician.pk}/edit/')
        self.assertContains(page, f'/tasks/technicians/accounts/?manage={self.technician.pk}')

    def test_no_login_form_once_one_exists(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{self.technician.pk}/edit/')
        self.assertIsNone(response.context['login_form'])
        self.assertTrue(response.context['has_login'])

    def test_no_password_form_before_one_exists(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(self.url)
        self.assertIsNotNone(response.context['login_form'])
        self.assertFalse(response.context['has_login'])


class SkillListTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.PROJECT_MANAGER, employment_type='staff',  # every country (docs/access_rules.md)
        )

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/skills/')
        self.assertEqual(response.status_code, 403)

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/skills/')
        self.assertEqual(response.status_code, 403)

    def test_manager_sees_skills_split_by_category(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/tasks/skills/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.skill, response.context['basic_skills'])

    def test_manager_adds_a_skill(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/tasks/skills/new/', {
            'name': 'Replace seat cushion', 'name_ar': 'استبدال وسادة المقعد', 'category': 'other',
        })
        self.assertEqual(response.status_code, 302)

        skill = Skill.objects.get(name='Replace seat cushion')
        self.assertEqual(skill.name_ar, 'استبدال وسادة المقعد')
        self.assertEqual(skill.category, 'other')
        self.assertTrue(skill.is_active)

    def test_supervisor_cannot_add_a_skill(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/skills/new/', {
            'name': 'Replace seat cushion', 'name_ar': 'استبدال وسادة المقعد', 'category': 'other',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Skill.objects.filter(name='Replace seat cushion').exists())


class RolePermissionsTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        self.manager = Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        self.admin = Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 403)

    def test_manager_gets_403(self):
        """Roles & permissions is the one manager-tier screen admin
        doesn't share with manager — see require_admin."""
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_the_matrix(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 200)

    def test_disabling_view_tasks_for_supervisor_takes_effect_immediately(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/').status_code, 200)

        RolePermission.objects.filter(
            role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS,
        ).update(allowed=False)

        response = self.client.get('/tasks/')
        self.assertEqual(response.status_code, 403)

    def test_granting_view_tasks_to_technician_takes_effect_immediately(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/').status_code, 403)

        RolePermission.objects.filter(
            role=Technician.Role.TECHNICIAN, permission=RolePermission.Permission.VIEW_TASKS,
        ).update(allowed=True)

        response = self.client.get('/tasks/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_update_the_matrix(self):
        self.client.login(username='admin1', password='pass12345')

        post_data = {}
        for permission in RolePermission.Permission:
            for role in [Technician.Role.SUPERVISOR, Technician.Role.MANAGER, Technician.Role.ADMIN]:
                post_data[f'{role}__{permission}'] = 'on'

        response = self.client.post('/tasks/roles/', post_data)
        self.assertEqual(response.status_code, 302)

        self.assertFalse(
            RolePermission.objects.filter(
                role=Technician.Role.TECHNICIAN, permission=RolePermission.Permission.VIEW_TASKS, allowed=True,
            ).exists(),
        )
        self.assertTrue(
            RolePermission.objects.filter(
                role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS, allowed=True,
            ).exists(),
        )

    def test_the_roles_page_links_to_notifications(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertContains(response, 'href="/tasks/notifications/"')


class NotificationSettingsPageTests(TaskTestCase):
    # The admin's Notifications page (tasks.views.notification_settings):
    # a switch per automatic message, and who gets the staff emails.

    def setUp(self):
        super().setUp()
        Technician.objects.create(
            user=User.objects.create_user('admin1', password='pass12345', email='admin@example.com'),
            country=self.country, full_name='Adam Admin', language='en', role=Technician.Role.ADMIN,
            employment_type='staff',
        )

    def _post(self, **changes):
        data = {
            'ticket_received_email': 'on', 'reply_to_customer_email': 'on', 'auto_notify_on_reschedule': 'on',
            'new_ticket_email': 'on', 'new_ticket_roles': ['support_manager', 'admin'],
            'new_task_email': 'on', 'new_task_roles': ['manager', 'admin'],
            'reply_to_staff_email': 'on', 'escalation_emails': 'on', 'shipment_to_supervisor_email': 'on',
            'push_assigned': 'on', 'push_removed': 'on', 'push_cancelled': 'on',
        }
        data.update(changes)
        return self.client.post('/tasks/notifications/', {key: value for key, value in data.items() if value})

    def test_only_an_admin_opens_it(self):
        for username in ('tech1', 'supervisor1'):
            self.client.login(username=username, password='pass12345')
            self.assertEqual(self.client.get('/tasks/notifications/').status_code, 403, username)
        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Always sent, for account security')
        self.assertIn('/tasks/notifications/', [page['url'] for page in response.context['nav_pages']])

    def test_everything_is_on_by_default_including_reschedule(self):
        settings = NotificationSettings.load()
        self.assertTrue(settings.auto_notify_on_reschedule)
        self.assertTrue(settings.ticket_received_email and settings.push_assigned)
        self.assertEqual(settings.new_task_roles, ['manager', 'admin'])

    def test_saving_switches_and_recipients(self):
        self.client.login(username='admin1', password='pass12345')
        response = self._post(ticket_received_email='', new_task_roles=['supervisor'])
        self.assertRedirects(response, '/tasks/notifications/')
        settings = NotificationSettings.load()
        self.assertFalse(settings.ticket_received_email)
        self.assertEqual(settings.new_task_roles, ['supervisor'])

    def test_an_email_that_is_on_needs_someone_to_receive_it(self):
        self.client.login(username='admin1', password='pass12345')
        response = self._post(new_ticket_roles=[])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors['new_ticket_roles'])
        # Off, it may have no one.
        self.assertEqual(self._post(new_ticket_email='', new_ticket_roles=[]).status_code, 302)

    def test_a_switched_off_message_is_not_sent(self):
        from tasks.views import send_ticket_confirmation_to_customer
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0901', company_name='Fitness First', site_description='Marina',
            contact_name='Ali', contact_phone='0501234567', contact_email='ali@example.com',
            description='Squeak', submitted_at=timezone.now(),
        )
        NotificationSettings.objects.filter(pk=NotificationSettings.load().pk).update(ticket_received_email=False)
        send_ticket_confirmation_to_customer(None, ticket)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_new_task_email_goes_to_the_chosen_roles(self):
        from tasks.views import _send_new_task_email_to_staff
        self.supervisor_user.email = 'sup@example.com'
        self.supervisor_user.save(update_fields=['email'])
        settings = NotificationSettings.load()
        settings.new_task_roles = ['supervisor']
        settings.save()
        task = Task.objects.create(
            task_number='AE-0950', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        request = self.client.request().wsgi_request
        _send_new_task_email_to_staff(request, task)
        self.assertEqual([message.to for message in mail.outbox], [['sup@example.com']])

    def test_a_switched_off_phone_notification_is_not_sent(self):
        from people.push import push_assigned
        task = Task.objects.create(
            task_number='AE-0951', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        NotificationSettings.objects.filter(pk=NotificationSettings.load().pk).update(push_assigned=False)
        with patch('people.push.send_push') as send_push:
            push_assigned(self.technician, task, is_lead=True)
        send_push.assert_not_called()


class MonthlyReportTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.PROJECT_MANAGER, employment_type='staff',  # every country (docs/access_rules.md)
        )
        self.this_month = timezone.now().replace(day=15)
        self.last_month = (self.this_month.replace(day=1) - timedelta(days=1)).replace(day=15)

        self.other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        self.other_customer = Customer.objects.create(country=self.other_country, name='Cairo Gym', segment='gym')
        self.other_site = Site.objects.create(customer=self.other_customer, name='Zamalek Branch', address='Cairo')

        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali', contact_phone='0501234567', description='Broken belt',
            submitted_at=self.this_month, status=CustomerTicket.Status.NEW,
        )
        CustomerTicket.objects.create(
            country=self.other_country, ticket_number='EG-T0001', company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Sara', contact_phone='0501234568', description='Squeaky wheel',
            submitted_at=self.this_month, status=CustomerTicket.Status.CONVERTED,
        )
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0002', company_name='Old Ticket', site_description='Marina Branch',
            contact_name='Ali', contact_phone='0501234567', description='Old issue',
            submitted_at=self.last_month, status=CustomerTicket.Status.CLOSED,
        )

        Task.objects.create(
            task_number='UAE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=self.this_month, created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        Task.objects.create(
            task_number='EG-0001', site=self.other_site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=self.this_month, created_by=self.supervisor_user, status=Task.Status.CLOSED,
        )
        Task.objects.create(
            task_number='UAE-0002', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=self.last_month, created_by=self.supervisor_user, status=Task.Status.NEW,
        )

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/reports/monthly/')
        self.assertEqual(response.status_code, 403)

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/reports/monthly/')
        self.assertEqual(response.status_code, 403)

    def test_manager_sees_every_country_combined_for_the_month(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/reports/monthly/?month={self.this_month:%Y-%m}')
        self.assertEqual(response.status_code, 200)
        # The headline figures: this month's two requests and two tasks, last month's left out.
        headlines = {card['label']: card['value'] for card in response.context['headlines']}
        self.assertEqual(headlines['Requests received'], '2')
        self.assertEqual(headlines['Tasks opened'], '2')
        # Every country, side by side; one of the two tasks is closed.
        rows = {row['country']: row for row in response.context['by_country']}
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row['completion'] or 0 for row in rows.values()), 100)
        # A management summary: no list of every request and task (that's the CSV).
        self.assertNotContains(response, 'UAE-0001')

    def test_export_is_a_csv_download(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/reports/monthly/export/?month={self.this_month:%Y-%m}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        content = response.content.decode()
        self.assertIn('Fitness First', content)
        self.assertIn('EG-0001', content)
        self.assertNotIn('UAE-0002', content)


class ActiveCountryTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        self.manager = Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.PROJECT_MANAGER, employment_type='staff',  # every country (docs/access_rules.md)
        )
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.egypt = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        self.egypt_customer = Customer.objects.create(country=self.egypt, name='Cairo Gym', segment='gym')

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/active-country/', {'country': self.egypt.pk})
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/active-country/', {'country': self.egypt.pk})
        self.assertEqual(response.status_code, 403)

    def test_manager_defaults_to_own_country(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/customers/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fitness First')
        self.assertNotContains(response, 'Cairo Gym')

    def test_manager_switches_and_sees_the_new_country(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(
            '/tasks/active-country/', {'country': self.egypt.pk, 'next': '/customers/'},
        )
        self.assertRedirects(response, '/customers/')

        response = self.client.get('/customers/')
        self.assertContains(response, 'Cairo Gym')
        self.assertNotContains(response, 'Fitness First')

    def test_switch_persists_across_requests(self):
        self.client.login(username='manager1', password='pass12345')
        self.client.post('/tasks/active-country/', {'country': self.egypt.pk})

        response = self.client.get('/tasks/technicians/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['rows'], [])

    def test_invalid_country_gives_404(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/tasks/active-country/', {'country': 999999})
        self.assertEqual(response.status_code, 404)

    def test_unsafe_next_falls_back_to_dashboard(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(
            '/tasks/active-country/', {'country': self.egypt.pk, 'next': 'https://evil.example/'},
        )
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_new_customer_from_a_ticket_is_created_in_the_active_country(self):
        # From New ticket the country isn't asked: it's the ticket's, the active one.
        self.client.login(username='admin1', password='pass12345')
        self.client.post('/tasks/active-country/', {'country': self.egypt.pk})

        response = self.client.post('/customers/new/?next=ticket', {
            'name': 'Nile Gym', 'segment': 'gym', 'branch_name': 'Zamalek', 'branch_address': 'Zamalek, Cairo',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.get(name='Nile Gym').country, self.egypt)


class HomeRedirectTests(TaskTestCase):
    # Every staff login lands on the welcome page: one icon tile per page
    # that person can open, from the same list as the header menu
    # (people.context_processors.nav_pages) — see spots.views.home.

    def _make_staff(self, username, role):
        user = User.objects.create_user(username, password='pass12345')
        Technician.objects.create(
            user=user, country=self.country, full_name=username.title(),
            language='en', role=role, employment_type='staff',
        )
        self.client.login(username=username, password='pass12345')

    def _tile_urls(self, response):
        self.assertTemplateUsed(response, 'welcome.html')
        return [page['url'] for page in response.context['nav_pages']]

    def test_technician_sees_only_their_own_pages(self):
        self.client.login(username='tech1', password='pass12345')
        urls = self._tile_urls(self.client.get('/'))
        self.assertEqual(urls[:3], ['/tasks/my-week/', '/tasks/my-progress/', '/tasks/my-skills/'])
        for url in ('/tasks/dashboard/', '/tasks/countries/', '/tasks/roles/', '/tasks/audit-log/'):
            self.assertNotIn(url, urls)

    def test_supervisor_sees_tasks_but_not_manager_pages(self):
        self.client.login(username='supervisor1', password='pass12345')
        urls = self._tile_urls(self.client.get('/'))
        self.assertIn('/tasks/', urls)
        self.assertNotIn('/tasks/countries/', urls)
        self.assertNotIn('/tasks/roles/', urls)

    def test_manager_sees_manager_pages_but_not_admin_ones(self):
        self._make_staff('manager1', Technician.Role.MANAGER)
        urls = self._tile_urls(self.client.get('/'))
        # Company-wide pages are the every-country roles' (docs/access_rules.md); a Manager has his country.
        self.assertNotIn('/tasks/countries/', urls)
        self.assertIn('/tasks/technicians/accounts/', urls)
        # Their own schedule and the team's progress; not the field team's own progress page.
        self.assertIn('/tasks/my-week/', urls)
        self.assertIn('/tasks/technicians/progress/', urls)
        self.assertNotIn('/tasks/my-progress/', urls)
        self.assertNotIn('/tasks/roles/', urls)

    def test_admin_sees_roles_and_audit_log(self):
        self._make_staff('admin1', Technician.Role.ADMIN)
        urls = self._tile_urls(self.client.get('/'))
        self.assertIn('/tasks/roles/', urls)
        self.assertIn('/tasks/audit-log/', urls)

    def test_every_tile_opens_for_that_role(self):
        # The tiles promise access — each one must actually load, not 403.
        for username, role in [
            ('tech2', Technician.Role.TECHNICIAN), ('super2', Technician.Role.SUPERVISOR),
            ('support1', Technician.Role.SUPPORT_MANAGER), ('warehouse1', Technician.Role.WAREHOUSE_MANAGER),
            ('ops1', Technician.Role.OPERATIONS_MANAGER), ('manager2', Technician.Role.MANAGER),
            ('admin2', Technician.Role.ADMIN),
        ]:
            self._make_staff(username, role)
            for url in self._tile_urls(self.client.get('/')) + ['/tasks/my-profile/']:
                with self.subTest(role=role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_tiles_follow_the_roles_and_permissions_screen(self):
        RolePermission.objects.filter(
            role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS,
        ).update(allowed=False)
        self.client.login(username='supervisor1', password='pass12345')
        self.assertNotIn('/tasks/', self._tile_urls(self.client.get('/')))

    def test_customer_lands_on_portal_home(self):
        portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user)
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/customers/portal/')

    def test_technician_on_a_temporary_password_lands_on_first_login(self):
        self.technician.must_change_password = True
        self.technician.save(update_fields=['must_change_password'])
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/first-login/')

    def test_customer_on_a_temporary_password_lands_on_portal_first_login(self):
        portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user, must_change_password=True)
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/customers/portal/first-login/')


class TechnicianFirstLoginTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.technician.must_change_password = True
        self.technician.save(update_fields=['must_change_password'])
        self.client.login(username='tech1', password='pass12345')

    def test_shown_while_must_change_password_is_set(self):
        response = self.client.get('/tasks/first-login/')
        self.assertEqual(response.status_code, 200)

    def test_redirects_to_dashboard_once_cleared(self):
        self.technician.must_change_password = False
        self.technician.save(update_fields=['must_change_password'])
        response = self.client.get('/tasks/first-login/')
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_submitting_sets_the_password_and_clears_the_flag(self):
        response = self.client.post('/tasks/first-login/', {
            'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertRedirects(response, '/', fetch_redirect_response=False)

        self.technician.refresh_from_db()
        self.assertFalse(self.technician.must_change_password)
        self.technician.user.refresh_from_db()
        self.assertTrue(self.technician.user.check_password('br4nd-New-Pass!'))


class SetLanguageTests(TaskTestCase):
    """spots.views.set_language replaces Django's own — for a signed-in
    technician or customer it must persist to their saved language field
    (spots.middleware.TechnicianLocaleMiddleware re-activates that on
    every request, so a session-only change would silently revert on the
    very next page load); an anonymous visitor still just gets the
    ordinary session/cookie switch.
    """
    def test_anonymous_visitor_gets_the_session_cookie_switch(self):
        response = self.client.post('/i18n/setlang/', {'language': 'ar', 'next': '/accounts/login/'})
        self.assertRedirects(response, '/accounts/login/', fetch_redirect_response=False)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'ar')

    def test_technician_switch_persists_to_their_own_language_field(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/i18n/setlang/', {'language': 'ar', 'next': '/tasks/my-week/'})
        self.assertRedirects(response, '/tasks/my-week/', fetch_redirect_response=False)

        self.technician.refresh_from_db()
        self.assertEqual(self.technician.language, 'ar')

    def test_customer_switch_persists_to_their_own_language_field(self):
        portal_user = User.objects.create_user('fitnessfirst_portal', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user)

        self.client.login(username='fitnessfirst_portal', password='pass12345')
        response = self.client.post('/i18n/setlang/', {'language': 'ar', 'next': '/customers/portal/'})
        self.assertRedirects(response, '/customers/portal/', fetch_redirect_response=False)

        # Their own, not their whole company's.
        portal_user.portal_user.refresh_from_db()
        self.assertEqual(portal_user.portal_user.language, 'ar')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.language, 'en')

    def test_unsafe_next_url_falls_back_to_root(self):
        response = self.client.post('/i18n/setlang/', {'language': 'ar', 'next': 'https://evil.example/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_invalid_language_code_is_ignored(self):
        self.technician.language = 'en'
        self.technician.save(update_fields=['language'])

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/i18n/setlang/', {'language': 'fr', 'next': '/tasks/my-week/'})
        self.assertRedirects(response, '/tasks/my-week/', fetch_redirect_response=False)

        self.technician.refresh_from_db()
        self.assertEqual(self.technician.language, 'en')


class MyTaskDetailTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
        )
        self.lead_assignment = TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.helper_user = User.objects.create_user('helper1', password='pass12345')
        self.helper = Technician.objects.create(
            user=self.helper_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        self.url = f'/tasks/my/{self.task.pk}/'

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_unassigned_technician_gets_404(self):
        other_user = User.objects.create_user('other1', password='pass12345')
        Technician.objects.create(
            user=other_user, country=self.country, full_name='Nour Other',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.client.login(username='other1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_lead_sees_accept_as_next_action(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.context['next_action'], 'accept')

    def test_helper_has_no_next_action(self):
        self.client.login(username='helper1', password='pass12345')
        response = self.client.get(self.url)
        self.assertIsNone(response.context['next_action'])

    def test_lead_sees_their_teammate_and_who_created_it(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.context['created_by_name'], 'Sara Super')
        teammates = list(response.context['teammates'])
        self.assertEqual(len(teammates), 1)
        self.assertEqual(teammates[0].technician, self.helper)

    def test_helper_sees_the_lead_as_their_teammate(self):
        self.client.login(username='helper1', password='pass12345')
        response = self.client.get(self.url)
        teammates = list(response.context['teammates'])
        self.assertEqual(len(teammates), 1)
        self.assertEqual(teammates[0].technician, self.technician)

    def test_created_by_shows_the_manager_when_a_manager_made_it(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task.created_by = manager_user
        self.task.save(update_fields=['created_by'])

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.context['created_by_name'], 'Mona Manager')

    def test_accept_advances_status_and_logs_event(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, {'action': 'accept'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)
        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.ACCEPTED)
        self.assertEqual(event.actor, self.tech_user)

    def test_helper_cannot_advance_status(self):
        self.client.login(username='helper1', password='pass12345')
        response = self.client.post(self.url, {'action': 'accept'})
        self.assertEqual(response.status_code, 200)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)
        self.assertEqual(self.task.events.count(), 0)

    def test_en_route_then_arrive_then_start_sequence(self):
        self.client.login(username='tech1', password='pass12345')
        self.client.post(self.url, {'action': 'accept'})

        response = self.client.get(self.url)
        self.assertEqual(response.context['next_action'], 'en_route')

        self.client.post(self.url, {'action': 'en_route'})
        response = self.client.get(self.url)
        self.assertEqual(response.context['next_action'], 'arrive')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)

        self.client.post(self.url, {'action': 'arrive'})
        response = self.client.get(self.url)
        self.assertEqual(response.context['next_action'], 'start')

        self.client.post(self.url, {'action': 'start'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

    def test_block_requires_a_note(self):
        self.task.status = Task.Status.ACCEPTED
        self.task.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, {'action': 'block', 'note': ''})
        self.assertEqual(response.status_code, 200)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)

    def test_block_sets_status_and_event_note(self):
        self.task.status = Task.Status.ACCEPTED
        self.task.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, {'action': 'block', 'note': 'Gym closed.'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.BLOCKED)
        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.BLOCKED)
        self.assertEqual(event.note, 'Gym closed.')

    def test_cannot_block_before_accepted(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_block'])

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_upload_creates_photo_attachment(self):
        self.client.login(username='tech1', password='pass12345')
        upload = SimpleUploadedFile('serial.jpg', REAL_JPEG, content_type='image/jpeg')

        # A serial plate photo goes with its machine.
        response = self.client.post(self.url, {
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.SERIAL_PLATE, 'asset': self.asset.pk,
        })
        self.assertEqual(response.status_code, 302)

        attachment = self.task.attachments.get()
        self.assertEqual(attachment.asset, self.asset)
        self.assertEqual(attachment.media_type, TaskAttachment.MediaType.PHOTO)
        self.assertEqual(attachment.purpose, TaskAttachment.Purpose.SERIAL_PLATE)
        self.assertEqual(attachment.storage_kind, TaskAttachment.StorageKind.FILE)
        self.assertEqual(attachment.uploaded_by, self.tech_user)
        self.assertTrue(attachment.url.startswith('http'))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_helper_can_upload_a_photo(self):
        self.client.login(username='helper1', password='pass12345')
        upload = SimpleUploadedFile('before.jpg', REAL_JPEG, content_type='image/jpeg')

        response = self.client.post(self.url, {
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.BEFORE,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.task.attachments.count(), 1)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_html_upload_disguised_as_image_content_type_is_rejected(self):
        # Attachments are linked back as raw same-origin files (see
        # task_detail.html), so accepting this would let a stored file
        # execute as script in the app's own origin — the extension is
        # what the server trusts when serving it back, not the client's
        # claimed content_type, so that's what must be checked here too.
        self.client.login(username='tech1', password='pass12345')
        upload = SimpleUploadedFile(
            'note.html', b'<script>alert(1)</script>', content_type='image/jpeg',
        )

        response = self.client.post(self.url, {
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.BEFORE,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.task.attachments.count(), 0)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_oversized_upload_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        upload = SimpleUploadedFile(
            'huge.jpg', b'x' * (25 * 1024 * 1024 + 1), content_type='image/jpeg',
        )

        response = self.client.post(self.url, {
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.BEFORE,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.task.attachments.count(), 0)


    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_each_machine_card_has_serial_before_after_and_the_same_cards_show_when_approving(self):
        from tasks.models import TaskAsset
        from tasks.views import visit_photo_cards
        TaskAsset.objects.create(task=self.task, asset=self.asset)
        self.client.login(username='tech1', password='pass12345')
        photo = lambda: SimpleUploadedFile('p.jpg', REAL_JPEG, content_type='image/jpeg')
        # A serial plate photo goes with its machine, never the branch.
        response = self.client.post(self.url, {
            'action': 'upload', 'file': photo(), 'purpose': TaskAttachment.Purpose.SERIAL_PLATE, 'asset': '',
        })
        self.assertIn('asset', response.context['upload_form'].errors)
        self.assertEqual(self.task.attachments.count(), 0)
        page = self.client.get(self.url)
        self.assertContains(page, 'class="branch-card"')
        self.assertContains(page, 'data-photo-slot-input', count=3)  # + Serial plate, + Before, + After
        for purpose in (TaskAttachment.Purpose.SERIAL_PLATE, TaskAttachment.Purpose.BEFORE):
            self.client.post(self.url, {'action': 'upload', 'file': photo(), 'purpose': purpose, 'asset': self.asset.pk})
        (card,) = visit_photo_cards(self.task, self.task.attachments.all())['cards']
        self.assertEqual([len(column['photos']) for column in card['columns']], [1, 1, 0])
        # The app gets the same card.
        from api.serializers import TaskDetailSerializer
        (app_card,) = TaskDetailSerializer(self.task).data['machine_cards']
        self.assertEqual(([photo['purpose'] for photo in app_card['photos']], app_card['asset_id']),
                         (['serial_plate', 'before'], self.asset.pk))
        # Approving (the staff page): the same card, read-only — the missing After said.
        self.client.login(username='supervisor1', password='pass12345')
        page = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(page, 'No photo after')
        self.assertNotContains(page, 'data-photo-slot')


class MyReportFormTests(TaskTestCase):
    EXISTING_ASSET_ROWS = 4
    NEW_ASSET_ROWS = 4
    PART_ROWS = 5

    def setUp(self):
        super().setUp()
        # About the report's other rules: the machines' photos have their own tests
        # (test_a_report_needs_every_machines_three_photos, api CustomerPhotosOnTheMachineCardTests).
        self._photos = patch('tasks.views.report_machine_photo_gaps', return_value=[])
        self._photos.start()
        self.addCleanup(self._photos.stop)
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.helper_user = User.objects.create_user('helper1', password='pass12345')
        self.helper = Technician.objects.create(
            user=self.helper_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        self.url = f'/tasks/my/{self.task.pk}/report/'

    def _management_form(self, prefix, total, initial=0):
        return {
            f'{prefix}-TOTAL_FORMS': str(total),
            f'{prefix}-INITIAL_FORMS': str(initial),
            f'{prefix}-MIN_NUM_FORMS': '0',
            f'{prefix}-MAX_NUM_FORMS': '1000',
        }

    def _base_payload(self, **report_overrides):
        payload = {
            'findings': 'Belt worn out', 'action_taken': 'Replaced belt', 'resolved': 'True',
            'labour_hours': '1.50', 'customer_name': 'Ali Manager',
        }
        payload.update(report_overrides)
        payload.update(self._management_form('existing', total=self.EXISTING_ASSET_ROWS))
        payload.update(self._management_form('new', total=self.NEW_ASSET_ROWS))
        payload.update(self._management_form('parts', total=self.PART_ROWS))
        return payload

    def test_first_submission_emails_the_customer_a_pdf_copy(self):
        self.client.login(username='tech1', password='pass12345')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self._base_payload(send_copy_to='ali@example.com'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['ali@example.com'])
        self.assertIn('AE-0001', email.subject)
        [(filename, content, mimetype)] = [a for a in email.attachments if isinstance(a, tuple)]
        self.assertEqual((filename, mimetype), ('AE-0001-visit-report.pdf', 'application/pdf'))
        self.assertTrue(content.startswith(b'%PDF'))
        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(report.copy_sent_to, 'ali@example.com')
        self.assertIsNotNone(report.copy_sent_at)

    def test_the_report_email_asks_for_the_rating(self):
        self.client.login(username='tech1', password='pass12345')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self._base_payload(send_copy_to='ali@example.com'))
        (email,) = mail.outbox
        feedback = CustomerFeedback.objects.get(task=self.task)
        self.assertIn(f'/reports/feedback/{feedback.token}/', email.body)
        self.assertIn('Rate our visit', email.alternatives[0][0])
        self.assertIn('How was our visit?', email.body)

    def test_no_rating_counts_as_asked_when_the_email_fails(self):
        self.client.login(username='tech1', password='pass12345')
        with patch('tasks.views._send_notification_email', return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.url, self._base_payload(send_copy_to='ali@example.com'))
        self.assertFalse(CustomerFeedback.objects.filter(task=self.task).exists())

    def _send_copy_capturing_language(self):
        with patch('reports.pdf.render_visit_report_pdf', return_value=b'%PDF-test') as render:
            self.client.login(username='tech1', password='pass12345')
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.url, self._base_payload(send_copy_to='ali@example.com'))
        return render.call_args.args[1], mail.outbox[0]

    def test_in_saudi_arabia_and_egypt_the_report_and_its_email_are_arabic(self):
        self.country.report_language = 'ar'
        self.country.save(update_fields=['report_language'])
        self.customer.language = 'en'  # their own choice doesn't change it
        self.customer.save(update_fields=['language'])
        language, email = self._send_copy_capturing_language()
        self.assertEqual(language, 'ar')
        # The PDF in Arabic; its email, like every customer email, in English and Arabic (tasks.customer_emails).
        self.assertIn('تقرير الزيارة', email.subject)
        html = email.alternatives[0][0]
        self.assertIn('dir="rtl"', html)
        self.assertIn('Profit Sports Solutions', html)  # the company name stays in English

    def test_elsewhere_the_report_is_always_english(self):
        self.customer.language = 'ar'  # even a customer who picked Arabic
        self.customer.save(update_fields=['language'])
        language, email = self._send_copy_capturing_language()
        self.assertEqual(language, 'en')
        self.assertIn('Visit report', email.subject)
        self.assertIn('dir="ltr"', email.alternatives[0][0])

    def test_a_correction_does_not_send_another_copy(self):
        self.client.login(username='tech1', password='pass12345')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self._base_payload(send_copy_to='ali@example.com'))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self._base_payload(send_copy_to='ali@example.com', labour_hours='2.00'))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_blank_address_sends_nothing(self):
        self.client.login(username='tech1', password='pass12345')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self._base_payload(send_copy_to=''))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(WorkReport.objects.get(task=self.task).copy_sent_to, '')

    def test_the_address_starts_as_the_site_contact(self):
        self.site.contact_email = 'marina@example.com'
        self.site.save(update_fields=['contact_email'])
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.context['report_form']['send_copy_to'].value(), 'marina@example.com')

    SIGNATURE = (
        'data:image/png;base64,'
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
    )

    def _file(self, **overrides):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, self._base_payload(**overrides))

    def test_what_the_customer_signed_is_frozen(self):
        self.client.login(username='tech1', password='pass12345')
        self._file(signature_drawn=self.SIGNATURE)
        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(report.signed_content['findings'], 'Belt worn out')
        self.assertTrue(report.signed_content['signature_url'].endswith('.png'))
        self.assertIsNotNone(report.signed_at)

    def test_an_edit_after_signing_is_flagged_and_the_signed_version_stays_downloadable(self):
        from reports.pdf import changed_after_signing
        self.client.login(username='tech1', password='pass12345')
        self._file(signature_drawn=self.SIGNATURE)
        self._file(findings='Belt worn out and motor noisy')
        report = WorkReport.objects.get(task=self.task)
        self.assertTrue(changed_after_signing(report))
        self.assertEqual(report.signed_content['findings'], 'Belt worn out')

        self.client.login(username='supervisor1', password='pass12345')
        page = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertTrue(page.context['report_changed_after_signing'])
        signed = self.client.get(f'/tasks/{self.task.pk}/report.pdf', {'version': 'signed', 'lang': 'en'})
        self.assertIn('-signed.pdf', signed['Content-Disposition'])
        self.assertTrue(signed.content.startswith(b'%PDF'))

    def test_saving_the_same_content_again_is_not_a_change(self):
        from reports.pdf import changed_after_signing
        self.client.login(username='tech1', password='pass12345')
        self._file(signature_drawn=self.SIGNATURE)
        self._file()
        self.assertFalse(changed_after_signing(WorkReport.objects.get(task=self.task)))

    def test_signing_again_clears_the_flag(self):
        from reports.pdf import changed_after_signing
        self.client.login(username='tech1', password='pass12345')
        self._file(signature_drawn=self.SIGNATURE)
        self._file(findings='Belt worn out and motor noisy', signature_drawn=self.SIGNATURE)
        report = WorkReport.objects.get(task=self.task)
        self.assertFalse(changed_after_signing(report))
        self.assertEqual(report.signed_content['findings'], 'Belt worn out and motor noisy')

    def test_an_unsigned_report_is_never_flagged(self):
        from reports.pdf import changed_after_signing
        self.client.login(username='tech1', password='pass12345')
        self._file()
        self._file(findings='Something else')
        report = WorkReport.objects.get(task=self.task)
        self.assertIsNone(report.signed_content)
        self.assertFalse(changed_after_signing(report))

    def test_helper_gets_404(self):
        self.client.login(username='helper1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_task_type_requiring_signature_blocks_a_report_without_one(self):
        self.task_type.requires_signature = True
        self.task_type.save(update_fields=['requires_signature'])
        self.task.task_type = self.task_type
        self.task.save(update_fields=['task_type'])

        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, self._base_payload())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

        signature = SimpleUploadedFile('sig.png', REAL_PNG, content_type='image/png')
        response = self.client.post(self.url, {**self._base_payload(), 'signature': signature})
        self.assertEqual(response.status_code, 302)

        # A later correction without a new upload keeps the one on file.
        response = self.client.post(self.url, self._base_payload(labour_hours='2.00'))
        self.assertEqual(response.status_code, 302)

    def test_signature_drawn_on_the_pad_is_saved_as_an_image(self):
        self.client.login(username='tech1', password='pass12345')
        drawn = (
            'data:image/png;base64,'
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
        )
        response = self.client.post(self.url, self._base_payload(signature_drawn=drawn))
        self.assertEqual(response.status_code, 302)
        report = WorkReport.objects.get(task=self.task)
        self.assertTrue(report.signature_url.endswith('.png'))

    def test_garbled_drawn_signature_is_a_form_error(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, self._base_payload(signature_drawn='data:image/png;base64,@@@'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_status_not_editable_redirects_with_message(self):
        self.task.status = Task.Status.ASSIGNED
        self.task.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertRedirects(response, f'/tasks/my/{self.task.pk}/')

    def test_minimal_submission_creates_report_and_event(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, self._base_payload())
        self.assertEqual(response.status_code, 302)

        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(report.findings, 'Belt worn out')
        self.assertTrue(report.resolved)

        event_types = set(self.task.events.values_list('event_type', flat=True))
        self.assertEqual(event_types, {TaskEvent.EventType.REPORT_SUBMITTED, TaskEvent.EventType.COMPLETED})
        submitted_event = self.task.events.get(event_type=TaskEvent.EventType.REPORT_SUBMITTED)
        self.assertEqual(submitted_event.actor, self.tech_user)

    def test_technicians_report_awaits_supervisor_review_not_completed_directly(self):
        # tech1 is a plain technician — their own report needs their
        # supervisor's sign-off before it counts as completed.
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post(self.url, self._base_payload())
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.PENDING_SUPERVISOR_REVIEW)

    def test_resubmitting_the_report_stays_pending_and_does_not_relog_completed_event(self):
        self.client.login(username='tech1', password='pass12345')
        self.client.post(self.url, self._base_payload())
        self.client.post(self.url, self._base_payload(findings='Belt worn out, fixed again'))

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.PENDING_SUPERVISOR_REVIEW)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.COMPLETED).count(), 1)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.REPORT_SUBMITTED).count(), 2)

    def test_lead_cannot_change_the_report_once_closed(self):
        self.client.login(username='tech1', password='pass12345')
        self.client.post(self.url, self._base_payload())
        self.task.refresh_from_db()
        self.task.status = Task.Status.CLOSED
        self.task.save(update_fields=['status'])

        response = self.client.post(self.url, self._base_payload(labour_hours='9.00'))
        self.assertRedirects(response, f'/tasks/my/{self.task.pk}/')
        self.assertEqual(str(WorkReport.objects.get(task=self.task).labour_hours), '0.00')  # no Start tap here

    def test_manager_corrects_a_closed_report_and_it_stays_closed(self):
        self.client.login(username='tech1', password='pass12345')
        self.client.post(self.url, self._base_payload())
        self.task.refresh_from_db()
        self.task.status = Task.Status.CLOSED
        self.task.save(update_fields=['status'])

        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        correct_url = f'/tasks/{self.task.pk}/report/correct/'
        response = self.client.post(correct_url, self._base_payload(labour_hours='2.25'))
        self.assertRedirects(response, f'/tasks/{self.task.pk}/', fetch_redirect_response=False)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.COMPLETED).count(), 1)

    def test_hours_and_prices_are_marked_staff_only_for_the_signing_view(self):
        self.client.login(username='tech1', password='pass12345')
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-report-form', html)
        # The hours aren't typed any more — shown, worked out from the taps.
        self.assertNotIn('name="labour_hours"', html)
        self.assertIn('data-signing-only', html)
        self.assertIn('data-signoff-start', html)

    def test_supervisor_cannot_use_the_manager_correction(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/report/correct/').status_code, 403)

    def test_resolved_is_required(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        del payload['resolved']
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_existing_asset_row_creates_task_asset_without_new_asset(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['existing-0-asset'] = str(self.asset.pk)
        payload['existing-0-outcome'] = TaskAsset.Outcome.REPAIRED
        self.client.post(self.url, payload)

        task_asset = TaskAsset.objects.get(task=self.task)
        self.assertEqual(task_asset.asset, self.asset)
        self.assertEqual(task_asset.outcome, TaskAsset.Outcome.REPAIRED)
        self.assertEqual(Asset.objects.count(), 1)

    def test_a_report_needs_every_machines_three_photos(self):
        from api.tests import give_machine_photos
        self._photos.stop()  # the real rule
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['existing-0-asset'] = str(self.asset.pk)
        payload['existing-0-outcome'] = TaskAsset.Outcome.REPAIRED
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        (gap,) = response.context['report_form'].non_field_errors()  # in the technician's Arabic
        self.assertIn(self.asset.model_name, gap)
        self.assertFalse(TaskAsset.objects.filter(task=self.task).exists())
        give_machine_photos(self.task, self.asset, self.tech_user)
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertTrue(TaskAsset.objects.filter(task=self.task).exists())
        self._photos.start()  # as the cleanup expects

    def test_recording_a_machine_the_customer_added_confirms_it(self):
        self.asset.status = Asset.Status.PENDING
        self.asset.save(update_fields=['status'])
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['existing-0-asset'] = str(self.asset.pk)
        payload['existing-0-outcome'] = TaskAsset.Outcome.INSPECTED_OK
        self.client.post(self.url, payload)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.ACTIVE)

    def test_incomplete_existing_asset_row_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['existing-0-asset'] = str(self.asset.pk)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_existing_asset_dropdown_is_scoped_to_the_task_site(self):
        other_site = Site.objects.create(customer=self.customer, name='Other Branch', address='Elsewhere')
        other_asset = Asset.objects.create(site=other_site, brand=self.brand, model_name='Other Machine')

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)

        asset_field = response.context['existing_formset'].forms[0].fields['asset']
        self.assertIn(self.asset, asset_field.queryset)
        self.assertNotIn(other_asset, asset_field.queryset)

    def test_new_asset_row_creates_asset_and_task_asset(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['new-0-brand'] = str(self.brand.pk)
        payload['new-0-model_name'] = 'Excite Run 900'
        payload['new-0-serial_no'] = 'SN-42'
        payload['new-0-code'] = ' EXC-900 '
        payload['new-0-outcome'] = TaskAsset.Outcome.INSPECTED_OK
        self.client.post(self.url, payload)

        new_asset = Asset.objects.get(model_name='Excite Run 900')
        self.assertEqual(new_asset.site, self.site)
        self.assertEqual((new_asset.serial_no, new_asset.code), ('SN-42', 'EXC-900'))
        task_asset = TaskAsset.objects.get(asset=new_asset)
        self.assertEqual(task_asset.outcome, TaskAsset.Outcome.INSPECTED_OK)

    def test_incomplete_new_asset_row_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['new-0-model_name'] = 'Excite Run 900'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_oversized_new_asset_model_name_is_rejected_cleanly(self):
        # Asset.model_name is max_length=150 — must fail as a form error,
        # not a database crash.
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['new-0-brand'] = str(self.brand.pk)
        payload['new-0-model_name'] = 'x' * 151
        payload['new-0-outcome'] = TaskAsset.Outcome.INSPECTED_OK
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_part_row_creates_part_used(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['parts-0-part_code'] = 'BELT-42'
        payload['parts-0-description'] = 'Drive belt'
        payload['parts-0-quantity'] = '1'
        payload['parts-0-unit_cost'] = '85.00'
        payload['parts-0-currency_code'] = 'AED'
        self.client.post(self.url, payload)

        report = WorkReport.objects.get(task=self.task)
        part = report.parts_used.get()
        self.assertEqual(part.part_code, 'BELT-42')
        self.assertEqual(part.quantity, 1)

    def test_oversized_part_code_is_rejected_cleanly(self):
        # PartUsed.part_code is max_length=50 — must fail as a form error,
        # not a database crash.
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['parts-0-part_code'] = 'x' * 51
        payload['parts-0-quantity'] = '1'
        payload['parts-0-unit_cost'] = '85.00'
        payload['parts-0-currency_code'] = 'AED'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_a_part_needs_no_price(self):
        # Managers agree prices with the customer before the visit: a part
        # is recorded with its code and quantity only.
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['parts-0-part_code'] = 'BELT-42'
        payload['parts-0-quantity'] = '2'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        part = WorkReport.objects.get(task=self.task).parts_used.get()
        self.assertEqual((part.part_code, part.quantity, part.unit_cost), ('BELT-42', 2, None))
        self.assertNotContains(self.client.get(self.url), 'Unit cost')

    def test_excessive_part_quantity_is_rejected_cleanly(self):
        # PositiveIntegerField has no upper bound of its own; this must fail
        # as a form error rather than a Postgres integer-overflow crash.
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['parts-0-part_code'] = 'BELT-42'
        payload['parts-0-quantity'] = '100000'
        payload['parts-0-unit_cost'] = '85.00'
        payload['parts-0-currency_code'] = 'AED'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_signature_upload_sets_absolute_url(self):
        self.client.login(username='tech1', password='pass12345')
        signature = SimpleUploadedFile('sig.png', REAL_PNG, content_type='image/png')
        payload = self._base_payload()
        payload['signature'] = signature
        self.client.post(self.url, payload)

        report = WorkReport.objects.get(task=self.task)
        self.assertTrue(report.signature_url.startswith('http'))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_non_image_signature_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        signature = SimpleUploadedFile(
            'sig.html', b'<script>alert(1)</script>', content_type='image/png',
        )
        payload = self._base_payload()
        payload['signature'] = signature

        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_already_submitted_report_can_be_corrected(self):
        WorkReport.objects.create(
            task=self.task, findings='x', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )
        self.client.login(username='tech1', password='pass12345')

        response = self.client.post(self.url, self._base_payload(findings='changed'))
        self.assertEqual(response.status_code, 302)
        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(report.findings, 'changed')

    def test_existing_asset_management_form_renders_even_with_zero_assets(self):
        # Regression: the management form must render unconditionally, or a
        # site with no existing assets submits a formset Django can't bind
        # ("ManagementForm data is missing"), and the whole report silently
        # fails to save with no visible error — found via manual browser
        # testing, since _base_payload above hand-writes the management
        # form fields and so never exercises the real rendered template.
        empty_site = Site.objects.create(customer=self.customer, name='Downtown Branch', address='Downtown')
        task = Task.objects.create(
            task_number='AE-0099', site=empty_site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(f'/tasks/my/{task.pk}/report/')
        self.assertContains(response, 'existing-TOTAL_FORMS')

        payload = {
            'findings': 'Elliptical clicking', 'action_taken': 'Tightened bolt', 'resolved': 'True',
            'labour_hours': '0.75', 'customer_name': 'Layla',
        }
        payload.update(self._management_form('existing', total=0))
        payload.update(self._management_form('new', total=self.NEW_ASSET_ROWS))
        payload.update(self._management_form('parts', total=self.PART_ROWS))
        response = self.client.post(f'/tasks/my/{task.pk}/report/', payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(WorkReport.objects.filter(task=task).exists())

    def test_resubmission_replaces_parts(self):
        report = WorkReport.objects.create(
            task=self.task, findings='old findings', resolved=False, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )
        PartUsed.objects.create(
            report=report, part_code='OLD-1', quantity=1, unit_cost='10.00', currency_code='AED',
        )

        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload(findings='fixed findings')
        payload['parts-0-part_code'] = 'NEW-1'
        payload['parts-0-quantity'] = '2'
        payload['parts-0-unit_cost'] = '20.00'
        payload['parts-0-currency_code'] = 'AED'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)

        report.refresh_from_db()
        self.assertEqual(report.findings, 'fixed findings')
        parts = list(report.parts_used.all())
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].part_code, 'NEW-1')


class TechnicianHoursTests(TaskTestCase):
    url = '/tasks/technicians/hours/'

    def setUp(self):
        super().setUp()
        self.helper_user = User.objects.create_user('helper1', password='pass12345')
        self.helper = Technician.objects.create(
            user=self.helper_user, country=self.country, full_name='Omar Helper',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.task = self._task_with_report('AE-0001', hours='2.50', estimated='2.00')
        TaskAssignment.objects.create(
            task=self.task, technician=self.helper, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        self._task_with_report('AE-0002', hours='1.00')

    def _task_with_report(self, number, hours, estimated=None, submitted_at=None, site=None):
        task = Task.objects.create(
            task_number=number, site=site or self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.COMPLETED,
            estimated_hours=estimated,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=task, findings='x', resolved=True, labour_hours=hours, customer_name='Ali',
            submitted_at=submitted_at or timezone.now(),
        )
        return task

    def _row(self, response, technician):
        return next(row for row in response.context['rows'] if row['technician'] == technician)

    def test_supervisor_sees_hours_summed_for_the_lead(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        row = self._row(response, self.technician)
        self.assertEqual(len(row['reports']), 2)
        self.assertEqual(str(row['hours']), '3.50')
        self.assertEqual(str(row['estimated']), '2.00')
        # Person-hours: the lead's 3.50, plus the helper's 2.50 on AE-0001.
        self.assertEqual(str(response.context['total_hours']), '6.00')

    def test_helper_gets_the_report_hours_by_default(self):
        self.client.login(username='supervisor1', password='pass12345')
        row = self._row(self.client.get(self.url), self.helper)
        self.assertEqual(str(row['hours']), '2.50')
        self.assertEqual(row['helped_on'], 1)

    def test_helper_hours_the_lead_entered_win(self):
        self.task.assignments.filter(technician=self.helper).update(labour_hours='1.00')
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(str(self._row(self.client.get(self.url), self.helper)['hours']), '1.00')

    def test_hours_before_a_handover_count_for_the_old_lead(self):
        task = Task.objects.create(
            task_number='AE-0003', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.IN_PROGRESS,
        )
        TaskAssignment.objects.create(
            task=task, technician=self.helper, role=TaskAssignment.Role.LEAD, assigned_at=timezone.now(),
            is_active=False, ended_at=timezone.now(), end_reason='sick', labour_hours='1.25',
        )
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(str(self._row(self.client.get(self.url), self.helper)['hours']), '3.75')

    def test_other_months_and_countries_are_excluded(self):
        self._task_with_report('AE-0003', hours='5.00', submitted_at=timezone.now() - timedelta(days=62))
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek', address='Cairo')
        # An Egyptian technician's report — not this country's hours.
        cairo_user = User.objects.create_user('cairo_tech', password='pass12345')
        cairo_tech = Technician.objects.create(
            user=cairo_user, country=other_country, full_name='Cairo Tech',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        cairo_task = self._task_with_report('EG-0001', hours='9.00', site=other_site)
        cairo_task.assignments.update(technician=cairo_tech)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        row = self._row(response, self.technician)
        self.assertEqual(str(row['hours']), '3.50')
        self.assertNotIn(cairo_tech, [r['technician'] for r in response.context['rows']])

    def test_earlier_month_is_reachable(self):
        # Mid-month, midday: the same month in UTC and in the country's own time,
        # whatever time of day the test runs.
        earlier = (timezone.now() - timedelta(days=62)).replace(day=15, hour=12)
        self._task_with_report('AE-0003', hours='5.00', submitted_at=earlier)
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url, {'month': timezone.localtime(earlier).strftime('%Y-%m')})
        self.assertEqual(str(self._row(response, self.technician)['hours']), '5.00')

    def test_technician_is_forbidden(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_overrun_is_more_than_half_again_the_estimate(self):
        exactly_half_over = self._task_with_report('AE-0010', hours='3.00', estimated='2.00')
        well_over = self._task_with_report('AE-0011', hours='3.01', estimated='2.00')
        no_estimate = self._task_with_report('AE-0012', hours='40.00')
        def report_for(task):
            return WorkReport.objects.select_related('task').get(task=task)

        self.assertFalse(report_for(exactly_half_over).is_overrun)
        self.assertTrue(report_for(well_over).is_overrun)
        self.assertFalse(report_for(no_estimate).is_overrun)

    def test_hours_page_counts_and_flags_overruns(self):
        self._task_with_report('AE-0011', hours='5.00', estimated='2.00')
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(self._row(response, self.technician)['overrun_count'], 1)
        self.assertContains(response, 'Significantly over estimate')

    def test_task_detail_flags_an_overrun_report(self):
        task = self._task_with_report('AE-0011', hours='5.00', estimated='2.00')
        self.client.login(username='supervisor1', password='pass12345')
        self.assertContains(self.client.get(f'/tasks/{task.pk}/'), 'Significantly over estimate')
        self.assertNotContains(self.client.get(f'/tasks/{self.task.pk}/'), 'Significantly over estimate')

    def test_technician_sees_own_month_hours_on_my_progress(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.context['month_hours']), '3.50')
        self.assertEqual(response.context['month_report_count'], 2)

    def test_helper_sees_their_hours_on_my_progress(self):
        self.client.login(username='helper1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertEqual(str(response.context['month_hours']), '2.50')

    def test_csv_export_has_summary_and_report_rows(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/hours/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.decode()
        self.assertIn('Tarek Tech,2,3.50,2.00,0,0', body)
        self.assertIn('Omar Helper,0,2.50,,0,1', body)
        self.assertIn('Omar Helper,AE-0001,Fitness First', body)
        self.assertIn('Tarek Tech,AE-0001,Fitness First', body)

    def test_csv_export_is_forbidden_to_technicians(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/technicians/hours/export/').status_code, 403)


class UnscheduledAlarmTests(TaskTestCase):
    """The 24-working-hour "still not scheduled" alarm (tasks/alarms.py)."""

    def setUp(self):
        super().setUp()
        self.country.timezone = 'Asia/Riyadh'
        self.country.weekend_days = '4,5'  # Friday–Saturday
        self.country.save()
        self.tz = ZoneInfo('Asia/Riyadh')
        self.supervisor = self.supervisor_user.technician

    def _task(self, number='SA-0001', created_at=None, **fields):
        created_at = created_at or timezone.now() - timedelta(days=5)
        task = Task.objects.create(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=created_at, created_by=self.supervisor_user, status=Task.Status.NEW, **fields,
        )
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.CREATED, occurred_at=created_at, actor=self.supervisor_user,
        )
        return task

    def _local(self, *args):
        return datetime(*args, tzinfo=self.tz)

    def test_weekend_days_do_not_count(self):
        from tasks.alarms import working_hours_between

        # Thursday 15:00 -> Sunday 15:00 is 72 hours, only 24 of them working.
        thursday = self._local(2026, 10, 1, 15)
        sunday = self._local(2026, 10, 4, 15)
        self.assertEqual(working_hours_between(thursday, sunday, self.tz, {4, 5}), 24)
        # Saturday–Sunday weekend: rest of Thursday (9h) plus all of Friday (24h).
        self.assertEqual(working_hours_between(thursday, sunday, self.tz, {5, 6}), 33)

    def test_created_thursday_is_not_overdue_until_after_sunday(self):
        from tasks.alarms import overdue_unscheduled_tasks

        task = self._task(created_at=self._local(2026, 10, 1, 15))
        saturday_night = self._local(2026, 10, 3, 23)
        sunday_evening = self._local(2026, 10, 4, 16)
        self.assertEqual(overdue_unscheduled_tasks(self.country, self.supervisor, now=saturday_night), [])
        self.assertEqual(overdue_unscheduled_tasks(self.country, self.supervisor, now=sunday_evening), [task])

    def test_an_emergency_counts_the_weekend_too(self):
        from tasks.alarms import overdue_unscheduled_tasks

        # The team works weekends for emergencies: Thursday 15:00 plus 24
        # hours is Friday 15:00, weekend or not.
        task = self._task(created_at=self._local(2026, 10, 1, 15))
        task.priority = Task.Priority.EMERGENCY
        task.save(update_fields=['priority'])
        friday_evening = self._local(2026, 10, 2, 16)
        self.assertEqual(overdue_unscheduled_tasks(self.country, self.supervisor, now=friday_evening), [task])

    def test_scheduled_or_started_tasks_are_not_flagged(self):
        from tasks.alarms import overdue_unscheduled_tasks

        self._task('SA-0001', scheduled_date=timezone.localdate())
        self._task('SA-0002', scheduled_for=timezone.now())
        started = self._task('SA-0003')
        started.status = Task.Status.IN_PROGRESS
        started.save(update_fields=['status'])
        self.assertEqual(overdue_unscheduled_tasks(self.country, self.supervisor), [])

    def test_only_the_responsible_supervisor_sees_their_task(self):
        from tasks.alarms import overdue_unscheduled_tasks

        other_user = User.objects.create_user('supervisor2', password='pass12345')
        other = Technician.objects.create(
            user=other_user, country=self.country, full_name='Samir Super',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        task = self._task(responsible_supervisor=other)
        self.assertEqual(overdue_unscheduled_tasks(self.country, self.supervisor), [])
        self.assertEqual(overdue_unscheduled_tasks(self.country, other), [task])

    def test_plain_technician_gets_no_alarm(self):
        from tasks.alarms import overdue_unscheduled_tasks

        self._task()
        self.assertEqual(overdue_unscheduled_tasks(self.country, self.technician), [])

    def test_alarm_shows_in_the_supervisors_bell(self):
        self._task()
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertContains(response, 'Not scheduled within 24 working hours')
        self.assertEqual(response.context['unseen_notifications'][0]['kind'], 'unscheduled_overdue')


class TaskAdminDeleteTests(TaskTestCase):
    def test_even_a_superuser_cannot_delete_a_task_from_the_admin(self):
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        User.objects.create_superuser('root', password='pass12345')
        self.client.login(username='root', password='pass12345')
        response = self.client.post(f'/admin/tasks/task/{task.pk}/delete/', {'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())


class TapUndoAndCorrectionTests(TaskTestCase):
    """A lead undoing their own mistaken tap (10 minutes), and a manager
    correcting a time later, with a reason.
    """

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now() - timedelta(days=1), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.ASSIGNED,
            occurred_at=timezone.now() - timedelta(hours=3), actor=self.supervisor_user,
        )
        self.url = f'/tasks/my/{self.task.pk}/'
        self.client.login(username='tech1', password='pass12345')

    def _tap(self, action):
        return self.client.post(self.url, {'action': action})

    def test_undo_accept_puts_the_task_back_to_assigned(self):
        self._tap('accept')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)
        self.assertIsNotNone(self.client.get(self.url).context['undoable_tap'])

        self._tap('undo')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)
        self.assertFalse(self.task.events.filter(event_type=TaskEvent.EventType.ACCEPTED).exists())

    def test_undo_start_puts_the_task_back_to_accepted(self):
        for action in ['accept', 'en_route', 'arrive', 'start']:
            self._tap(action)
        self._tap('undo')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)
        self.assertTrue(self.task.events.filter(event_type=TaskEvent.EventType.ARRIVED).exists())

    def test_only_the_last_tap_is_undone(self):
        self._tap('accept')
        self._tap('en_route')
        self._tap('undo')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)
        self.assertFalse(self.task.events.filter(event_type=TaskEvent.EventType.EN_ROUTE).exists())
        self.assertTrue(self.task.events.filter(event_type=TaskEvent.EventType.ACCEPTED).exists())

    def test_no_undo_after_ten_minutes(self):
        self._tap('accept')
        self.task.events.filter(event_type=TaskEvent.EventType.ACCEPTED).update(
            occurred_at=timezone.now() - timedelta(minutes=11),
        )
        self._tap('undo')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ACCEPTED)

    def test_cannot_undo_someone_elses_event(self):
        # The last event is the supervisor's assignment, not a tap of theirs.
        self._tap('undo')
        self.assertTrue(self.task.events.filter(event_type=TaskEvent.EventType.ASSIGNED).exists())

    # --- manager corrections ---

    def _manager_login(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')

    def _correct(self, event, when, reason='Forgot to tap on site'):
        return self.client.post(f'/tasks/{self.task.pk}/', {
            'action': 'correct_event', 'event_id': event.pk,
            # Typed in the manager's own country time, as the form shows it.
            'corrected_at': timezone.localtime(when, ZoneInfo(self.country.timezone)).strftime('%Y-%m-%dT%H:%M'),
            'reason': reason,
        })

    def _accepted_event(self):
        self._tap('accept')
        event = self.task.events.get(event_type=TaskEvent.EventType.ACCEPTED)
        event.occurred_at = timezone.now() - timedelta(hours=1)
        event.save(update_fields=['occurred_at'])
        return event

    def test_manager_corrects_a_time_and_the_original_is_kept(self):
        event = self._accepted_event()
        original = event.occurred_at
        self._manager_login()
        self._correct(event, timezone.now() - timedelta(hours=2))
        event.refresh_from_db()
        self.assertEqual(event.occurred_at, original)
        self.assertIsNotNone(event.corrected_at)
        self.assertEqual(event.correction_reason, 'Forgot to tap on site')
        self.assertEqual(event.corrected_by.username, 'manager1')

    def test_correction_needs_a_reason_and_a_sensible_time(self):
        event = self._accepted_event()
        self._manager_login()
        self._correct(event, timezone.now() - timedelta(hours=2), reason='')
        self._correct(event, timezone.now() + timedelta(hours=1))
        self._correct(event, timezone.now() - timedelta(hours=5))  # before the assignment
        event.refresh_from_db()
        self.assertIsNone(event.corrected_at)

    def test_supervisor_cannot_correct_times(self):
        event = self._accepted_event()
        self.client.login(username='supervisor1', password='pass12345')
        response = self._correct(event, timezone.now() - timedelta(hours=2))
        self.assertEqual(response.status_code, 403)
        event.refresh_from_db()
        self.assertIsNone(event.corrected_at)


class SupervisorFieldLockTests(TaskTestCase):
    """Estimate, billing, warranty and the responsible supervisor lock for
    supervisors once the report is filed; the promised date once work has
    started. Managers can still change all of them.
    """

    def setUp(self):
        super().setUp()
        self.supervisor = self.supervisor_user.technician
        self.task = Task.objects.create(
            task_number='UAE-0001', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            promised_at=timezone.now() + timedelta(days=1), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED, estimated_hours='2.00', responsible_supervisor=self.supervisor,
        )
        self.url = f'/tasks/{self.task.pk}/edit/'

    def _payload(self, **overrides):
        payload = {
            'priority': Task.Priority.NORMAL, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CONTRACT, 'is_warranty': 'true', 'estimated_hours': '9.00',
            'responsible_supervisor': '', 'description': '',
            'promised_at': (timezone.localtime(timezone.now() + timedelta(days=5), ZoneInfo(self.country.timezone))
                            .strftime('%Y-%m-%dT%H:%M')),
            'products-TOTAL_FORMS': '0', 'products-INITIAL_FORMS': '0',
            'products-MIN_NUM_FORMS': '0', 'products-MAX_NUM_FORMS': '1000',
        }
        payload.update(overrides)
        return payload

    def _set_status(self, status):
        self.task.status = status
        self.task.save(update_fields=['status'])

    def test_supervisor_edits_freely_while_the_task_is_open(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(responsible_supervisor=self.supervisor.pk))
        self.task.refresh_from_db()
        self.assertEqual(str(self.task.estimated_hours), '9.00')
        self.assertEqual(self.task.billing_type, Task.BillingType.CONTRACT)

    def test_supervisor_cannot_change_them_after_the_report_is_filed(self):
        self._set_status(Task.Status.PENDING_SUPERVISOR_REVIEW)
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(str(self.task.estimated_hours), '2.00')
        self.assertEqual(self.task.billing_type, Task.BillingType.CHARGEABLE)
        self.assertIsNone(self.task.is_warranty)
        self.assertEqual(self.task.responsible_supervisor, self.supervisor)

    def test_promised_date_locks_once_work_starts(self):
        original = self.task.promised_at
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.STARTED, occurred_at=timezone.now(),
            actor=self.tech_user,
        )
        self._set_status(Task.Status.IN_PROGRESS)
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(responsible_supervisor=self.supervisor.pk))
        self.task.refresh_from_db()
        self.assertEqual(self.task.promised_at.replace(second=0, microsecond=0),
                         original.replace(second=0, microsecond=0))
        # Still before the report, so the estimate is still theirs to change.
        self.assertEqual(str(self.task.estimated_hours), '9.00')

    def test_manager_can_still_change_them(self):
        self._set_status(Task.Status.COMPLETED)
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        self.client.post(self.url, self._payload(responsible_supervisor=self.supervisor.pk))
        self.task.refresh_from_db()
        self.assertEqual(str(self.task.estimated_hours), '9.00')
        self.assertEqual(self.task.billing_type, Task.BillingType.CONTRACT)


class TicketEscalationTests(TaskTestCase):
    """The support desk escalating a ticket to the Operations Manager."""

    def setUp(self):
        super().setUp()
        self.support_user = User.objects.create_user('support1', password='pass12345')
        self.support = Technician.objects.create(
            user=self.support_user, country=self.country, full_name='Dana Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.ops_user = User.objects.create_user('ops1', password='pass12345', email='ops@example.com')
        self.ops = Technician.objects.create(
            user=self.ops_user, country=self.country, full_name='Mohamed Fahmy',
            language='en', role=Technician.Role.OPERATIONS_MANAGER, employment_type='staff',
        )
        self.portal_user = User.objects.create_user('fitnessfirst_portal', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.portal_user)
        self.ticket = CustomerTicket.objects.create(
            country=self.country, customer=self.customer, ticket_number='AE-T0001', company_name='Fitness First',
            site_description='Marina Branch', contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/tickets/{self.ticket.pk}/'

    def _escalate(self, **overrides):
        self.client.login(username='support1', password='pass12345')
        return self.client.post(self.url, {
            'action': 'escalate', 'escalated_to': self.ops.pk,
            'reason': 'Customer wants a free replacement outside warranty',
            'customer_message': 'We are reviewing a replacement for you.', **overrides,
        })

    def test_support_escalates_and_the_ops_manager_is_alerted(self):
        self._escalate()
        escalation = TicketEscalation.objects.get(ticket=self.ticket)
        self.assertEqual(escalation.decision, TicketEscalation.Decision.PENDING)
        self.assertEqual(mail.outbox[-1].to, ['ops@example.com'])

        self.client.login(username='ops1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_decide'])
        self.assertEqual(response.context['unseen_notifications'][0]['kind'], 'escalation_pending')

    def test_the_email_goes_out_the_moment_it_is_escalated(self):
        self._escalate()
        email = mail.outbox[-1]
        self.assertEqual(email.to, ['ops@example.com'])
        self.assertIn('AE-T0001', email.subject)
        self.assertIn('free replacement outside warranty', email.body)

    def test_escalating_to_someone_without_an_email_warns(self):
        self.ops_user.email = ''
        self.ops_user.save(update_fields=['email'])
        response = self._escalate()
        self.assertEqual(len(mail.outbox), 0)
        shown = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('no email address' in message for message in shown))

    def test_a_failed_send_is_logged_not_swallowed(self):
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('SMTP down')):
            with self.assertLogs('tasks.views', level='ERROR') as logs:
                self._escalate()
        self.assertIn('Ticket escalated to you', logs.output[0])
        # The escalation itself still happened.
        self.assertTrue(TicketEscalation.objects.filter(ticket=self.ticket).exists())

    def test_deploy_check_flags_emails_that_only_go_to_the_log(self):
        from tasks.checks import email_is_really_sent

        with override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend', EMAIL_HOST=''):
            self.assertEqual([w.id for w in email_is_really_sent(None)], ['tasks.W001'])
        with override_settings(
            EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='smtp.example.com',
        ):
            self.assertEqual(email_is_really_sent(None), [])

    def test_only_one_pending_escalation_at_a_time(self):
        self._escalate()
        self._escalate()
        self.assertEqual(TicketEscalation.objects.filter(ticket=self.ticket).count(), 1)

    def test_ops_manager_decides_and_it_goes_back_to_the_desk(self):
        self._escalate()
        self.client.login(username='ops1', password='pass12345')
        self.client.post(self.url, {
            'action': 'decide_escalation', 'decision': 'approved', 'decision_note': 'Approve, goodwill.',
        })
        escalation = TicketEscalation.objects.get(ticket=self.ticket)
        self.assertEqual(escalation.decision, TicketEscalation.Decision.APPROVED)
        self.assertIsNotNone(escalation.decided_at)
        self.assertTrue(self.ticket.notifications.filter(kind=TicketNotification.Kind.ESCALATION_DECIDED).exists())
        # Decided — no longer in the ops manager's bell.
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_decide'])

    def test_ops_manager_opens_tickets_not_escalated_to_them(self):
        # The support manager's own manager has the whole ticket desk, not
        # only what's escalated to them.
        self.client.login(username='ops1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_ops_manager_can_use_the_desk_actions(self):
        self.client.login(username='ops1', password='pass12345')
        self.client.post(self.url, {'action': 'dismiss', 'dismissal_reason': 'x'})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.DISMISSED)

    def test_escalates_only_one_level_up_never_to_yourself(self):
        other_support = Technician.objects.create(
            user=User.objects.create_user('support2', password='pass12345'), country=self.country,
            full_name='Other Support', language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self._escalate(escalated_to=self.support.pk)
        self._escalate(escalated_to=other_support.pk)
        self.assertFalse(TicketEscalation.objects.exists())

    def test_operations_manager_in_another_country_can_still_be_reached(self):
        # The Operations Manager covers every country, wherever they're based.
        from reference.models import Country

        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        cairo_ops = Technician.objects.create(
            user=User.objects.create_user('ops2', password='pass12345'), country=other_country,
            full_name='Cairo Ops', language='en', role=Technician.Role.OPERATIONS_MANAGER, employment_type='staff',
        )
        self._escalate(escalated_to=cairo_ops.pk)
        self.assertEqual(TicketEscalation.objects.get().escalated_to, cairo_ops)

    def test_operations_manager_is_the_top_of_the_chain(self):
        self.client.login(username='ops1', password='pass12345')
        self.assertFalse(self.client.get(self.url).context['can_escalate'])

    def test_customer_sees_the_role_and_message_but_nothing_internal(self):
        self._escalate()
        self.client.get(self.url)  # shows (and clears) the staff confirmation
        self.client.login(username='fitnessfirst_portal', password='pass12345')
        response = self.client.get(f'/tasks/tickets/status/{self.ticket.token}/')
        self.assertContains(response, 'management team')
        self.assertNotContains(response, 'Operations Manager')
        self.assertContains(response, 'We are reviewing a replacement for you.')
        self.assertNotContains(response, 'Mohamed Fahmy')
        self.assertNotContains(response, 'free replacement outside warranty')

        TicketEscalation.objects.update(
            decision=TicketEscalation.Decision.REJECTED, decision_note='Not covered.', decided_at=timezone.now(),
        )
        response = self.client.get(f'/tasks/tickets/status/{self.ticket.token}/')
        self.assertNotContains(response, 'Not covered.')
        self.assertNotContains(response, 'Rejected')

    def test_escalate_shortcut_sits_by_the_reply_box_while_open(self):
        self.client.login(username='support1', password='pass12345')
        self.assertContains(self.client.get(self.url), 'href="#escalation"')
        self.ticket.status = CustomerTicket.Status.DISMISSED
        self.ticket.save(update_fields=['status'])
        self.assertFalse(self.client.get(self.url).context['can_escalate'])

    def test_can_escalate_again_after_a_decision(self):
        self._escalate()
        TicketEscalation.objects.update(decision=TicketEscalation.Decision.INSTRUCTIONS, decided_at=timezone.now())
        self._escalate()
        self.assertEqual(TicketEscalation.objects.filter(ticket=self.ticket).count(), 2)

    def test_ticket_list_flags_pending_escalations(self):
        self._escalate()
        response = self.client.get('/tasks/tickets/')
        self.assertTrue(response.context['tickets'][0].is_escalated)


class CountrySupportManagerTests(TaskTestCase):
    """A Country Support Manager runs one country's ticket desk, and the
    strict escalation chain above them: Country Support Manager ->
    Technical Support Manager -> Operations Manager."""

    def setUp(self):
        super().setUp()
        self.other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        self.country_support = self._staff('csm1', Technician.Role.COUNTRY_SUPPORT, self.country)
        self.support = self._staff('support1', Technician.Role.SUPPORT_MANAGER, self.other_country, 'tsm@example.com')
        self.ops = self._staff('ops1', Technician.Role.OPERATIONS_MANAGER, self.other_country, 'ops@example.com')
        self.ticket = self._ticket(self.country, 'AE-T0001', 'Fitness First')
        self.other_ticket = self._ticket(self.other_country, 'EG-T0001', 'Cairo Gym')
        self.url = f'/tasks/tickets/{self.ticket.pk}/'

    def _staff(self, username, role, country, email=''):
        return Technician.objects.create(
            user=User.objects.create_user(username, password='pass12345', email=email), country=country,
            full_name=username.title(), language='en', role=role, employment_type='staff',
        )

    def _ticket(self, country, number, company):
        return CustomerTicket.objects.create(
            country=country, ticket_number=number, company_name=company, site_description='Main Branch',
            contact_name='Ali', contact_phone='0500000000', description='Treadmill belt slipping.',
            submitted_at=timezone.now(),
        )

    def _escalate(self, username, to, **extra):
        self.client.login(username=username, password='pass12345')
        return self.client.post(self.url, {
            'action': 'escalate', 'escalated_to': to.pk, 'reason': 'Out-of-warranty replacement requested',
            'customer_message': 'We are reviewing this for you.', **extra,
        })

    def test_handles_own_country_tickets_only(self):
        self.client.login(username='csm1', password='pass12345')
        response = self.client.get('/tasks/tickets/', {'status': 'all', 'country': 'all'})
        self.assertContains(response, 'Fitness First')
        self.assertNotContains(response, 'Cairo Gym')
        self.assertFalse(response.context['can_view_all_countries'])
        self.assertEqual(self.client.get(f'/tasks/tickets/{self.other_ticket.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/tasks/new/?ticket={self.other_ticket.pk}').status_code, 404)

    def test_converts_own_country_ticket_into_a_task(self):
        self.client.login(username='csm1', password='pass12345')
        create_url = f'/tasks/new/?ticket={self.ticket.pk}'
        self.assertEqual(self.client.get(create_url).status_code, 200)
        response = self.client.post(create_url, {
            'site': self.site.pk, 'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Replace the belt.',
        })
        self.assertEqual(response.status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CONVERTED)

    def test_gets_the_new_ticket_alert_for_their_country(self):
        self.client.login(username='csm1', password='pass12345')
        TicketNotification.objects.create(ticket=self.ticket, kind='new_ticket', created_at=timezone.now())
        TicketNotification.objects.create(ticket=self.other_ticket, kind='new_ticket', created_at=timezone.now())
        tickets = [item['ticket'] for item in self.client.get('/tasks/tickets/').context['unseen_notifications']]
        self.assertEqual(tickets, [self.ticket])

    def test_escalates_only_to_the_technical_support_manager(self):
        self._escalate('csm1', self.ops)
        self.assertFalse(TicketEscalation.objects.exists())

        response = self._escalate('csm1', self.support)
        escalation = TicketEscalation.objects.get()
        # Reachable even though they're based in another country.
        self.assertEqual(escalation.escalated_to, self.support)
        self.assertEqual(mail.outbox[-1].to, ['tsm@example.com'])
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_technical_support_manager_decides_it(self):
        self._escalate('csm1', self.support)
        self.client.login(username='support1', password='pass12345')
        page = self.client.get(self.url)
        self.assertTrue(page.context['can_decide'])
        self.assertEqual(page.context['unseen_notifications'][0]['kind'], 'escalation_pending')
        self.client.post(self.url, {
            'action': 'decide_escalation', 'decision': 'approved', 'decision_note': 'Approve, goodwill.',
        })
        self.assertEqual(TicketEscalation.objects.get().decision, TicketEscalation.Decision.APPROVED)

    def test_technical_support_manager_forwards_it_to_the_operations_manager(self):
        self._escalate('csm1', self.support)
        self._escalate('support1', self.ops, customer_message='')

        first, second = TicketEscalation.objects.order_by('escalated_at', 'pk')
        self.assertEqual(first.decision, TicketEscalation.Decision.FORWARDED)
        self.assertIsNotNone(first.decided_at)
        self.assertEqual(second.escalated_by, self.support)
        self.assertEqual(second.escalated_to, self.ops)
        self.assertEqual(second.decision, TicketEscalation.Decision.PENDING)
        self.assertEqual(mail.outbox[-1].to, ['ops@example.com'])

        self.client.login(username='ops1', password='pass12345')
        page = self.client.get(self.url)
        self.assertTrue(page.context['can_decide'])
        self.assertFalse(page.context['can_escalate'])

    def test_forwarded_is_not_a_choice_when_deciding(self):
        from tasks.forms import DecideEscalationForm
        choices = [value for value, label in DecideEscalationForm().fields['decision'].choices]
        self.assertNotIn(TicketEscalation.Decision.FORWARDED, choices)

    def test_customer_sees_both_messages_but_no_internal_titles(self):
        self._escalate('csm1', self.support)
        self._escalate('support1', self.ops, customer_message='A manager is reviewing your request.')
        self.client.get(self.url)  # shows (and clears) the staff confirmation
        PortalUser.objects.create(customer=self.customer, user=User.objects.create_user('fitnessfirst_portal', password='pass12345'))
        self.ticket.customer = self.customer
        self.ticket.save(update_fields=['customer'])
        self.client.login(username='fitnessfirst_portal', password='pass12345')
        response = self.client.get(f'/tasks/tickets/status/{self.ticket.token}/')
        self.assertContains(response, 'We are reviewing this for you.')
        self.assertContains(response, 'A manager is reviewing your request.')
        self.assertNotContains(response, 'reviewed your request')
        self.assertNotContains(response, 'Technical Support Manager')
        self.assertNotContains(response, 'Operations Manager')


class TechnicianTripTests(TaskTestCase):
    """A technician on a trip abroad: assignable there during the trip,
    hours still counted at home.
    """

    def setUp(self):
        super().setUp()
        from people.models import TechnicianTrip

        self.TechnicianTrip = TechnicianTrip
        self.ksa = Country.objects.create(
            name='Saudi Arabia', name_ar='السعودية', iso_code='SA', timezone='Asia/Riyadh', currency_code='SAR',
        )
        ksa_customer = Customer.objects.create(country=self.ksa, name='Riyadh Gym', segment='gym')
        self.ksa_site = Site.objects.create(customer=ksa_customer, name='Olaya', address='Riyadh')
        self.ksa_sup_user = User.objects.create_user('ksa_sup', password='pass12345')
        Technician.objects.create(
            user=self.ksa_sup_user, country=self.ksa, full_name='Khalid Super',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        self.ksa_task = Task.objects.create(
            task_number='KSA-0001', site=self.ksa_site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.ksa_sup_user,
            status=Task.Status.NEW, scheduled_date=timezone.localdate() + timedelta(days=3),
        )
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.edit_url = f'/tasks/technicians/{self.technician.pk}/edit/'

    def _trip(self, start_offset, end_offset):
        today = timezone.localdate()
        return self.TechnicianTrip.objects.create(
            technician=self.technician, country=self.ksa, start_date=today + timedelta(days=start_offset),
            end_date=today + timedelta(days=end_offset), created_by=self.manager_user, created_at=timezone.now(),
        )

    def _add_trip(self, country, start_offset, end_offset):
        today = timezone.localdate()
        return self.client.post(self.edit_url, {
            'action': 'add_trip', 'country': country.pk, 'note': 'Installation project',
            'start_date': (today + timedelta(days=start_offset)).isoformat(),
            'end_date': (today + timedelta(days=end_offset)).isoformat(),
        })

    def _candidate_names(self):
        self.client.login(username='ksa_sup', password='pass12345')
        response = self.client.get(f'/tasks/{self.ksa_task.pk}/assign/')
        return [t.full_name for t in response.context['candidates']], response

    def test_visitor_is_a_candidate_during_the_trip(self):
        self._trip(1, 5)
        names, response = self._candidate_names()
        self.assertIn('Tarek Tech', names)
        self.assertContains(response, 'Visiting from')

    def test_not_a_candidate_outside_the_trip_dates(self):
        self._trip(4, 6)  # the task is scheduled for day 3
        names, _response = self._candidate_names()
        self.assertNotIn('Tarek Tech', names)

    def test_destination_supervisor_can_assign_the_visitor(self):
        self._trip(1, 5)
        self.client.login(username='ksa_sup', password='pass12345')
        self.client.post(f'/tasks/{self.ksa_task.pk}/assign/', {
            'action': 'set_lead', 'technician': self.technician.pk,
        })
        self.assertTrue(self.ksa_task.assignments.filter(technician=self.technician, is_active=True).exists())

    def test_hours_abroad_count_on_the_home_hours_page(self):
        TaskAssignment.objects.create(
            task=self.ksa_task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=self.ksa_task, findings='x', resolved=True, labour_hours='4.00', customer_name='Ali',
            submitted_at=timezone.now(),
        )
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/hours/')
        row = next(r for r in response.context['rows'] if r['technician'] == self.technician)
        self.assertEqual(str(row['hours']), '4.00')

    def test_manager_adds_and_cancels_a_trip(self):
        self.client.login(username='manager1', password='pass12345')
        self._add_trip(self.ksa, 1, 5)
        trip = self.TechnicianTrip.objects.get(technician=self.technician)
        self.client.post(self.edit_url, {'action': 'cancel_trip', 'trip_id': trip.pk})
        self.assertFalse(self.TechnicianTrip.objects.exists())

    def test_trip_cannot_end_before_it_starts_or_be_to_home(self):
        self.client.login(username='manager1', password='pass12345')
        self._add_trip(self.ksa, 5, 1)
        self._add_trip(self.country, 0, 0)
        self.assertFalse(self.TechnicianTrip.objects.exists())

    def test_past_trip_cannot_be_cancelled(self):
        trip = self._trip(-10, -2)
        self.client.login(username='manager1', password='pass12345')
        self.client.post(self.edit_url, {'action': 'cancel_trip', 'trip_id': trip.pk})
        self.assertTrue(self.TechnicianTrip.objects.filter(pk=trip.pk).exists())

    def test_supervisor_cannot_add_trips(self):
        self.client.login(username='supervisor1', password='pass12345')
        self._add_trip(self.ksa, 0, 0)
        self.assertFalse(self.TechnicianTrip.objects.exists())


class PartsCatalogueTests(TaskTestCase):
    """The parts catalogue: who maintains it, CSV upload, and reports
    having to use its codes once it has any.
    """

    def setUp(self):
        super().setUp()
        from reference.models import Part

        self.Part = Part
        self.warehouse_user = User.objects.create_user('warehouse1', password='pass12345')
        Technician.objects.create(
            user=self.warehouse_user, country=self.country, full_name='Walid Warehouse',
            language='en', role=Technician.Role.WAREHOUSE_MANAGER, employment_type='staff',
        )

    def test_warehouse_manager_maintains_the_list_and_supervisor_cannot(self):
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post('/tasks/parts/', {'action': 'add', 'code': 'belt-01', 'description': 'Treadmill belt'})
        self.assertEqual(self.Part.objects.get().code, 'BELT-01')

        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/parts/').status_code, 403)

    def test_duplicate_code_is_refused(self):
        self.Part.objects.create(code='BELT-01')
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post('/tasks/parts/', {'action': 'add', 'code': 'Belt-01'})
        self.assertEqual(self.Part.objects.count(), 1)

    def test_csv_upload_adds_and_updates(self):
        self.Part.objects.create(code='BELT-01', description='old')
        upload = SimpleUploadedFile(
            'parts.csv', 'code,description\nbelt-01,Treadmill belt\nPAD-7,Seat pad\n'.encode(), content_type='text/csv',
        )
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post('/tasks/parts/', {'action': 'import', 'file': upload})
        self.assertEqual(self.Part.objects.get(code='BELT-01').description, 'Treadmill belt')
        self.assertEqual(self.Part.objects.get(code='PAD-7').description, 'Seat pad')
        self.assertFalse(self.Part.objects.filter(code='CODE').exists())

    def _stock_sheet(self, rows, picture_cell=None):
        """An .xlsx laid out like the warehouse's stock sheet: Code,
        Description, a colour column with no header, Qty; a picture placed
        in a code cell."""
        import io
        import openpyxl
        from openpyxl.drawing.image import Image as SheetImage
        from PIL import Image

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(['Code', 'Description', None, None, 'Qty'])
        sheet.append(['SPARE PARTS'])
        for row in rows:
            sheet.append(row)
        if picture_cell:
            picture = io.BytesIO()
            Image.new('RGB', (8, 8), 'red').save(picture, format='PNG')
            sheet.add_image(SheetImage(picture), picture_cell)
        data = io.BytesIO()
        book.save(data)
        return SimpleUploadedFile('Stock.xlsx', data.getvalue())

    def test_excel_sheet_adds_parts_with_their_colour_and_picture(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            upload = self._stock_sheet([
                ['rxp00010\n', 'Securing pin housing end cap', None, 'Color of frame: MATT BLACK', 4],
                ['7SA037', 'Lever with button', None, None, 14],
            ], picture_cell='A3')
            self.client.login(username='warehouse1', password='pass12345')
            response = self.client.post('/tasks/parts/', {'action': 'import', 'file': upload}, follow=True)
            self.assertContains(response, '2 parts added, 0 updated, 1 photos.')
            pin = self.Part.objects.get(code='RXP00010')
            self.assertEqual((pin.description, pin.colour), ('Securing pin housing end cap', 'Color of frame: MATT BLACK'))
            self.assertTrue(pin.photo.name.endswith('RXP00010.png'))
            self.assertFalse(self.Part.objects.get(code='7SA037').photo)
            self.assertFalse(self.Part.objects.filter(code__contains='SPARE').exists())  # a heading, not a part

    def test_uploading_the_same_sheet_again_stores_no_second_photo(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.client.login(username='warehouse1', password='pass12345')
            for expected in ('1 parts added, 0 updated, 1 photos.', '0 parts added, 0 updated, 0 photos.'):
                upload = self._stock_sheet([['RXP00010', 'Securing pin housing end cap']], picture_cell='A3')
                response = self.client.post('/tasks/parts/', {'action': 'import', 'file': upload}, follow=True)
                self.assertContains(response, expected)

    def test_uploading_again_keeps_what_the_sheet_leaves_empty(self):
        self.Part.objects.create(code='7SA037', description='Lever with button', colour='Black', is_active=False)
        self.client.login(username='warehouse1', password='pass12345')
        self.client.post('/tasks/parts/', {'action': 'import', 'file': self._stock_sheet([['7SA037', None]])})
        part = self.Part.objects.get()
        self.assertEqual((part.description, part.colour, part.is_active), ('Lever with button', 'Black', True))

    def test_a_part_gets_its_colour_and_photo_from_the_list(self):
        part = self.Part.objects.create(code='BELT-01')
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.client.login(username='warehouse1', password='pass12345')
            self.client.post('/tasks/parts/', {
                'action': 'edit', 'part_id': part.pk, 'description': 'Treadmill belt', 'colour': 'Grey',
                'photo': SimpleUploadedFile('belt.jpg', REAL_JPEG, content_type='image/jpeg'),
            })
            part.refresh_from_db()
            self.assertEqual((part.description, part.colour), ('Treadmill belt', 'Grey'))
            self.assertTrue(part.photo)

    def test_report_parts_must_come_from_the_catalogue(self):
        from reports.forms import PartUsedItemForm

        row = {'quantity': 1, 'unit_cost': '10.00', 'currency_code': 'AED'}
        # Empty catalogue: free text, as before.
        self.assertTrue(PartUsedItemForm({**row, 'part_code': 'ANY-1'}).is_valid())

        self.Part.objects.create(code='BELT-01', description='Treadmill belt')
        self.assertFalse(PartUsedItemForm({**row, 'part_code': 'ANY-1'}).is_valid())
        form = PartUsedItemForm({**row, 'part_code': 'belt-01'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['part_code'], 'BELT-01')
        self.assertEqual(form.cleaned_data['description'], 'Treadmill belt')

    def test_switched_off_parts_cannot_be_used(self):
        from reports.forms import PartUsedItemForm

        self.Part.objects.create(code='BELT-01')
        self.Part.objects.create(code='OLD-9', is_active=False)
        form = PartUsedItemForm({'part_code': 'OLD-9', 'quantity': 1, 'unit_cost': '1', 'currency_code': 'AED'})
        self.assertFalse(form.is_valid())


class DepartedTechnicianTests(TaskTestCase):
    """Deactivating someone still on tasks, then handing their work on."""

    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.replacement_user = User.objects.create_user('tech2', password='pass12345')
        self.replacement = Technician.objects.create(
            user=self.replacement_user, country=self.country, full_name='Rami Replacement',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.IN_PROGRESS,
        )
        self.lead = TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.assign_url = f'/tasks/{self.task.pk}/assign/'

    def _deactivate(self):
        self.client.login(username='manager1', password='pass12345')
        return self.client.post(f'/tasks/technicians/{self.technician.pk}/edit/', {
            'action': 'deactivate', 'reason': 'Contract ended',
        })

    def test_deactivating_someone_with_open_tasks_lists_them_to_reassign(self):
        response = self._deactivate()
        self.assertRedirects(response, f'/tasks/technicians/{self.technician.pk}/edit/')
        page = self.client.get(f'/tasks/technicians/{self.technician.pk}/edit/')
        self.assertEqual(page.context['tasks_to_reassign'], [self.task])

    def test_departed_lead_can_be_replaced_even_after_work_started(self):
        self._deactivate()
        self.client.login(username='supervisor1', password='pass12345')
        self.assertTrue(self.client.get(f'/tasks/{self.task.pk}/').context['departed_on_task'])
        self.client.post(self.assign_url, {
            'action': 'set_lead', 'technician': self.replacement.pk, 'end_reason': 'left_company',
        })
        new_lead = self.task.assignments.get(is_active=True, role=TaskAssignment.Role.LEAD)
        self.assertEqual(new_lead.technician, self.replacement)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.end_reason, 'left_company')

    def test_an_active_lead_stays_locked_once_work_started(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.assign_url, {
            'action': 'set_lead', 'technician': self.replacement.pk, 'end_reason': 'sick',
        })
        self.assertEqual(self.task.assignments.get(is_active=True).technician, self.technician)

    def test_manager_can_change_the_team_once_work_started(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(self.assign_url)
        self.assertFalse(response.context['locked'])
        self.assertTrue(response.context['manager_override'])

        self.client.post(self.assign_url, {
            'action': 'set_lead', 'technician': self.replacement.pk, 'end_reason': 'overloaded',
        })
        new_lead = self.task.assignments.get(is_active=True, role=TaskAssignment.Role.LEAD)
        self.assertEqual(new_lead.technician, self.replacement)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

        self.client.post(self.assign_url, {'action': 'add_helper', 'technician': self.technician.pk})
        helper = self.task.assignments.get(is_active=True, role=TaskAssignment.Role.HELPER)
        self.assertEqual(helper.technician, self.technician)
        self.client.post(self.assign_url, {
            'action': 'remove_helper', 'assignment_id': helper.pk, 'end_reason': 'overloaded',
        })
        helper.refresh_from_db()
        self.assertFalse(helper.is_active)

    def test_manager_cannot_change_the_team_once_the_report_is_filed(self):
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save(update_fields=['status'])
        self.client.login(username='manager1', password='pass12345')
        self.assertTrue(self.client.get(self.assign_url).context['locked'])
        self.client.post(self.assign_url, {
            'action': 'set_lead', 'technician': self.replacement.pk, 'end_reason': 'overloaded',
        })
        self.assertEqual(self.task.assignments.get(is_active=True).technician, self.technician)

    def test_departed_helper_can_be_removed_after_work_started(self):
        helper = TaskAssignment.objects.create(
            task=self.task, technician=self.replacement, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        self.replacement.set_active(False, reason='Left')
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.assign_url, {
            'action': 'remove_helper', 'assignment_id': helper.pk, 'end_reason': 'left_company',
        })
        helper.refresh_from_db()
        self.assertFalse(helper.is_active)


class CancelTaskTests(TaskTestCase):
    """A manager calls a task off before its report is filed."""

    def setUp(self):
        super().setUp()
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.url = f'/tasks/{self.task.pk}/'

    def test_manager_cancels_with_a_reason_and_the_team_is_told(self):
        self.client.login(username='manager1', password='pass12345')
        with patch('tasks.views.push_cancelled') as push:
            self.client.post(self.url, {'action': 'cancel_task', 'note': 'Customer called it off'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CANCELLED)
        event = self.task.events.get(event_type=TaskEvent.EventType.CANCELLED)
        self.assertEqual(event.note, 'Customer called it off')
        push.assert_called_once_with(self.technician, self.task)

    def test_a_reason_is_required(self):
        self.client.login(username='manager1', password='pass12345')
        self.client.post(self.url, {'action': 'cancel_task', 'note': ''})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)

    def test_cannot_cancel_once_the_report_is_filed(self):
        self.task.status = Task.Status.COMPLETED
        self.task.save(update_fields=['status'])
        self.client.login(username='manager1', password='pass12345')
        self.assertFalse(self.client.get(self.url).context['can_cancel'])
        self.client.post(self.url, {'action': 'cancel_task', 'note': 'x'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_supervisor_cannot_cancel(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'cancel_task', 'note': 'x'})
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)


class RebookStoppedTaskTests(TaskTestCase):
    """A task stopped on site (blocked) gets a new visit: the same task, a
    new time, back to its team, the stop kept in its history."""

    def setUp(self):
        super().setUp()
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.BLOCKED,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.url = f'/tasks/{self.task.pk}/'
        TaskEvent.objects.create(
            task=self.task, event_type=TaskEvent.EventType.BLOCKED, occurred_at=timezone.now(),
            actor=self.supervisor_user, note='Gym closed',
        )
        # Typed in the country's own time (Dubai), stored in UTC.
        self.new_time = (timezone.localtime(timezone.now(), ZoneInfo('Asia/Dubai')) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)

    def _rebook(self, when, note=''):
        return self.client.post(self.url, {
            'action': 'rebook', 'scheduled_for': when.strftime('%Y-%m-%dT%H:%M'), 'note': note,
        })

    def test_manager_books_a_new_visit_and_the_lead_is_told(self):
        self.client.login(username='manager1', password='pass12345')
        self.assertContains(self.client.get(self.url), 'Gym closed')
        # The lead hears of it from the new visit's history entry (tasks.updates).
        with patch('tasks.updates.push_in_their_language') as push, self.captureOnCommitCallbacks(execute=True):
            self._rebook(self.new_time, 'Customer confirmed it reopens')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)
        self.assertEqual(self.task.scheduled_for, self.new_time)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.RESCHEDULED).get().note,
                         'Customer confirmed it reopens')
        self.assertTrue(self.task.events.filter(event_type=TaskEvent.EventType.BLOCKED).exists())
        self.assertIn(self.technician, [call.args[0] for call in push.call_args_list])

    def test_a_time_in_the_past_is_refused(self):
        self.client.login(username='manager1', password='pass12345')
        self._rebook(self.new_time - timedelta(days=10))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.BLOCKED)

    def test_only_a_stopped_task_can_be_rebooked(self):
        self.task.status = Task.Status.ASSIGNED
        self.task.save(update_fields=['status'])
        self.client.login(username='manager1', password='pass12345')
        self._rebook(self.new_time)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.scheduled_for)

    def test_supervisor_cannot_rebook(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self._rebook(self.new_time).status_code, 403)


class TakenOffTaskTests(TaskTestCase):
    """Whoever is taken off a task hears about it, and a lead swapped
    mid-job keeps the hours the manager says they put in."""

    def setUp(self):
        super().setUp()
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.other = Technician.objects.create(
            user=User.objects.create_user('tech2', password='pass12345'), country=self.country,
            full_name='Rami Other', language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.IN_PROGRESS,
        )
        self.lead = TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        self.url = f'/tasks/{self.task.pk}/assign/'

    def test_replaced_lead_is_told_and_keeps_their_hours(self):
        self.client.login(username='manager1', password='pass12345')
        with patch('tasks.views.push_removed') as push:
            self.client.post(self.url, {
                'action': 'set_lead', 'technician': self.other.pk, 'end_reason': 'sick', 'hours_worked': '1.5',
            })
        push.assert_called_once_with(self.technician, self.task)
        self.lead.refresh_from_db()
        self.assertEqual(str(self.lead.labour_hours), '1.50')

    def test_removed_helper_is_told(self):
        helper = TaskAssignment.objects.create(
            task=self.task, technician=self.other, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        self.client.login(username='manager1', password='pass12345')
        with patch('tasks.views.push_removed') as push:
            self.client.post(self.url, {
                'action': 'remove_helper', 'assignment_id': helper.pk, 'end_reason': 'overloaded',
            })
        push.assert_called_once_with(self.other, self.task)

    def test_supervisor_never_gets_the_hours_box(self):
        self.technician.set_active(False, reason='Left')
        self.client.login(username='supervisor1', password='pass12345')
        self.assertTrue(self.client.get(self.url).context['can_set_lead'])
        self.assertNotIn('hours_worked', self.client.get(self.url).context['set_lead_form'].fields)

    def test_hours_box_only_when_replacing_a_lead_mid_job(self):
        self.client.login(username='manager1', password='pass12345')
        self.assertIn('hours_worked', self.client.get(self.url).context['set_lead_form'].fields)
        self.task.status = Task.Status.ASSIGNED
        self.task.save(update_fields=['status'])
        self.assertNotIn('hours_worked', self.client.get(self.url).context['set_lead_form'].fields)


class TappedHoursTests(TaskTestCase):
    """Typed hours checked against the app's own taps."""

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.COMPLETED,
        )
        self.start = timezone.now() - timedelta(hours=5)
        self._tap(TaskEvent.EventType.STARTED, 0)
        self._tap(TaskEvent.EventType.PAUSED, 1)
        self._tap(TaskEvent.EventType.RESUMED, 2)
        self._tap(TaskEvent.EventType.REPORT_SUBMITTED, 3)  # 3 h start to filed, 1 h paused

    def _tap(self, event_type, hours_after_start):
        TaskEvent.objects.create(
            task=self.task, event_type=event_type, actor=self.tech_user,
            occurred_at=self.start + timedelta(hours=hours_after_start),
        )

    def _report(self, hours):
        report = WorkReport.objects.create(
            task=self.task, findings='x', resolved=True, labour_hours=hours, customer_name='Ali',
            submitted_at=timezone.now(),
        )
        return WorkReport.objects.get(pk=report.pk)

    def test_tapped_time_leaves_out_pauses(self):
        self.assertEqual(str(self._report('2.00').tapped_hours), '2.00')

    def test_hours_well_above_the_taps_are_flagged(self):
        self.assertTrue(self._report('6.00').is_above_tapped)

    def test_a_managers_corrected_time_counts(self):
        started = self.task.events.get(event_type=TaskEvent.EventType.STARTED)
        started.corrected_at = started.occurred_at - timedelta(hours=4)
        started.save(update_fields=['corrected_at'])
        report = self._report('6.00')
        self.assertEqual(str(report.tapped_hours), '6.00')
        self.assertFalse(report.is_above_tapped)

    def test_a_little_over_or_under_is_not_flagged(self):
        self.assertFalse(self._report('2.50').is_above_tapped)
        self.task.report.delete()
        self.assertFalse(self._report('1.00').is_above_tapped)

    def test_no_start_tap_means_no_check(self):
        self.task.events.filter(event_type=TaskEvent.EventType.STARTED).delete()
        report = self._report('9.00')
        self.assertIsNone(report.tapped_hours)
        self.assertFalse(report.is_above_tapped)

    def test_helper_cannot_have_more_hours_than_the_job(self):
        from reports.forms import WorkReportForm

        helper = TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.HELPER,
            assigned_at=timezone.now(), is_active=True,
        )
        form = WorkReportForm({
            'findings': 'x', 'resolved': 'True', 'customer_name': 'Ali',
            f'helper_hours_{helper.pk}': '3',
        }, helpers=[helper], time_on_site=Decimal('2'))
        self.assertFalse(form.is_valid())
        self.assertIn(f'helper_hours_{helper.pk}', form.errors)


class StaffOpenedTicketTests(TaskTestCase):
    """Technical support and operations managers opening tickets: for a
    customer who phoned, or an internal task."""

    def setUp(self):
        super().setUp()
        self.support = self._staff('support1', Technician.Role.SUPPORT_MANAGER)
        self.ops = self._staff('ops1', Technician.Role.OPERATIONS_MANAGER)
        self.warehouse = self._staff('warehouse1', Technician.Role.WAREHOUSE_MANAGER)
        self.site.contact_name, self.site.contact_phone = 'Ali Manager', '0501234567'
        self.site.save()

    def _staff(self, username, role):
        return Technician.objects.create(
            user=User.objects.create_user(username, password='pass12345'), country=self.country,
            full_name=username.title(), language='en', role=role, employment_type='staff',
        )

    def _customer_ticket(self, **extra):
        return self.client.post('/tasks/tickets/open/', {
            'site': self.site.pk, 'source': 'phone', 'description': 'Treadmill belt slipping', **extra,
        })

    def test_support_manager_opens_a_ticket_for_a_customer_who_phoned(self):
        self.client.login(username='support1', password='pass12345')
        response = self._customer_ticket()
        ticket = CustomerTicket.objects.get()
        self.assertRedirects(response, f'/tasks/tickets/{ticket.pk}/', fetch_redirect_response=False)
        self.assertEqual(ticket.kind, 'customer')
        self.assertEqual(ticket.source, 'phone')
        self.assertEqual(ticket.customer, self.customer)
        self.assertEqual(ticket.opened_by, self.support.user)
        # Contact left empty: the site's own is used.
        self.assertEqual(ticket.contact_name, 'Ali Manager')
        self.assertTrue(TicketNotification.objects.filter(ticket=ticket, kind='new_ticket').exists())
        # A task made from it keeps the channel.
        self._staff('manager1', Technician.Role.MANAGER)
        self.client.login(username='manager1', password='pass12345')
        form = self.client.get(f'/tasks/new/?ticket={ticket.pk}').context['form']
        self.assertEqual(form['source'].initial, 'phone')

    def test_operations_manager_can_open_one_too(self):
        self.client.login(username='ops1', password='pass12345')
        response = self._customer_ticket()
        ticket = CustomerTicket.objects.get()
        # They're on the ticket desk too, so they land on its review page.
        self.assertRedirects(response, f'/tasks/tickets/{ticket.pk}/', fetch_redirect_response=False)
        self.assertEqual(ticket.opened_by, self.ops.user)

    def test_a_caller_who_is_not_a_customer_yet(self):
        # The Technical Support Manager, on the phone: New ticket → New
        # customer (with its first branch, invited) → back to the ticket with
        # that branch chosen → the ticket.
        self.client.login(username='support1', password='pass12345')
        self.assertContains(self.client.get('/tasks/tickets/open/'), '/customers/new/?next=ticket')
        response = self.client.post('/customers/new/?next=ticket', {
            'name': 'Iron Den Gym', 'segment': 'gym', 'branch_name': 'Iron Den Main', 'branch_address': 'Al Barsha',
            'contact_name': 'Omar', 'contact_phone': '0509998877', 'contact_email': 'omar@irondengym.example',
            'invite': 'on',
        })
        customer = Customer.objects.get(name='Iron Den Gym')
        site = customer.sites.get()
        self.assertEqual(customer.country, self.country)
        self.assertRedirects(response, f'/tasks/tickets/open/?site={site.pk}', fetch_redirect_response=False)
        self.assertEqual(self.client.get(response.url).context['form']['site'].value(), str(site.pk))
        self.assertEqual(customer.portal_users.get().user.username, 'omar@irondengym.example')  # invited

        response = self._customer_ticket(site=site.pk, description='Rower display is dead')
        ticket = CustomerTicket.objects.get(customer=customer)
        self.assertRedirects(response, f'/tasks/tickets/{ticket.pk}/', fetch_redirect_response=False)
        self.assertEqual((ticket.site, ticket.kind, ticket.opened_by), (site, 'customer', self.support.user))
        self.assertRegex(ticket.ticket_number, r'-T0001$')
        # The customer's contact is the branch's, and gets the confirmation too.
        self.assertEqual(ticket.contact_name, 'Omar')
        self.assertEqual(sum('omar@irondengym.example' in message.to for message in mail.outbox), 2)

    def test_a_supervisor_cannot_create_a_customer(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get('/customers/new/').status_code, 403)

    @override_settings(PORTAL_HOST='')  # the portal on this same address, not its own
    def test_the_customer_sees_it_in_their_portal_and_gets_the_email(self):
        portal_user = get_user_model().objects.create_user('fitnessportal', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user)
        self.client.login(username='support1', password='pass12345')
        self._customer_ticket(contact_email='ali@example.com')
        ticket = CustomerTicket.objects.get()
        self.assertTrue(any('ali@example.com' in message.to for message in mail.outbox))

        self.client.logout()
        self.client.login(username='fitnessportal', password='pass12345')
        self.assertContains(self.client.get('/customers/portal/requests/'), ticket.ticket_number)
        # Their request page, where they follow it and reply.
        self.assertEqual(self.client.get(f'/tasks/tickets/status/{ticket.token}/').status_code, 200)

    def test_supervisor_cannot_open_a_customer_ticket(self):
        # A customer's ticket stays with open_tickets; a supervisor has the
        # New internal task page instead. Nothing is created.
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self._customer_ticket().status_code, 403)
        self.assertFalse(CustomerTicket.objects.exists())

    def _stock_request(self):
        self.client.login(username='support1', password='pass12345')
        with patch('tasks.views.push_in_their_language') as push:
            self.client.post('/tasks/tickets/internal/new/', {
                'subject': 'Need 10 running belts', 'description': 'For the Marina jobs',
                'assigned_to': self.warehouse.pk,
            })
        push.assert_called_once()
        return CustomerTicket.objects.get()

    def test_internal_ticket_goes_to_whoever_it_is_for(self):
        ticket = self._stock_request()
        self.assertEqual(ticket.kind, 'internal')
        self.assertEqual(ticket.assigned_to, self.warehouse)

        self.client.login(username='warehouse1', password='pass12345')
        listed = self.client.get('/tasks/tickets/internal/').context['tickets']
        self.assertEqual(list(listed), [ticket])
        bell = self.client.get('/').context['unseen_notifications']
        self.assertTrue(any(item['kind'] == 'internal_ticket' for item in bell))

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.client.post(f'/tasks/tickets/internal/{ticket.pk}/', {
                'action': 'add_note', 'message': 'Ordered',
                'attachments': [SimpleUploadedFile('po.pdf', b'%PDF-1.4', content_type='application/pdf')],
            })
            self.assertContains(self.client.get(f'/tasks/tickets/internal/{ticket.pk}/'), '>po.pdf</a>')
        self.client.post(f'/tasks/tickets/internal/{ticket.pk}/', {'action': 'close', 'close_reason': 'Delivered'})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'closed')
        self.assertEqual(ticket.internal_notes.get().message, 'Ordered')

    def test_internal_tickets_stay_out_of_customer_lists(self):
        ticket = self._stock_request()
        self.assertNotIn(ticket, self.client.get('/tasks/tickets/?status=all').context['tickets'])
        response = self.client.get(f'/tasks/tickets/{ticket.pk}/')
        self.assertRedirects(response, f'/tasks/tickets/internal/{ticket.pk}/', fetch_redirect_response=False)

    def test_someone_not_involved_cannot_see_it(self):
        ticket = self._stock_request()
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(f'/tasks/tickets/internal/{ticket.pk}/').status_code, 404)

    def test_every_manager_sees_every_internal_ticket(self):
        ticket = self._stock_request()
        self._staff('manager1', Technician.Role.MANAGER)
        for username in ('manager1', 'ops1'):
            with self.subTest(username=username):
                self.client.login(username=username, password='pass12345')
                self.assertIn(ticket, self.client.get('/tasks/tickets/internal/?status=all').context['tickets'])
                self.assertEqual(self.client.get(f'/tasks/tickets/internal/{ticket.pk}/').status_code, 200)

    def test_warehouse_manager_opens_internal_but_not_customer_tickets(self):
        self.client.login(username='warehouse1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/tickets/open/').status_code, 403)

        with patch('tasks.views.push_in_their_language'):
            self.client.post('/tasks/tickets/internal/new/', {
                'subject': 'Belts for Marina', 'description': 'Ten of them', 'assigned_to': self.support.pk,
            })
        ticket = CustomerTicket.objects.get()
        self.assertEqual(ticket.kind, 'internal')
        self.assertEqual(ticket.opened_by, self.warehouse.user)

    def test_any_manager_converts_an_internal_ticket_into_a_task(self):
        ticket = self._stock_request()
        self.client.login(username='warehouse1', password='pass12345')
        page = self.client.get(f'/tasks/tickets/internal/{ticket.pk}/')
        self.assertContains(page, f'/tasks/new/?ticket={ticket.pk}')

        create_url = f'/tasks/new/?ticket={ticket.pk}'
        form = self.client.get(create_url).context['form']
        self.assertEqual(form['source'].initial, Task.Source.INTERNAL)
        response = self.client.post(create_url, {
            'site': self.site.pk,
            'priority': Task.Priority.NORMAL, 'source': Task.Source.INTERNAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Fit the new belts.',
        })
        self.assertEqual(response.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, CustomerTicket.Status.CONVERTED)
        self.assertEqual(ticket.task.site, self.site)
        # Stays internal — never linked to a customer's portal.
        self.assertIsNone(ticket.customer)
        self.assertNotContains(self.client.get(f'/tasks/tickets/internal/{ticket.pk}/'), 'Convert to task')

    def test_supervisor_cannot_convert_an_internal_ticket(self):
        ticket = self._stock_request()
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(f'/tasks/new/?ticket={ticket.pk}').status_code, 404)


class TodayTestsMixin:
    def _make_task(self, number, *, lead=None, lead_active=True, priority=Task.Priority.NORMAL, **fields):
        task = Task.objects.create(
            task_number=number, site=self.site, task_type=self.task_type, description='Belt noise',
            priority=priority, source=Task.Source.PHONE, billing_type=Task.BillingType.CONTRACT,
            reported_at=fields.pop('reported_at', timezone.now()), promised_at=timezone.now(),
            created_by=self.supervisor_user, status=fields.pop('status', Task.Status.ASSIGNED), **fields,
        )
        if lead is not None:
            TaskAssignment.objects.create(
                task=task, technician=lead, role=TaskAssignment.Role.LEAD,
                assigned_at=timezone.now(), is_active=lead_active,
            )
        return task


class HomeSummaryTests(TodayTestsMixin, TaskTestCase):
    # The home page (spots.views.home -> tasks.views.home_summary): counts
    # of what's waiting on you, each linking to its list, then today's visits.

    def test_supervisor_sees_unassigned_and_unscheduled_counts_with_links(self):
        self._make_task('UAE-0101')
        self._make_task('UAE-0102', lead=self.technician, scheduled_for=timezone.now())
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/')
        actions = {action['url']: action['count'] for action in response.context['actions']}
        self.assertEqual(actions['/tasks/?lead=none'], 1)
        self.assertEqual(actions['/tasks/?scheduled=none'], 1)
        self.assertContains(response, 'href="/tasks/?lead=none"')

    def test_nothing_waiting_shows_an_empty_message(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/')
        self.assertEqual(response.context['actions'], [])
        self.assertContains(response, 'There is nothing awaiting your action.')

    def test_technician_sees_their_visit_today_and_no_action_box(self):
        task = self._make_task('UAE-0103', lead=self.technician, scheduled_for=timezone.now())
        self._make_task('UAE-0104', lead=self.technician, scheduled_for=timezone.now() + timedelta(days=3))
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/')
        self.assertEqual(response.context['visits'], [task])
        self.assertFalse(response.context['show_actions'])
        self.assertContains(response, f'/tasks/my/{task.pk}/')

    def test_replaced_lead_does_not_count_as_a_visit(self):
        self._make_task('UAE-0105', lead=self.technician, lead_active=False, scheduled_for=timezone.now())
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/').context['visits'], [])


class TaskListQuickFilterTests(TodayTestsMixin, TaskTestCase):
    # The ?priority= / ?lead=none / ?scheduled=none links from the dashboard.

    def setUp(self):
        super().setUp()
        self.client.login(username='supervisor1', password='pass12345')

    def _numbers(self, query):
        response = self.client.get(f'/tasks/?{query}')
        return sorted(task.task_number for task in response.context['page_obj'])

    def test_unassigned_includes_a_task_whose_only_lead_was_replaced(self):
        self._make_task('UAE-0201')
        self._make_task('UAE-0202', lead=self.technician)
        self._make_task('UAE-0203', lead=self.technician, lead_active=False)
        self.assertEqual(self._numbers('lead=none'), ['UAE-0201', 'UAE-0203'])

    def test_priority_and_unscheduled_filters(self):
        self._make_task('UAE-0204', priority=Task.Priority.EMERGENCY)
        self._make_task('UAE-0205', scheduled_for=timezone.now())
        self.assertEqual(self._numbers('priority=emergency'), ['UAE-0204'])
        self.assertEqual(self._numbers('scheduled=none'), ['UAE-0204'])

    def test_quick_filter_is_named_above_the_list(self):
        response = self.client.get('/tasks/?lead=none')
        self.assertContains(response, 'Filtered by:')
        self.assertContains(response, '<input type="hidden" name="lead" value="none">', html=True)


class DashboardTileTests(DashboardManagerMixin, TodayTestsMixin, TaskTestCase):
    def test_tiles_link_to_the_filtered_lists_and_urgent_rows_come_first(self):
        self._make_task('UAE-0301', lead=self.technician, scheduled_for=timezone.now())
        self._make_task('UAE-0302', priority=Task.Priority.EMERGENCY, lead=self.technician,
                        scheduled_for=timezone.now())
        self._make_task('UAE-0303', scheduled_for=timezone.now())
        self._login_manager()
        response = self.client.get('/tasks/dashboard/')
        self.assertContains(response, 'href="/tasks/?priority=emergency"')
        self.assertContains(response, 'href="/tasks/?lead=none"')
        numbers = [task.task_number for task in response.context['tasks']]
        self.assertEqual(numbers[:2], ['UAE-0302', 'UAE-0303'])

    def test_a_role_without_the_task_list_sees_numbers_but_no_links(self):
        self._make_task('UAE-0304')
        self._login_manager()
        RolePermission.objects.filter(role=Technician.Role.MANAGER, permission='view_tasks').update(allowed=False)
        response = self.client.get('/tasks/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'href="/tasks/?lead=none"')


# Midday in Dubai, so "an hour ago" is still today in the test country
# whatever time the tests run (the page uses the user's country's day).
TEAM_MAP_NOW = datetime(2026, 10, 22, 8, 0, tzinfo=ZoneInfo('UTC'))


class TeamMapTests(TodayTestsMixin, TaskTestCase):
    # Last tap per technician today, plus what needs a follow-up — built
    # only from the location taken at each tap.

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=TEAM_MAP_NOW)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.site.latitude, self.site.longitude = Decimal('25.080000'), Decimal('55.140000')
        self.site.save(update_fields=['latitude', 'longitude'])
        self.client.login(username='supervisor1', password='pass12345')

    def _tap(self, task, event_type, latitude, longitude, **fields):
        return record_tap(task, event_type, self.tech_user, {'latitude': latitude, 'longitude': longitude}, **fields)

    def test_last_tap_today_is_the_pin(self):
        task = self._make_task('UAE-0401', lead=self.technician, status=Task.Status.IN_PROGRESS)
        self._tap(task, TaskEvent.EventType.ARRIVED, '25.0801', '55.1401',
                  occurred_at=timezone.now() - timedelta(minutes=5))
        self._tap(task, TaskEvent.EventType.STARTED, '25.0802', '55.1402')
        response = self.client.get('/tasks/technicians/map/')
        self.assertEqual(response.status_code, 200)
        [pin] = response.context['pins']
        self.assertEqual((pin['name'], pin['lat'], pin['away']), ('Tarek Tech', 25.0802, False))
        self.assertIn('Started', pin['detail'])

    def test_no_tap_today_means_no_pin(self):
        response = self.client.get('/tasks/technicians/map/')
        self.assertEqual(response.context['pins'], [])
        self.assertContains(response, 'No check-ins today')

    def test_a_late_arrival_is_flagged_until_they_arrive(self):
        task = self._make_task('UAE-0402', lead=self.technician, scheduled_for=timezone.now() - timedelta(hours=1))
        texts = [item['text'] for item in self.client.get('/tasks/technicians/map/').context['attention']]
        self.assertTrue(any('has not yet arrived' in text for text in texts))
        self._tap(task, TaskEvent.EventType.ARRIVED, '25.0801', '55.1401')
        texts = [item['text'] for item in self.client.get('/tasks/technicians/map/').context['attention']]
        self.assertFalse(any('has not yet arrived' in text for text in texts))

    def test_a_tap_far_from_the_site_is_flagged(self):
        task = self._make_task('UAE-0403', lead=self.technician, status=Task.Status.IN_PROGRESS)
        self._tap(task, TaskEvent.EventType.STARTED, '25.2000', '55.3000')
        response = self.client.get('/tasks/technicians/map/')
        self.assertTrue(response.context['pins'][0]['away'])
        self.assertTrue(any('from Marina Branch' in item['text'] for item in response.context['attention']))

    def test_a_job_past_its_estimate_is_flagged(self):
        task = self._make_task('UAE-0404', lead=self.technician, status=Task.Status.IN_PROGRESS,
                               estimated_hours=Decimal('1.00'))
        self._tap(task, TaskEvent.EventType.STARTED, '25.0801', '55.1401',
                  occurred_at=timezone.now() - timedelta(hours=2))
        texts = [item['text'] for item in self.client.get('/tasks/technicians/map/').context['attention']]
        self.assertTrue(any('exceeded its estimate of 1.0 hours' in text for text in texts))

    def test_technician_cannot_open_it(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/technicians/map/').status_code, 403)


class StaffPasswordResetLinkTests(TaskTestCase):
    # Django's emailed "forgot password" link (spots.views.PasswordResetConfirmView).

    def test_new_password_from_the_link_keeps_the_app_signed_in(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        Token.objects.create(user=self.tech_user)
        uid = urlsafe_base64_encode(force_bytes(self.tech_user.pk))
        token = default_token_generator.make_token(self.tech_user)
        # Django swaps the token for "set-password" in the URL on the first visit.
        response = self.client.get(f'/accounts/reset/{uid}/{token}/', follow=True)
        self.assertContains(response, 'At least 8 characters')
        response = self.client.post(response.redirect_chain[-1][0], {
            'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertRedirects(response, '/accounts/reset/done/')
        self.tech_user.refresh_from_db()
        self.assertTrue(self.tech_user.check_password('br4nd-New-Pass!'))
        self.assertTrue(Token.objects.filter(user=self.tech_user).exists())


class StaffAccountsTests(TaskTestCase):
    # Staff accounts (tasks.staff_accounts): every staff login and the fix
    # for each problem, for the manager tier, every action in the Audit log.

    url = '/tasks/technicians/accounts/'

    def setUp(self):
        super().setUp()
        self.manager = self._staff('manager1', Technician.Role.MANAGER)
        self.admin = self._staff('admin1', Technician.Role.ADMIN)

    def _staff(self, username, role):
        return Technician.objects.create(
            user=User.objects.create_user(username, password='pass12345', email=f'{username}@example.com'),
            country=self.country, full_name=username.title(), language='en', role=role, employment_type='staff',
        )

    def _act(self, action, person, **extra):
        return self.client.post(self.url, {'action': action, 'technician': person.pk, **extra})

    def _lock(self, username, ip='10.0.0.7'):
        from axes.models import AccessAttempt
        return AccessAttempt.objects.create(
            username=username, ip_address=ip, user_agent='test', http_accept='', path_info='/accounts/login/',
            get_data='', post_data='', failures_since_start=5, attempt_time=timezone.now(),
        )

    def _statuses(self):
        return {row['person'].full_name: row['status'] for row in self.client.get(self.url).context['page_obj']}

    def test_only_the_manager_tier_can_open_it(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self._statuses()['Tarek Tech'], 'active')

    def test_temporary_password_is_shown_once_signs_out_phones_and_is_logged(self):
        from django.contrib.admin.models import LogEntry
        Token.objects.create(user=self.tech_user)
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.ADMIN)  # the Admin's alone
        self.client.login(username='manager1', password='pass12345')
        response = self._act('temporary_password', self.technician)
        self.assertTemplateUsed(response, 'tasks/staff_account_password.html')
        self.tech_user.refresh_from_db()
        self.assertTrue(self.tech_user.check_password(response.context['password']))
        self.technician.refresh_from_db()
        self.assertTrue(self.technician.must_change_password)
        self.assertFalse(Token.objects.filter(user=self.tech_user).exists())
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.technician.pk), change_message__icontains='temporary password',
        ).exists())

    def test_unlock_clears_only_that_login(self):
        from axes.models import AccessAttempt
        self._lock('tech1')
        self._lock('someone-else')
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self._statuses()['Tarek Tech'], 'locked')
        self._act('unlock', self.technician)
        self.assertEqual(list(AccessAttempt.objects.values_list('username', flat=True)), ['someone-else'])

    def test_locked_staff_show_in_the_managers_bell(self):
        self._lock('tech1')
        self.client.login(username='manager1', password='pass12345')
        kinds = [item['kind'] for item in self.client.get(self.url).context['unseen_notifications']]
        self.assertIn('staff_locked', kinds)

    def test_turn_a_login_off_and_on(self):
        self.client.login(username='manager1', password='pass12345')
        self._act('disable', self.technician)
        self.tech_user.refresh_from_db()
        self.assertFalse(self.tech_user.is_active)
        self._act('enable', self.technician)
        self.tech_user.refresh_from_db()
        self.assertTrue(self.tech_user.is_active)

    def test_the_last_admin_cannot_turn_off_their_own_login(self):
        self.client.login(username='admin1', password='pass12345')
        self._act('disable', self.admin)
        self.admin.user.refresh_from_db()
        self.assertTrue(self.admin.user.is_active)

    def test_a_manager_cannot_touch_an_admin_account(self):
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self._act('temporary_password', self.admin).status_code, 403)
        self.assertEqual(self._act('change_email', self.admin, email='other@example.com').status_code, 403)

    def test_a_manager_sets_no_password_and_changes_no_email(self):
        self.tech_user.email = 'tarek@example.com'
        self.tech_user.save(update_fields=['email'])
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self._act('temporary_password', self.technician).status_code, 403)
        self.assertEqual(self._act('change_email', self.technician, email='x@example.com').status_code, 403)
        page = self.client.get(f'/tasks/technicians/accounts/?manage={self.technician.pk}')
        self.assertNotContains(page, 'value="temporary_password"')
        self.assertNotContains(page, 'value="change_email"')
        # What they can still do: a setup link, to the person's own email.
        self.assertContains(page, 'value="send_setup_link"')

    def test_change_email_keeps_a_sign_in_name_that_is_not_an_email(self):
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.ADMIN)  # the Admin's alone
        self.client.login(username='manager1', password='pass12345')
        self._act('change_email', self.technician, email='tarek.new@example.com')
        self.tech_user.refresh_from_db()
        self.assertEqual((self.tech_user.username, self.tech_user.email), ('tech1', 'tarek.new@example.com'))


class AccountTakeoverGuardTests(TaskTestCase):
    # On a technician's edit page nobody below someone can change their
    # account, and the last admin stays.

    def setUp(self):
        super().setUp()
        self.manager = Technician.objects.create(
            user=User.objects.create_user('manager1', password='pass12345'), country=self.country,
            full_name='Mona Manager', language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.admin = Technician.objects.create(
            user=User.objects.create_user('admin1', password='pass12345'), country=self.country,
            full_name='Adam Admin', language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

    def test_a_supervisor_cannot_change_a_managers_record(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/technicians/{self.manager.pk}/edit/', {
            'full_name': 'Mona Manager', 'email': 'attacker@example.com', 'language': 'en',
        })
        # Someone above you isn't on your list at all (overseen_roles).
        self.assertEqual(response.status_code, 404)

    def test_a_manager_gets_no_password_reset_for_an_admin(self):
        self.client.login(username='manager1', password='pass12345')
        # A manager doesn't oversee an admin: neither the page nor a reset is reachable.
        self.assertEqual(self.client.get(f'/tasks/technicians/{self.admin.pk}/edit/').status_code, 404)
        response = self.client.post(f'/tasks/technicians/{self.admin.pk}/edit/', {
            'action': 'reset_password', 'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertEqual(response.status_code, 404)

    def test_the_last_admin_cannot_be_deactivated(self):
        self.client.login(username='admin1', password='pass12345')
        self.client.post(f'/tasks/technicians/{self.admin.pk}/edit/', {'action': 'deactivate', 'reason': 'left_company'})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class PasswordChangedEmailTests(TaskTestCase):
    # tasks.views.send_password_changed_email: so someone notices a change
    # they did not make.

    def setUp(self):
        super().setUp()
        self.tech_user.email = 'tarek@example.com'
        self.tech_user.save(update_fields=['email'])
        Technician.objects.create(
            user=User.objects.create_user('manager1', password='pass12345'), country=self.country,
            full_name='Mona Manager', language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

    def test_changing_your_own_password_emails_you(self):
        self.client.login(username='tech1', password='pass12345')
        self.client.post('/tasks/my-profile/', {
            'action': 'change_password', 'old_password': 'pass12345',
            'new_password1': 'new-pass-98765', 'new_password2': 'new-pass-98765',
        })
        self.assertEqual([message.to for message in mail.outbox], [['tarek@example.com']])
        self.assertIn('contact your manager', mail.outbox[0].body)

    def test_a_temporary_password_from_staff_accounts_emails_them(self):
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.ADMIN)  # the Admin's alone
        self.client.login(username='manager1', password='pass12345')
        self.client.post('/tasks/technicians/accounts/', {'action': 'temporary_password', 'technician': self.technician.pk})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('temporary password', mail.outbox[0].body)
        # The password itself is never in the email.
        self.tech_user.refresh_from_db()
        self.assertNotIn('Temporary password:', mail.outbox[0].body)

    def test_first_setup_in_the_app_sends_nothing(self):
        import json
        self.technician.must_change_password = True
        self.technician.save(update_fields=['must_change_password'])
        token = self.client.post(
            '/api/login/', json.dumps({'username': 'tech1', 'password': 'pass12345'}), content_type='application/json',
        ).json()['token']
        self.client.post(
            '/api/me/password/', json.dumps({'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(mail.outbox, [])


class StaffSetupLinkTests(TaskTestCase):
    # One-time setup links for staff logins (tasks.views.send_staff_setup_link,
    # tasks.staff_accounts.staff_setup) and resending them from Staff accounts.

    def setUp(self):
        super().setUp()
        Technician.objects.create(
            user=User.objects.create_user('manager1', password='pass12345'), country=self.country,
            full_name='Mona Manager', language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.newcomer = Technician.objects.create(
            country=self.country, full_name='Hani Helper', language='en',
            role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        self.client.post(f'/tasks/technicians/{self.newcomer.pk}/edit/', {'action': 'create_login', 'email': 'hani@example.com'})
        self.newcomer.refresh_from_db()
        self.client.logout()

    def _link(self, message=None):
        import re
        body = (message or mail.outbox[-1]).body
        return re.search(r'http://testserver(/tasks/setup/[^/\s]+/)', body).group(1)

    def _statuses(self):
        response = self.client.get('/tasks/technicians/accounts/')
        return {row['person'].full_name: row['status'] for row in response.context['page_obj']}

    def test_the_link_sets_a_password_signs_them_in_and_works_once(self):
        link = self._link()
        self.assertContains(self.client.get(link), 'Set your password')
        response = self.client.post(link, {'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.newcomer.refresh_from_db()
        self.assertTrue(self.newcomer.user.check_password('br4nd-New-Pass!'))
        self.assertFalse(self.newcomer.must_change_password)
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 400)

    def test_shows_as_sent_then_expired_and_a_resend_gives_a_fresh_link(self):
        self.client.login(username='manager1', password='pass12345')
        self.assertEqual(self._statuses()['Hani Helper'], 'invited')
        Technician.objects.filter(pk=self.newcomer.pk).update(setup_link_sent_at=timezone.now() - timedelta(days=8))
        self.assertEqual(self._statuses()['Hani Helper'], 'expired')
        self.client.post('/tasks/technicians/accounts/', {'action': 'send_setup_link', 'technician': self.newcomer.pk})
        self.assertEqual(self._statuses()['Hani Helper'], 'invited')
        self.assertEqual(len(mail.outbox), 2)
        self.client.logout()
        self.assertEqual(self.client.get(self._link()).status_code, 200)

    def test_a_temporary_password_kills_any_earlier_link(self):
        link = self._link()
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.ADMIN)  # the Admin's alone
        self.client.login(username='manager1', password='pass12345')
        self.client.post('/tasks/technicians/accounts/', {'action': 'temporary_password', 'technician': self.newcomer.pk})
        self.assertEqual(self._statuses()['Hani Helper'], 'temporary')
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 400)

    def test_a_tampered_link_does_nothing(self):
        self.assertEqual(self.client.get('/tasks/setup/not-a-real-token/').status_code, 400)


class VisibleLinksTests(TaskTestCase):
    # Nobody is shown a link they can't use: for every role (and a
    # customer), follow every link reachable from the home page and fail
    # on any that's refused, missing, or bounces to sign-in.

    MAX_PAGES = 150
    SKIP_PREFIXES = ('/static/', '/media/', '/accounts/logout/', '/i18n/')

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='UAE-0901', site=self.site, task_type=self.task_type, description='Belt noise',
            priority=Task.Priority.HIGH, source=Task.Source.PHONE, billing_type=Task.BillingType.CONTRACT,
            reported_at=timezone.now(), promised_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.IN_PROGRESS, scheduled_for=timezone.now(),
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0901', company_name='Fitness First',
            site_description='Marina Branch', contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )

    def _staff(self, username, role):
        Technician.objects.create(
            user=User.objects.create_user(username, password='pass12345'), country=self.country,
            full_name=username.title(), language='en', role=role, employment_type='staff',
        )
        return username

    def _links(self, html):
        from html.parser import HTMLParser

        found = []

        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                href = dict(attrs).get('href')
                if tag == 'a' and href:
                    found.append(href)

        Parser().feed(html)
        return found

    def _crawl(self, username):
        from urllib.parse import urldefrag

        self.client.logout()
        self.client.login(username=username, password='pass12345')
        seen, queue, problems = set(), [('/', None)], []
        while queue and len(seen) < self.MAX_PAGES:
            url, found_on = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            response = self.client.get(url)
            if response.status_code in (403, 404):
                problems.append(f'{url} -> {response.status_code} (linked from {found_on})')
                continue
            if response.status_code == 302:
                target = response['Location']
                if '/login' in target:
                    problems.append(f'{url} -> sign-in (linked from {found_on})')
                elif target.startswith('/'):
                    queue.append((target, found_on))
                continue
            if 'text/html' not in response.get('Content-Type', ''):
                continue
            for href in self._links(response.content.decode()):
                href = urldefrag(href)[0]
                if href.startswith('/') and not href.startswith(self.SKIP_PREFIXES) and href not in seen:
                    queue.append((href, url))
        return problems

    def test_every_visible_link_opens_for_every_role(self):
        users = ['tech1', 'supervisor1'] + [
            self._staff(f'{role}1', role) for role in (
                Technician.Role.SUPPORT_MANAGER, Technician.Role.COUNTRY_SUPPORT,
                Technician.Role.WAREHOUSE_MANAGER, Technician.Role.OPERATIONS_MANAGER,
                Technician.Role.MANAGER, Technician.Role.ADMIN,
            )
        ]
        portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user)
        users.append('fitnessfirst')

        problems = {username: self._crawl(username) for username in users}
        problems = {username: found for username, found in problems.items() if found}
        self.assertEqual(problems, {}, '\n' + '\n'.join(
            f'{username}: {item}' for username, found in problems.items() for item in found
        ))


class SidebarPerUserTests(TaskTestCase):
    # The sidebar shows only someone's own pages, marks where they are, and
    # isn't there at all for customers; a forbidden page looks like a
    # missing one.

    def test_my_profile_lights_up_the_profile_row_not_tasks(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertTrue(response.context['profile_active'])
        active = [page['label'] for group in response.context['nav_groups'] for page in group['pages'] if page['active']]
        self.assertEqual(active, [])
        self.assertContains(response, 'site-header__profile-link--active')

    def test_a_task_page_still_lights_up_tasks(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/')
        active = [page['url'] for group in response.context['nav_groups'] for page in group['pages'] if page['active']]
        self.assertEqual(active, ['/tasks/'])

    def test_customers_get_the_same_sidebar_with_their_own_pages(self):
        portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=portal_user)
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/customers/portal/')
        self.assertContains(response, 'site-header__nav"')
        self.assertContains(response, 'nav-toggle')
        self.assertNotContains(response, 'site-header__links')
        self.assertEqual([item['label'] for item in response.context['portal_nav']],
                         ['Home', 'Requests', 'Visits', 'Reports', 'Machines', 'Settings'])
        self.assertContains(response, 'aria-current="page"')
        # None of the staff pages.
        self.assertNotContains(response, f'href="{reverse("tasks:my_week")}"')

    def test_a_forbidden_page_looks_like_a_missing_one(self):
        self.client.login(username='tech1', password='pass12345')
        self.technician.language = 'en'
        self.technician.save(update_fields=['language'])
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Page not found', status_code=403)
        self.assertNotContains(response, 'Not allowed', status_code=403)


class DashboardWeeklyChartTests(DashboardManagerMixin, TodayTestsMixin, TaskTestCase):
    # The dashboard's opened-vs-completed chart: tasks reported per local
    # week, and tasks whose first work report was submitted that week.

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=TEAM_MAP_NOW)  # a Thursday
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()

    def test_counts_opened_and_completed_by_week(self):
        this_week = self._make_task('UAE-0501', reported_at=TEAM_MAP_NOW)
        older = self._make_task('UAE-0502', reported_at=TEAM_MAP_NOW - timedelta(weeks=2))
        self._make_task('UAE-0503', reported_at=TEAM_MAP_NOW - timedelta(weeks=20))
        for when in (TEAM_MAP_NOW, TEAM_MAP_NOW + timedelta(hours=1)):  # only the first report counts
            TaskEvent.objects.create(
                task=older, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=when, actor=self.tech_user,
            )
        self._login_manager()
        weekly = self.client.get('/tasks/dashboard/', {'period': 'week'}).context['tasks_chart']
        weeks = weekly['periods']
        self.assertEqual(len(weeks), 8)
        self.assertTrue(weeks[-1]['is_current'])
        self.assertEqual((weeks[-1]['opened'], weeks[-1]['completed']), (1, 1))
        self.assertEqual(weeks[-3]['opened'], 1)
        self.assertEqual(sum(week['opened'] for week in weeks), 2)
        self.assertEqual(weekly['top'], 4)
        self.assertEqual(weeks[-1]['opened_pct'], 25)
        self.assertIsNotNone(this_week)

    def test_tickets_chart_only_for_people_who_handle_tickets(self):
        self._login_manager()
        self.assertIsNone(self.client.get('/tasks/dashboard/').context['tickets_chart'])

        Technician.objects.create(
            user=User.objects.create_user('ops1', password='pass12345'), country=self.country,
            full_name='Ola Ops', language='en', role=Technician.Role.OPERATIONS_MANAGER, employment_type='staff',
        )
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0501', company_name='Fitness First', site_description='Marina',
            contact_name='Ali', contact_phone='0501234567', description='Squeak', submitted_at=TEAM_MAP_NOW,
            status=CustomerTicket.Status.CLOSED, reviewed_at=TEAM_MAP_NOW,
        )
        self.client.login(username='ops1', password='pass12345')
        response = self.client.get('/tasks/dashboard/', {'period': 'week'})
        week = response.context['tickets_chart']['periods'][-1]
        self.assertEqual((week['opened'], week['completed']), (1, 1))
        self.assertContains(response, 'Tickets received and resolved per week')

    def test_monthly_is_the_default_and_covers_twelve_months(self):
        self._make_task('UAE-0601', reported_at=TEAM_MAP_NOW)
        self._make_task('UAE-0602', reported_at=TEAM_MAP_NOW - timedelta(days=40))
        self._login_manager()
        chart = self.client.get('/tasks/dashboard/').context['tasks_chart']
        self.assertEqual(chart['period'], 'month')
        months = chart['periods']
        self.assertEqual(len(months), 12)
        self.assertEqual((months[-1]['start'], months[0]['start']), (date(2026, 10, 1), date(2025, 11, 1)))
        self.assertEqual((months[-1]['opened'], months[-2]['opened']), (1, 1))

    def test_chart_has_a_legend_and_a_table_view(self):
        self._login_manager()
        response = self.client.get('/tasks/dashboard/')
        self.assertContains(response, 'chart-legend')
        self.assertContains(response, 'Show as table')


class TaskReportPdfTests(TodayTestsMixin, TaskTestCase):
    # The visit report as a PDF on the letterhead (reports.pdf), from the task page.

    def setUp(self):
        super().setUp()
        self.task = self._make_task('UAE-0701', lead=self.technician, status=Task.Status.CLOSED,
                                    scheduled_for=timezone.now())
        self.report = WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt\nTested the treadmill',
            resolved=True, labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/{self.task.pk}/report.pdf'

    def test_staff_get_a_pdf_in_either_language(self):
        self.client.login(username='supervisor1', password='pass12345')
        for lang in ('en', 'ar'):
            response = self.client.get(self.url, {'lang': lang})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertTrue(response.content.startswith(b'%PDF'))
            self.assertIn('UAE-0701-visit-report.pdf', response['Content-Disposition'])

    def test_a_technician_cannot_download_it(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_no_report_no_pdf(self):
        self.report.delete()
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_pdf_never_fetches_anything_outside_the_app(self):
        from reports.pdf import _local_file
        self.assertIsNone(_local_file('http://example.com/evil.png'))
        self.assertIsNone(_local_file('http://localhost/media/../../etc/passwd'))
        self.assertIsNotNone(_local_file('/static/reports/letterhead-header.png'))


class TicketMachineDecisionTests(TaskTestCase):
    # The manager decides each machine on a portal ticket: a visit or a
    # part shipment task for some, advice or "not covered" for others, and
    # can split one ticket into several tasks.

    def setUp(self):
        super().setUp()
        Technician.objects.create(
            user=User.objects.create_user('support1', password='pass12345'), country=self.country,
            full_name='Dana Support', language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0601', customer=self.customer, site=self.site,
            company_name='Fitness First', site_description='Marina Branch', contact_name='Ali',
            contact_phone='0501234567', description='(written from the machines)', submitted_at=timezone.now(),
        )
        self.treadmill, self.bike, self.rower = [
            TicketMachine.objects.create(
                ticket=self.ticket, model_name=name, serial_no=serial, problem=TicketMachine.Problem.NOISE, position=i,
            )
            for i, (name, serial) in enumerate([('Treadmill', 'T-1'), ('Bike', 'B-1'), ('Rower', 'R-1')])
        ]
        self.review_url = f'/tasks/tickets/{self.ticket.pk}/'
        self.client.login(username='support1', password='pass12345')

    def _decide(self, machines, do, note=''):
        return self.client.post(self.review_url, {
            'action': 'machines', 'machines': [str(m.pk) for m in machines], 'do': do, 'decision_note': note,
        })

    def test_a_panatta_machine_reminds_of_the_pak_never_requires_it(self):
        # No Panatta machine: nothing said.
        self.assertNotContains(self.client.get(self.review_url), 'opening a PAK with Panatta?')
        panatta = Brand.objects.create(name='Panatta')
        self.treadmill.asset = Asset.objects.create(site=self.site, brand=panatta, model_name='Treadmill', serial_no='T-1')
        self.treadmill.save(update_fields=['asset'])
        self.assertContains(self.client.get(self.review_url), 'opening a PAK with Panatta?')
        # Once it has its PAK, the reminder goes; and a request without one is never blocked.
        self.ticket.pak_reference_number = 'PAK-1234'
        self.ticket.save(update_fields=['pak_reference_number'])
        self.assertNotContains(self.client.get(self.review_url), 'opening a PAK with Panatta?')

    def _create_task(self, url):
        return self.client.post(url, {
            'site': self.site.pk, 'priority': Task.Priority.NORMAL, 'source': Task.Source.PORTAL,
            'billing_type': Task.BillingType.CHARGEABLE, 'reported_at': '2026-09-06T10:00',
            'is_warranty': '', 'description': 'Noisy machines',
        })

    def test_split_a_ticket_into_a_visit_and_advice(self):
        response = self._decide([self.treadmill, self.bike], 'visit')
        create_url = response['Location']
        self.assertIn('decision=visit', create_url)
        page = self.client.get(create_url)
        self.assertIn('Treadmill (T-1)', page.context['form'].fields['description'].initial)
        self.assertNotIn('Rower', page.context['form'].fields['description'].initial)

        self.assertEqual(self._create_task(create_url).status_code, 302)
        self.treadmill.refresh_from_db()
        task = self.treadmill.task
        self.assertEqual(self.treadmill.decision, TicketMachine.Decision.VISIT)
        self.assertEqual(task.reported_serial_numbers, 'T-1' + chr(10) + 'B-1')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)  # the rower is still undecided

        self._decide([self.rower], 'advice', note='Tighten the seat bolt.')
        self.ticket.refresh_from_db()
        self.rower.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CONVERTED)
        self.assertEqual(self.ticket.task, task)
        self.assertEqual((self.rower.decision, self.rower.decision_note), ('advice', 'Tighten the seat bolt.'))

    def test_two_tasks_from_one_ticket(self):
        self._create_task(self._decide([self.treadmill], 'visit')['Location'])
        self._create_task(self._decide([self.bike, self.rower], 'ship_part')['Location'])
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CONVERTED)
        tasks = Task.objects.filter(ticket_machines__ticket=self.ticket).distinct()
        self.assertEqual(tasks.count(), 2)
        self.bike.refresh_from_db()
        self.assertEqual(self.bike.decision, TicketMachine.Decision.SHIP_PART)
        review = self.client.get(self.review_url)
        self.assertEqual(len(review.context['ticket_tasks']), 2)

    def test_no_task_needed_closes_the_ticket(self):
        self._decide([self.treadmill, self.bike], 'advice')
        self._decide([self.rower], 'not_covered', note='Under the gym supplier warranty.')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)
        self.assertFalse(Task.objects.exists())

    def test_a_decided_machine_cannot_be_picked_again(self):
        self._decide([self.treadmill], 'advice')
        response = self.client.get(f'/tasks/new/?ticket={self.ticket.pk}&machines={self.treadmill.pk}&decision=visit')
        self.assertRedirects(response, self.review_url, fetch_redirect_response=False)

    def test_the_customer_sees_each_machines_status(self):
        self._decide([self.rower], 'not_covered', note='Under the gym supplier warranty.')
        PortalUser.objects.create(customer=self.customer, user=User.objects.create_user('fitnessfirst', password='pass12345'))
        self.client.login(username='fitnessfirst', password='pass12345')  # the customer, not staff
        response = self.client.get(f'/tasks/tickets/status/{self.ticket.token}/')
        self.assertContains(response, 'Under review')
        self.assertContains(response, 'Under the gym supplier warranty.')


class TicketMachineFindingsTests(TaskTestCase):
    # The report records what was found and done on each machine, starts
    # from the machines the customer reported for this task, and the
    # customer's ticket shows each machine's result.

    # The same report form and lead as the approval tests (not their tests).
    _report_payload = TaskSupervisorApprovalTests._report_payload
    _assign_lead = TaskSupervisorApprovalTests._assign_lead

    def setUp(self):
        super().setUp()
        # About the report's other rules: the machines' photos have their own tests
        # (test_a_report_needs_every_machines_three_photos, api CustomerPhotosOnTheMachineCardTests).
        self._photos = patch('tasks.views.report_machine_photo_gaps', return_value=[])
        self._photos.start()
        self.addCleanup(self._photos.stop)
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PORTAL, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
            responsible_supervisor=self.supervisor_user.technician,
        )
        self.report_url = f'/tasks/my/{self.task.pk}/report/'
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0701', customer=self.customer, site=self.site,
            company_name='Fitness First', contact_name='Ali', contact_phone='0501234567',
            description='(from the machines)', submitted_at=timezone.now(), task=self.task,
            status=CustomerTicket.Status.CONVERTED,
        )
        self.listed = TicketMachine.objects.create(
            ticket=self.ticket, asset=self.asset, problem=TicketMachine.Problem.NOISE,
            decision=TicketMachine.Decision.VISIT, task=self.task,
        )
        self.unlisted = TicketMachine.objects.create(
            ticket=self.ticket, model_name='Old bike', serial_no='OB-9', problem=TicketMachine.Problem.DISPLAY,
            decision=TicketMachine.Decision.VISIT, task=self.task, position=1,
        )
        self._assign_lead(self.supervisor_user.technician)
        self.client.login(username='supervisor1', password='pass12345')

    def test_the_report_starts_from_the_tickets_machines(self):
        response = self.client.get(self.report_url)
        self.assertEqual(response.context['existing_formset'].initial, [{'asset': self.asset.pk}])
        self.assertEqual(response.context['new_formset'].initial, [
            {'model_name': 'Old bike', 'serial_no': 'OB-9', 'code': '', 'ticket_machine': self.unlisted.pk,
             'serial_photo_url': ''},
        ])

    def test_each_machines_finding_is_saved_and_shown_to_the_customer(self):
        payload = self._report_payload()
        payload.update({
            'existing-TOTAL_FORMS': '1', 'existing-INITIAL_FORMS': '1',
            'existing-0-asset': str(self.asset.pk), 'existing-0-outcome': 'repaired',
            'existing-0-note': 'Belt tightened.',
            'new-TOTAL_FORMS': '1', 'new-INITIAL_FORMS': '1',
            'new-0-brand': str(self.brand.pk), 'new-0-model_name': 'Old bike', 'new-0-serial_no': 'OB-9',
            'new-0-outcome': 'replaced', 'new-0-note': 'Console replaced.',
            'new-0-ticket_machine': str(self.unlisted.pk),
        })
        self.assertEqual(self.client.post(self.report_url, payload).status_code, 302)

        self.unlisted.refresh_from_db()
        self.assertIsNotNone(self.unlisted.asset)  # registered at the site now
        self.assertEqual(self.unlisted.result.note, 'Console replaced.')
        self.assertEqual(self.listed.result.get_outcome_display(), 'Repaired')

        from reports.pdf import report_content
        notes = sorted(machine.get('note') for machine in report_content(self.task.report)['machines'])
        self.assertEqual(notes, ['Belt tightened.', 'Console replaced.'])

        PortalUser.objects.create(customer=self.customer, user=User.objects.create_user('fitnessfirst', password='pass12345'))
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get(f'/tasks/tickets/status/{self.ticket.token}/')
        self.assertContains(response, 'Belt tightened.')
        self.assertContains(response, 'Console replaced.')

    def test_a_report_signed_before_findings_still_reads_as_unchanged(self):
        from reports.pdf import changed_after_signing, report_content
        self.client.post(self.report_url, self._report_payload())
        report = self.task.report
        report.signed_content = report_content(report)
        report.save(update_fields=['signed_content'])
        TaskAsset.objects.create(task=self.task, asset=self.asset, outcome='repaired')
        report.signed_content['machines'] = report_content(report)['machines']
        report.save(update_fields=['signed_content'])
        self.assertNotIn('note', report.signed_content['machines'][0])
        self.assertFalse(changed_after_signing(report))


class CorporateReportTests(TicketMachineFindingsTests):
    # The visit report like the company's PPM report: standard wording that
    # reads in the report's language, per-machine finding / action /
    # status, recommendations, the next visit, the team as "performed by",
    # and the time on site (no prices).

    def _file(self, **extra):
        from reports.models import WorkReport
        payload = self._report_payload()
        payload.update({
            'findings': '', 'work_lines': ['bolts', 'greased'], 'recommendations': ['keep_grease'],
            'existing-TOTAL_FORMS': '1', 'existing-INITIAL_FORMS': '1',
            'existing-0-asset': str(self.asset.pk), 'existing-0-outcome': 'repaired',
            'existing-0-finding_code': 'noise_moving_part', 'existing-0-action_code': 'greased',
            'new-TOTAL_FORMS': '0', 'new-INITIAL_FORMS': '0', **extra,
        })
        self.client.post(self.report_url, payload)
        return WorkReport.objects.get(task=self.task)

    def test_the_report_reads_in_its_language_with_findings_actions_and_next_visit(self):
        from reports.pdf import _template_context, report_content
        report = self._file()
        self.assertEqual(report.work_lines, ['bolts', 'greased'])
        follow_up = Task.objects.create(
            task_number='AE-0002', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            scheduled_date=timezone.localdate() + timedelta(days=90),
        )
        with translation.override('ar'):
            context = _template_context(report, report_content(report), '', None, False)
            lines = [str(line) for line in context['work_lines']]
            machine = context['machines'][0]
            self.assertIn('تم فحص جميع البراغي وإحكام شدّها.', lines)
            self.assertEqual(str(machine['finding_label']), 'صوت صادر من جزء متحرك')
            self.assertEqual(str(machine['action_label']), 'تم فك الجهاز وتشحيم الجزء المتأثر')
            self.assertTrue(str(context['recommendations'][0]).startswith('للحفاظ على فعالية التشحيم'))
        self.assertEqual(context['next_visit'], follow_up.scheduled_date)

    def test_no_hours_line_and_the_team_performed_it(self):
        from django.template.loader import render_to_string
        from reports.pdf import _template_context, report_content
        report = self._file()
        html = render_to_string('reports/visit_report_pdf.html', {
            **_template_context(report, report_content(report), '', None, False), 'is_rtl': False,
        })
        self.assertNotIn('Hours and prices', html)
        self.assertIn('Profit Sports Solutions technical team', html)
        self.assertIn('Noise from a moving part', html)

    def test_a_report_signed_before_the_standard_wording_still_reads_as_unchanged(self):
        from reports.pdf import changed_after_signing, report_content
        report = self._file(**{'work_lines': [], 'recommendations': [],
                               'existing-0-finding_code': '', 'existing-0-action_code': ''})
        report.signed_content = report_content(report)
        report.save(update_fields=['signed_content'])
        self.assertNotIn('work_lines', report.signed_content)
        self.assertFalse(changed_after_signing(report))

    def test_the_website_preview_opens_the_report_and_saves_nothing(self):
        from reports.models import WorkReport
        payload = self._report_payload()
        payload.update({'preview': '1', 'findings': '', 'work_lines': ['bolts'], 'new-TOTAL_FORMS': '0',
                        'new-INITIAL_FORMS': '0', 'existing-TOTAL_FORMS': '0', 'existing-INITIAL_FORMS': '0'})
        response = self.client.post(self.report_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PREVIEW')
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_the_customer_sees_the_time_on_site(self):
        from reports.pdf import changed_after_signing, render_visit_report_html
        self._tap(TaskEvent.EventType.STARTED, timezone.now() - timedelta(hours=2, minutes=30))
        report = self._file()
        self.assertIn('2:30 hours', render_visit_report_html(report, language='en'))
        self.assertContains(self.client.get(self.report_url), 'Time on site')

    def test_a_report_signed_before_the_time_was_shown_is_not_marked_changed(self):
        from reports.pdf import changed_after_signing, report_content
        report = self._file()
        older = report_content(report)
        older.pop('hours')
        report.signed_content = {**older, 'signature_url': ''}
        self.assertFalse(changed_after_signing(report))

    def _tap(self, kind, at):
        TaskEvent.objects.create(task=self.task, event_type=kind, occurred_at=at, actor=self.supervisor_user)

    def test_the_time_on_site_runs_from_start_to_sending_without_pauses_or_review(self):
        start = timezone.now() - timedelta(days=1)
        kinds = TaskEvent.EventType
        self._tap(kinds.ARRIVED, start - timedelta(minutes=20))  # not counted: the clock starts at "Start"
        self._tap(kinds.STARTED, start)
        self._tap(kinds.PAUSED, start + timedelta(hours=1))
        self._tap(kinds.RESUMED, start + timedelta(hours=3))
        self._tap(kinds.COMPLETED, start + timedelta(hours=3, minutes=32))
        # Waiting for review and approval adds nothing.
        self._tap(kinds.SUPERVISOR_APPROVED, start + timedelta(hours=9))
        self.assertEqual(self.task.time_on_site(), Decimal('1.53'))  # 1:32

    def test_the_hours_are_worked_out_not_typed(self):
        self._tap(TaskEvent.EventType.STARTED, timezone.now() - timedelta(hours=2))
        report = self._file(labour_hours='9')
        self.assertEqual(report.labour_hours, Decimal('2.00'))
        # A later correction keeps the time the report was first sent.
        self.assertEqual(self._file(labour_hours='9').labour_hours, Decimal('2.00'))

    def test_hours_read_as_hours_and_minutes(self):
        from reports.pdf import hours_text
        self.assertEqual(
            [hours_text(value) for value in ('1.53', '2.50', '0.02', '10.00', '0.00', None)],
            ['1:32', '2:30', '0:01', '10:00', '', ''],
        )


class SupervisorCreatesInternalTasksTests(TaskTestCase):
    # Everything inside the company is a task: a supervisor creates an
    # internal task — but a
    # customer's ticket stays with whoever has open_tickets.

    def test_a_supervisor_creates_an_internal_task_but_not_a_customer_ticket(self):
        from tasks.models import CustomerTicket
        self.client.login(username='supervisor1', password='pass12345')
        page = self.client.get('/tasks/tickets/internal/new/')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.client.get('/tasks/tickets/open/').status_code, 403)
        supervisor = Technician.objects.get(user__username='supervisor1')
        response = self.client.post('/tasks/tickets/internal/new/', {
            'subject': 'Treadmill belts', 'description': 'We need 5 belts for Excite Run 700.',
            'assigned_to': supervisor.pk,
        })
        ticket = CustomerTicket.objects.get(subject='Treadmill belts')
        self.assertEqual(ticket.kind, CustomerTicket.Kind.INTERNAL)
        self.assertRegex(ticket.ticket_number, r'-I0001$')  # a series of its own
        self.assertRedirects(response, f'/tasks/tickets/internal/{ticket.pk}/', fetch_redirect_response=False)

    def test_all_tasks_shows_the_internal_ones_first(self):
        # "All" means all (task_list): the internal tasks at the top, labelled.
        supervisor = Technician.objects.get(user__username='supervisor1')
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post('/tasks/tickets/internal/new/', {
            'subject': 'Treadmill belts', 'description': 'We need 5 belts.', 'assigned_to': supervisor.pk,
        })
        page = self.client.get('/tasks/')
        self.assertEqual([ticket.subject for ticket in page.context['internal_tasks']], ['Treadmill belts'])
        self.assertContains(page, 'Treadmill belts')
        # Not with a filter only field tasks have.
        self.assertEqual(self.client.get('/tasks/?lead=none').context['internal_tasks'], [])

    def test_a_technician_still_cannot(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/tickets/internal/new/').status_code, 403)


class SerialNumberRuleTests(CorporateReportTests):
    # A machine is tracked by its serial: a new one needs it, or a
    # deliberate "No readable serial plate" (then the review flags it);
    # a serial already at the site is that machine, not a second one.

    def _new_machine(self, **row):
        return {
            'new-TOTAL_FORMS': '1', 'new-INITIAL_FORMS': '0', 'new-0-brand': str(self.brand.pk),
            'new-0-model_name': 'Excite Bike', 'new-0-outcome': 'repaired', 'new-0-finding_code': 'noise_moving_part',
            'new-0-action_code': 'greased', **{f'new-0-{key}': value for key, value in row.items()},
        }

    def _post(self, **extra):
        from reports.models import WorkReport
        payload = self._report_payload()
        payload.update({'findings': '', 'work_lines': ['bolts'], 'existing-TOTAL_FORMS': '0',
                        'existing-INITIAL_FORMS': '0', **extra})
        self.client.post(self.report_url, payload)
        return WorkReport.objects.filter(task=self.task).first()

    def test_a_new_machine_without_a_serial_is_not_saved(self):
        self.assertIsNone(self._post(**self._new_machine(serial_no='')))

    def test_no_readable_plate_is_saved_and_flagged_in_the_review(self):
        from reports.pdf import report_language
        from tasks.views import report_problems
        report = self._post(**self._new_machine(serial_no='', no_serial='on'))
        self.assertIsNotNone(report)
        problems = [str(problem) for problem in report_problems(report, report_language(self.customer))]
        self.assertTrue(any('no serial number' in problem for problem in problems), problems)

    def test_a_serial_already_at_the_site_is_that_machine(self):
        self.asset.serial_no = 'BK-7788'
        self.asset.save(update_fields=['serial_no'])
        self._post(**self._new_machine(serial_no='bk-7788'))
        self.assertEqual(list(self.task.task_assets.values_list('asset_id', flat=True)), [self.asset.pk])
        self.assertEqual(Asset.objects.filter(site=self.site).count(), 1)


class InstallationChecklistTests(TaskTestCase):
    # The installers check off each machine on site: what happened, its
    # serial, colours, what went in instead, a note and a photo.

    _report_payload = TaskSupervisorApprovalTests._report_payload
    _assign_lead = TaskSupervisorApprovalTests._assign_lead

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
            responsible_supervisor=self.supervisor_user.technician,
        )
        self.report_url = f'/tasks/my/{self.task.pk}/report/'
        self._assign_lead(self.technician)
        self.technician.language = 'en'  # the messages are checked in English
        self.technician.save(update_fields=['language'])
        self.client.login(username='tech1', password='pass12345')
        self.task_type.category = TaskType.Category.INSTALLATION
        self.task_type.save(update_fields=['category'])
        self.task.task_type = self.task_type
        self.task.brand = self.brand
        self.task.save(update_fields=['task_type', 'brand'])
        self.run_line = TaskProduct.objects.create(task=self.task, product_code='EXCITE-RUN')
        self.bike_line = TaskProduct.objects.create(task=self.task, product_code='EXCITE-BIKE')

    def _check(self, rows):
        from reports.models import WorkReport
        payload = self._report_payload()
        payload.update({'findings': '', 'work_lines': ['bolts'],
                        'checklist-TOTAL_FORMS': str(len(rows)), 'checklist-INITIAL_FORMS': '2',
                        'checklist-MIN_NUM_FORMS': '0', 'checklist-MAX_NUM_FORMS': '1000'})
        for index, row in enumerate(rows):
            payload.update({f'checklist-{index}-{key}': value for key, value in row.items()})
        self.client.post(self.report_url, payload)
        return WorkReport.objects.filter(task=self.task).first()

    def test_parts_used_show_their_catalogue_photo(self):
        from reference.models import Part
        Part.objects.create(code='BELT-01', description='Treadmill belt', photo='part_photos/abc/BELT-01.png')
        page = self.client.get(self.report_url)
        self.assertContains(page, 'data-photo="/media/part_photos/abc/BELT-01.png"')
        self.assertContains(page, 'js/part-photos.js')

    def test_installed_needs_a_serial_and_damaged_needs_a_reason(self):
        from reports.models import WorkReport
        payload = self._report_payload()
        payload.update({
            'findings': '', 'work_lines': ['bolts'], 'existing-TOTAL_FORMS': '0', 'existing-INITIAL_FORMS': '0',
            'new-TOTAL_FORMS': '0', 'new-INITIAL_FORMS': '0',
            'checklist-TOTAL_FORMS': '2', 'checklist-INITIAL_FORMS': '2',
            'checklist-MIN_NUM_FORMS': '0', 'checklist-MAX_NUM_FORMS': '1000',
            'checklist-0-id': self.run_line.pk, 'checklist-0-product_code': 'EXCITE-RUN', 'checklist-0-status': 'done',
            'checklist-1-id': self.bike_line.pk, 'checklist-1-product_code': 'EXCITE-BIKE', 'checklist-1-status': 'damaged',
        })
        response = self.client.post(self.report_url, payload)
        self.assertContains(response, 'This machine needs its serial number')
        self.assertContains(response, 'Say what is wrong, or add a photo.')
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

    def test_the_checklist_is_saved_and_only_installed_machines_join_the_list(self):
        self._check([
            {'id': self.run_line.pk, 'product_code': 'EXCITE-RUN', 'status': 'done', 'serial_number': 'TG-1',
             'frame': 'Red', 'arm': '', 'padding': '', 'trim': ''},
            {'id': self.bike_line.pk, 'product_code': 'EXCITE-BIKE', 'status': 'later', 'serial_number': ''},
            {'product_code': 'EXCITE-TOP', 'status': 'done', 'serial_number': 'TG-9'},
        ])
        self.run_line.refresh_from_db()
        self.bike_line.refresh_from_db()
        self.assertEqual((self.run_line.status, self.run_line.serial_number, self.run_line.frame, self.run_line.arm),
                         ('done', 'TG-1', 'Red', 'Black'))
        self.assertEqual(self.bike_line.status, 'later')
        added = self.task.products.get(product_code='EXCITE-TOP')
        self.assertTrue(added.added_on_site)
        self.assertEqual(
            sorted(Asset.objects.filter(site=self.site).exclude(pk=self.asset.pk).values_list('serial_no', 'code')),
            [('TG-1', 'EXCITE-RUN'), ('TG-9', 'EXCITE-TOP')],
        )

    def test_what_was_not_installed_gets_a_follow_up_task(self):
        self._check([
            {'id': self.run_line.pk, 'product_code': 'EXCITE-RUN', 'status': 'done', 'serial_number': 'TG-1'},
            {'id': self.bike_line.pk, 'product_code': 'EXCITE-BIKE', 'status': 'later', 'serial_number': ''},
        ])
        self.client.login(username='supervisor1', password='pass12345')
        page = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(page, 'Create a follow-up task for them')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'create_follow_up'})
        follow_up = Task.objects.get(previous_task=self.task)
        self.assertRedirects(response, f'/tasks/{follow_up.pk}/', fetch_redirect_response=False)
        line = follow_up.products.get()
        self.assertEqual((line.product_code, line.status, line.source_line), ('EXCITE-BIKE', 'pending', self.bike_line))
        # Once made, it isn't offered again.
        self.assertNotContains(self.client.get(f'/tasks/{self.task.pk}/'), 'Create a follow-up task for them')

    def test_the_customer_gets_an_installation_report(self):
        from reports.pdf import render_visit_report_html
        report = self._check([
            {'id': self.run_line.pk, 'product_code': 'EXCITE-RUN', 'status': 'done', 'serial_number': 'TG-1',
             'frame': 'Red', 'arm': '', 'padding': '', 'trim': ''},
            {'id': self.bike_line.pk, 'product_code': 'EXCITE-BIKE', 'status': 'missing', 'serial_number': ''},
        ])
        html = render_visit_report_html(report, language='en')
        self.assertIn('Installation Report', html)
        self.assertIn('Installed Equipment', html)
        self.assertIn('Red / Black / Black / Black', html)
        self.assertIn('1 machine is still to be installed', html)
        self.assertNotIn('Findings &amp; Actions', html)


class EquipmentChainTests(TaskTestCase):
    # Loaded at the Riyadh warehouse, installed at a Dubai gym: the machines
    # (and their serials) travel from step to step, across borders. A
    # receiving only adds to stock; it sends nothing on.

    def setUp(self):
        super().setUp()
        self.saudi = Country.objects.create(
            name='Saudi Arabia', name_ar='السعودية', iso_code='SA', timezone='Asia/Riyadh', currency_code='SAR',
            weekend_days='4',
        )
        ours = Customer.objects.create(country=self.saudi, name='Profit Sports Solutions', segment='other', is_internal=True)
        self.warehouse = Site.objects.create(customer=ours, name='Riyadh warehouse', address='Riyadh')
        receiving_type = TaskType.objects.create(code='recv', name='Receiving', name_ar='استلام', category='receiving')
        loading_type = TaskType.objects.create(code='load', name='Loading', name_ar='تحميل', category='loading')
        TaskType.objects.create(code='inst', name='Installation', name_ar='تركيب', category='installation')
        self.receiving = self._task('SA-0001', receiving_type)
        TaskProduct.objects.create(task=self.receiving, product_code='EXCITE-RUN', quantity=2, status='done')
        self.loading = self._task('SA-0002', loading_type)
        self.loaded = TaskProduct.objects.create(task=self.loading, product_code='EXCITE-RUN', serial_number='TG-1', status='done')
        TaskProduct.objects.create(task=self.loading, product_code='EXCITE-RUN', status='damaged')
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Dana Manager', language='en',
            role=Technician.Role.PROJECT_MANAGER, employment_type='staff',  # every country (docs/access_rules.md)
        )

    def _task(self, number, task_type):
        return Task.objects.create(
            task_number=number, site=self.warehouse, task_type=task_type, brand=self.brand,
            priority=Task.Priority.NORMAL, source=Task.Source.INTERNAL, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.COMPLETED,
        )

    def test_loaded_machines_go_to_an_installation_in_another_country(self):
        self.client.login(username='manager1', password='pass12345')
        self.assertContains(self.client.get(f'/tasks/{self.loading.pk}/'), 'Send to installation')
        sara = Technician.objects.get(user=self.supervisor_user)  # in the UAE
        saudi_supervisor = Technician.objects.create(
            user=User.objects.create_user('ksa_super', password='x'), country=self.saudi, full_name='Khalid Super',
            language='ar', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        send = {'action': 'create_next_step', 'step': 'installation', 'site': self.site.pk, 'lines': [self.loaded.pk]}
        # Another country: someone there must own it — not nobody, not someone from elsewhere.
        for supervisor in ('', saudi_supervisor.pk):
            self.client.post(f'/tasks/{self.loading.pk}/', {**send, 'supervisor': supervisor})
            self.assertFalse(Task.objects.filter(previous_task=self.loading).exists())
        response = self.client.post(f'/tasks/{self.loading.pk}/', {**send, 'supervisor': sara.pk})
        installation = Task.objects.get(previous_task=self.loading)
        self.assertEqual(installation.responsible_supervisor, sara)
        self.assertRedirects(response, f'/tasks/{installation.pk}/', fetch_redirect_response=False)
        self.assertEqual(installation.site, self.site)  # the Dubai gym
        self.assertTrue(installation.task_number.startswith('AE-'))
        line = installation.products.get()
        self.assertEqual((line.serial_number, line.status, line.source_line), ('TG-1', 'pending', self.loaded))
        # Carried once: nothing left to carry from the loading.
        self.assertNotContains(self.client.get(f'/tasks/{self.loading.pk}/'), 'Send to installation')

    def test_a_receiving_sends_nothing_on_and_the_internal_account_gets_no_tickets(self):
        from tasks.forms import StaffTicketForm
        self.client.login(username='manager1', password='pass12345')
        self.assertNotContains(self.client.get(f'/tasks/{self.receiving.pk}/'), 'Send to installation')
        line = self.receiving.products.get()
        response = self.client.post(f'/tasks/{self.receiving.pk}/', {
            'action': 'create_next_step', 'step': 'installation', 'site': self.site.pk, 'lines': [line.pk],
        })
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.warehouse, StaffTicketForm(country=self.saudi).fields['site'].queryset)

    def test_only_managers_send_machines_on(self):
        self.loading.site = self.site  # even in the supervisor's own country
        self.loading.save(update_fields=['site'])
        self.client.login(username='supervisor1', password='pass12345')
        self.assertNotContains(self.client.get(f'/tasks/{self.loading.pk}/'), 'Send to installation')
        response = self.client.post(f'/tasks/{self.loading.pk}/', {
            'action': 'create_next_step', 'step': 'installation', 'site': self.site.pk, 'lines': [self.loaded.pk],
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Task.objects.filter(previous_task=self.loading).exists())

    def test_a_loading_is_planned_from_the_warehouse_stock(self):
        from .views import loading_shortfall, warehouse_stock_for_planning
        # 2 received, 2 on this loading: none left, but this loading can still plan its own 2.
        self.assertEqual(warehouse_stock_for_planning(self.loading), {'EXCITE-RUN': 2})
        self.assertEqual(loading_shortfall(self.loading), '')
        TaskProduct.objects.create(task=self.loading, product_code='EXCITE-RUN')
        self.assertIn('EXCITE-RUN (1 short)', loading_shortfall(self.loading))

    def test_only_a_manager_plans_a_loading(self):
        # A warehouse in the supervisor's own country, so they can open the task at all.
        ours = Customer.objects.create(country=self.country, name='Profit Sports Solutions', segment='other', is_internal=True)
        self.loading.site = Site.objects.create(customer=ours, name='Dubai warehouse', address='Jebel Ali')
        self.loading.status = Task.Status.NEW
        self.loading.save(update_fields=['site', 'status'])
        self.client.login(username='supervisor1', password='pass12345')
        page = self.client.get(f'/tasks/{self.loading.pk}/edit/')
        self.assertContains(page, 'Only a manager plans the machines on a loading or an installation.')
        self.client.login(username='manager1', password='pass12345')
        page = self.client.get(f'/tasks/{self.loading.pk}/edit/')
        self.assertContains(page, 'In this warehouse:')
        self.assertNotContains(page, 'Only a manager plans the machines on a loading or an installation.')


class ReceivingByCountTests(TaskTestCase):
    # A container comes with codes and quantities, not serials: receiving
    # counts by code (machines and spare parts); the serial is typed once,
    # at loading (optional) or installation (required).

    def setUp(self):
        super().setUp()
        self.task_type.category = TaskType.Category.RECEIVING
        self.task_type.save(update_fields=['category'])
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, task_type=self.task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.INTERNAL, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
        )

    def _plan(self, rows):
        from .forms import TaskProductForm
        from .views import save_planned_lines
        forms_ = [TaskProductForm(row) for row in rows]
        if all(form.is_valid() for form in forms_):
            save_planned_lines(self.task, [form.cleaned_data for form in forms_])
        return forms_

    def _check(self, rows, step='receiving'):
        from .forms import TaskProductCheckForm
        from .views import checklist_initial, save_checklist
        known = {row['id']: row for row in checklist_initial(self.task)}
        forms_ = [TaskProductCheckForm(row, initial=known.get(row.get('id'), {}), step=step) for row in rows]
        if all(form.is_valid() for form in forms_):
            save_checklist(self.task, [form.cleaned_data for form in forms_])
        return forms_

    def test_receiving_keeps_code_and_quantity_and_takes_spare_parts(self):
        self._plan([
            {'kind': 'machine', 'product_code': 'BENCH-01', 'quantity': '20', 'serial_number': 'IGNORED'},
            {'kind': 'part', 'product_code': 'RXP00010', 'quantity': '5', 'frame': 'Red'},
        ])
        bench, part = self.task.products.order_by('pk')
        self.assertEqual((bench.kind, bench.quantity, bench.serial_number), ('machine', 20, ''))
        self.assertEqual((part.kind, part.quantity, part.frame), ('part', 5, 'Black'))

    def test_a_spare_part_must_be_in_the_catalogue_once_there_is_one(self):
        from reference.models import Part
        Part.objects.create(code='RXP00010', description='Securing pin housing end cap')
        forms_ = self._plan([{'kind': 'part', 'product_code': 'NOPE', 'quantity': '1'}])
        self.assertIn('product_code', forms_[0].errors)
        self._plan([{'kind': 'part', 'product_code': 'rxp00010', 'quantity': '1'}])
        self.assertEqual(self.task.products.get().product_code, 'RXP00010')

    def test_a_short_count_splits_the_line(self):
        line = TaskProduct.objects.create(task=self.task, product_code='BENCH-01', quantity=20)
        row = {'id': line.pk, 'product_code': 'BENCH-01', 'status': 'done', 'received': '18', 'rest_status': 'damaged'}
        forms_ = self._check([row])
        self.assertIn('note', forms_[0].errors)  # damaged ones need a word or a photo
        self._check([{**row, 'note': 'Two frames bent'}])
        lines = list(self.task.products.order_by('pk').values_list('quantity', 'status', 'note'))
        self.assertEqual(lines, [(18, 'done', 'Two frames bent'), (2, 'damaged', 'Two frames bent')])

    def test_none_arrived_marks_the_whole_line(self):
        line = TaskProduct.objects.create(task=self.task, product_code='BENCH-01', quantity=4)
        self._check([{'id': line.pk, 'product_code': 'BENCH-01', 'status': 'done', 'received': '0'}])
        self.assertEqual(list(self.task.products.values_list('quantity', 'status')), [(4, 'missing')])

    def test_receiving_asks_no_serial_and_something_extra_is_counted_too(self):
        line = TaskProduct.objects.create(task=self.task, product_code='BENCH-01', quantity=2)
        forms_ = self._check([
            {'id': line.pk, 'product_code': 'BENCH-01', 'status': 'done'},
            {'product_code': 'RXP00011', 'kind': 'part', 'quantity': '3', 'status': 'done'},
        ])
        self.assertNotIn('serial_number', forms_[0].fields)
        extra = self.task.products.get(added_on_site=True)
        self.assertEqual((extra.kind, extra.quantity), ('part', 3))
        line.refresh_from_db()
        self.assertEqual((line.quantity, line.status), (2, 'done'))

    def test_the_serial_is_optional_at_loading_and_required_at_installation(self):
        line = TaskProduct.objects.create(task=self.task, product_code='BENCH-01')
        row = {'id': line.pk, 'product_code': 'BENCH-01', 'status': 'done', 'serial_number': ''}
        self.assertTrue(self._check([row], step='loading')[0].is_valid())
        self.assertIn('serial_number', self._check([row], step='installation')[0].errors)

    def test_received_lines_stay_in_the_warehouse(self):
        # Loading is planned from the warehouse's stock instead (EquipmentChainTests).
        from .views import lines_to_carry
        TaskProduct.objects.create(task=self.task, product_code='BENCH-01', quantity=3, status='done')
        self.assertEqual(lines_to_carry(self.task), [])


class WarehouseStockTests(TaskTestCase):
    # Stock is worked out from the tasks: received − loaded (machines) or
    # used on reports (parts), plus hand counts; parts ordered for a repair
    # are held for it until it's done.

    def setUp(self):
        super().setUp()
        ours = Customer.objects.create(country=self.country, name='Profit Sports Solutions', segment='other', is_internal=True)
        self.warehouse = Site.objects.create(customer=ours, name='Dubai warehouse', address='Jebel Ali')
        self.receiving = self._task('AE-0101', 'receiving', self.warehouse)
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Dana Manager', language='en',
            role=Technician.Role.MANAGER, employment_type='staff',
        )

    def _task(self, number, category, site, status=Task.Status.IN_PROGRESS):
        task_type, _created = TaskType.objects.get_or_create(
            code=category, defaults={'name': category, 'name_ar': category, 'category': category},
        )
        return Task.objects.create(
            task_number=number, site=site, task_type=task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.INTERNAL, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, status=status,
        )

    def test_machines_are_received_less_loaded_and_a_cancelled_load_gives_them_back(self):
        from .stock import machine_stock
        TaskProduct.objects.create(task=self.receiving, product_code='BENCH-01', quantity=20, status='done')
        TaskProduct.objects.create(task=self.receiving, product_code='BENCH-01', quantity=2, status='missing')
        loading = self._task('AE-0102', 'loading', self.warehouse, status=Task.Status.NEW)
        for _unit in range(3):
            TaskProduct.objects.create(task=loading, product_code='bench-01')  # planned is enough
        cancelled = self._task('AE-0103', 'loading', self.warehouse, status=Task.Status.CANCELLED)
        TaskProduct.objects.create(task=cancelled, product_code='BENCH-01')
        self.assertEqual(machine_stock(self.warehouse), {'BENCH-01': 17})

    def test_a_count_keeps_the_difference_and_the_same_count_changes_nothing(self):
        from .stock import machine_stock, record_count
        TaskProduct.objects.create(task=self.receiving, product_code='BENCH-01', quantity=20, status='done')
        adjustment = record_count(self.warehouse, 'machine', 'BENCH-01', 18, 'Monthly count', self.manager_user)
        self.assertEqual(adjustment.quantity, -2)
        self.assertIsNone(record_count(self.warehouse, 'machine', 'bench-01', 18, 'Again', self.manager_user))
        self.assertEqual(machine_stock(self.warehouse), {'BENCH-01': 18})

    def test_parts_are_used_on_reports_and_held_for_their_repair(self):
        from .stock import part_rows
        repair = self._task('AE-0104', 'maintenance', self.site)
        TaskProduct.objects.create(task=self.receiving, kind='part', product_code='RXP00010', quantity=10, status='done')
        TaskProduct.objects.create(
            task=self.receiving, kind='part', product_code='RXP00010', quantity=5, status='done', for_task=repair,
        )
        # Another job's report used 3; the repair's own report used 2 of its 5.
        other_report = WorkReport.objects.create(
            task=self._task('AE-0105', 'maintenance', self.site), findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        PartUsed.objects.create(report=other_report, part_code='rxp00010', quantity=3)
        repair_report = WorkReport.objects.create(
            task=repair, findings='Pin broken', action_taken='Replaced', resolved=True, labour_hours='1.00',
            customer_name='Ali Manager', submitted_at=timezone.now(),
        )
        PartUsed.objects.create(report=repair_report, part_code='RXP00010', quantity=2)
        row = part_rows(self.country)[0]
        self.assertEqual((row['code'], row['quantity'], row['held'], row['free']), ('RXP00010', 10, [(repair, 3)], 7))
        # Once the repair is closed, what it didn't use goes free.
        repair.status = Task.Status.CLOSED
        repair.save(update_fields=['status'])
        row = part_rows(self.country)[0]
        self.assertEqual((row['quantity'], row['held'], row['free']), (10, [], 10))

    def test_the_stock_screen_and_who_counts(self):
        TaskProduct.objects.create(task=self.receiving, product_code='BENCH-01', quantity=4, status='done')
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/stock/').status_code, 403)
        self.client.login(username='manager1', password='pass12345')
        page = self.client.get('/tasks/stock/')
        self.assertContains(page, 'Machines — Dubai warehouse')
        self.assertContains(page, 'BENCH-01')
        response = self.client.post('/tasks/stock/', {
            'kind': 'machine', 'site': self.warehouse.pk, 'product_code': 'BENCH-01', 'counted': 5, 'note': 'Found one',
        }, follow=True)
        self.assertContains(response, 'Count recorded: BENCH-01 +1.')

    def test_a_part_line_names_the_repair_it_was_ordered_for(self):
        from .forms import TaskProductForm
        repair = self._task('AE-0104', 'maintenance', self.site)
        form = TaskProductForm({'kind': 'part', 'product_code': 'RXP00010', 'quantity': '2', 'for_task': 'ae-0104'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['for_task'], repair)
        form = TaskProductForm({'kind': 'part', 'product_code': 'RXP00010', 'quantity': '2', 'for_task': 'AE-9999'})
        self.assertIn('for_task', form.errors)

    def test_the_sheet_quantities_become_the_opening_stock(self):
        from .stock import part_stock
        upload = PartsCatalogueTests._stock_sheet(self, [['7SA037', 'Lever with button', None, None, 14]])
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/tasks/parts/', {
            'action': 'import', 'file': upload, 'warehouse': self.warehouse.pk,
        }, follow=True)
        self.assertContains(response, '1 quantities recorded as stock at Dubai warehouse.')
        self.assertEqual(part_stock(self.country), {'7SA037': 14})


class DeactivateAndHandOverTests(TaskTestCase):
    # Someone leaving: their open tasks go to a colleague in the same step.

    def test_their_tasks_move_to_the_colleague(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Maya Manager', language='en',
            role=Technician.Role.MANAGER, employment_type='staff',
        )
        colleague = Technician.objects.create(
            user=User.objects.create_user('tech2', password='pass12345'), country=self.country,
            full_name='Rami Colleague', language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        task = Task.objects.create(
            task_number='AE-0009', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED,
        )
        TaskAssignment.objects.create(task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now(), is_active=True)
        self.client.login(username='manager1', password='pass12345')
        url = f'/tasks/technicians/{self.technician.pk}/edit/'
        self.assertContains(self.client.get(url), 'Move all their open tasks to')
        response = self.client.post(url, {'action': 'deactivate', 'reason': 'Resigned', 'move_to': colleague.pk})
        self.assertRedirects(response, '/tasks/technicians/', fetch_redirect_response=False)
        self.technician.refresh_from_db()
        self.assertFalse(self.technician.is_active)
        lead = task.assignments.get(is_active=True)
        self.assertEqual((lead.technician, lead.role), (colleague, TaskAssignment.Role.LEAD))
        self.assertEqual(task.assignments.get(technician=self.technician).end_reason, 'left_company')


class ScheduleChangeBellTests(TaskTestCase):
    """A time a supervisor asks to move waits on the approvers' bell until it's decided."""

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0950', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )

    def test_the_technical_support_manager_sees_it_until_decided(self):
        from tasks.models import ScheduleChangeRequest
        tsm_user = User.objects.create_user('tsm1', password='pass12345')
        Technician.objects.create(
            user=tsm_user, country=self.country, full_name='Tamer Support', language='en',
            role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        change = ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self.supervisor_user.technician, requested_scheduled_for=timezone.now() + timedelta(days=2),
            reason='Customer asked for Thursday', created_at=timezone.now(),
        )
        self.client.login(username='tsm1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertContains(response, 'Change of visit time requested')
        self.assertIn('schedule_change_pending', [item['kind'] for item in response.context['unseen_notifications']])
        change.status = ScheduleChangeRequest.Status.APPROVED
        change.save(update_fields=['status'])
        response = self.client.get('/tasks/')
        self.assertNotIn('schedule_change_pending', [item['kind'] for item in response.context['unseen_notifications']])

    def test_a_supervisor_is_not_asked_to_approve(self):
        from tasks.models import ScheduleChangeRequest
        ScheduleChangeRequest.objects.create(
            task=self.task, requested_by=self.supervisor_user.technician, requested_scheduled_for=timezone.now() + timedelta(days=2),
            created_at=timezone.now(),
        )
        self.client.login(username='supervisor1', password='pass12345')
        self.assertNotContains(self.client.get('/tasks/'), 'Change of visit time requested')


class TaskUpdatesTellTheTeamTests(TaskTestCase):
    """Every action on a task tells the people on it, except whoever did it (tasks.updates)."""

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0960', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            responsible_supervisor=self.supervisor_user.technician,
        )
        TaskAssignment.objects.create(task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now())

    def _record(self, event_type, actor):
        with self.captureOnCommitCallbacks(execute=True):
            return TaskEvent.objects.create(task=self.task, event_type=event_type, occurred_at=timezone.now(), actor=actor)

    def test_the_lead_arriving_tells_the_supervisor_not_the_lead(self):
        from unittest.mock import patch
        from tasks.models import TaskEventRecipient
        with patch('tasks.updates.push_in_their_language') as push:
            self._record(TaskEvent.EventType.ARRIVED, self.tech_user)
        told = set(TaskEventRecipient.objects.values_list('technician__user__username', flat=True))
        self.assertEqual(told, {'supervisor1'})
        self.assertEqual(push.call_count, 1)

    def test_it_waits_in_the_bell_until_the_task_is_opened(self):
        self._record(TaskEvent.EventType.RESCHEDULED, self.supervisor_user)
        self.client.login(username='tech1', password='pass12345')
        kinds = lambda: [i['kind'] for i in self.client.get('/tasks/my-week/').context.get('unseen_notifications', [])]
        self.assertIn('task_update', kinds())
        self.client.get(f'/tasks/my/{self.task.pk}/')
        self.assertNotIn('task_update', kinds())

    def test_a_negligence_flag_tells_no_one(self):
        from tasks.models import TaskEventRecipient
        self._record(TaskEvent.EventType.NEGLIGENCE, self.supervisor_user)
        self.assertFalse(TaskEventRecipient.objects.exists())


class CompanyWatchTests(TaskTestCase):
    """The Technical Support and Operations Managers watch every country
    (tasks.alarms): a task still blocked after 2 working hours, or with no
    day after 24, reaches their bell wherever it is; their Dashboard (and the
    app's) opens on the company's summary. Not a country Manager's."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        def staff(username, role):
            return Technician.objects.create(
                user=User.objects.create_user(username, password='pass12345'), country=self.country,
                full_name=username.title(), language='en', role=role, employment_type='staff',
            )
        self.support = staff('support1', Technician.Role.SUPPORT_MANAGER)
        staff('manager1', Technician.Role.MANAGER)
        egypt = Country.objects.create(name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP')
        site = Site.objects.create(customer=Customer.objects.create(country=egypt, name='Nile Gym', segment='gym'),
                                   name='Zamalek', address='Cairo')
        long_ago = timezone.now() - timezone.timedelta(days=5)

        def task(number, status):
            made = Task.objects.create(
                task_number=number, site=site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
                billing_type=Task.BillingType.CHARGEABLE, reported_at=long_ago, created_by=self.supervisor_user,
                status=status,
            )
            TaskEvent.objects.create(task=made, event_type=TaskEvent.EventType.CREATED, occurred_at=long_ago,
                                     actor=self.supervisor_user)
            return made
        self.blocked = task('EG-0001', Task.Status.BLOCKED)
        TaskEvent.objects.create(task=self.blocked, event_type=TaskEvent.EventType.BLOCKED, occurred_at=long_ago,
                                 actor=self.tech_user)
        self.unscheduled = task('EG-0002', Task.Status.NEW)

    def bell(self, username):
        self.client.login(username=username, password='pass12345')
        return {item['kind']: item for item in self.client.get('/tasks/dashboard/').context['unseen_notifications']}

    def test_their_bell_has_every_countrys_alarms(self):
        bell = self.bell('support1')  # looking at the UAE; the tasks are in Egypt
        self.assertEqual(bell['blocked_long']['task'], self.blocked)
        self.assertEqual(bell['unscheduled_overdue']['task'], self.unscheduled)
        manager = self.bell('manager1')
        self.assertNotIn('blocked_long', manager)
        self.assertNotIn('unscheduled_overdue', manager)
        # The Operations Manager: the urgent alarms too, but not the summary (the Technical Support Manager's).
        from django.contrib.auth.models import User
        Technician.objects.create(
            user=User.objects.create_user('ops1', password='pass12345'), country=self.country, full_name='Ops',
            language='en', role=Technician.Role.OPERATIONS_MANAGER, employment_type='staff',
        )
        ops = self.bell('ops1')
        self.assertEqual(ops['blocked_long']['task'], self.blocked)
        self.assertEqual(ops['unscheduled_overdue']['task'], self.unscheduled)
        self.assertIsNone(self.client.get('/tasks/dashboard/').context['company_summary'])

    def test_their_dashboard_opens_on_the_company(self):
        self.client.login(username='support1', password='pass12345')
        summary = self.client.get('/tasks/dashboard/').context['company_summary']
        self.assertEqual((summary['blocked'], summary['blocked_long'], summary['unscheduled_overdue']), (1, 1, 1))
        listed = self.client.get('/tasks/', {'alarm': 'blocked_long', 'country': 'all'}).context['page_obj']
        self.assertEqual([task.pk for task in listed], [self.blocked.pk])
        # The same in the app.
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=self.support.user)
        app = self.client_class().get('/api/dashboard/', HTTP_AUTHORIZATION=f'Token {token.key}').json()
        self.assertEqual(app['company_summary']['blocked_long'], 1)
        # Not a country Manager's.
        self.client.login(username='manager1', password='pass12345')
        self.assertIsNone(self.client.get('/tasks/dashboard/').context['company_summary'])

    def test_the_operations_manager_hears_later(self):
        # Blocked for about a working day: the Technical Support Manager knows; the Operations
        # Manager not yet (after 2 working days) — only what stayed stuck reaches them.
        from django.contrib.auth.models import User
        from tasks.alarms import long_blocked_tasks
        ops = Technician.objects.create(
            user=User.objects.create_user('ops1', password='pass12345'), country=self.country, full_name='Ops',
            language='en', role=Technician.Role.OPERATIONS_MANAGER, employment_type='staff',
        )
        TaskEvent.objects.filter(task=self.blocked, event_type=TaskEvent.EventType.BLOCKED).update(
            occurred_at=timezone.now() - timezone.timedelta(hours=20))
        self.assertEqual(long_blocked_tasks(self.support), [self.blocked])
        self.assertEqual(long_blocked_tasks(ops), [])

    def test_a_task_blocked_a_moment_ago_is_not_yet_an_alarm(self):
        TaskEvent.objects.filter(task=self.blocked, event_type=TaskEvent.EventType.BLOCKED).update(
            occurred_at=timezone.now() - timezone.timedelta(minutes=30))
        self.assertNotIn('blocked_long', self.bell('support1'))


class CustomRoleTests(TaskTestCase):
    """Roles the Admin makes (people.CustomRole, docs/access_rules.md): a COO
    based on the Operations Manager, with Tickets switched off, follows the
    Operations Manager's fixed rules and its own switches."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        self.admin = Technician.objects.create(
            user=User.objects.create_user('admin9', password='pass12345'), country=self.country, full_name='Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.client.login(username='admin9', password='pass12345')
        self.client.post('/tasks/roles/', {
            'action': 'new_role', 'new-name': 'COO', 'new-name_ar': 'الرئيس التنفيذي للعمليات',
            'new-based_on': Technician.Role.OPERATIONS_MANAGER,
        })
        from people.models import CustomRole
        self.coo_role = CustomRole.objects.get(name='COO')
        # Its switches start as the Operations Manager's; the Admin turns Tickets off.
        allowed = set(RolePermission.objects.filter(role=self.coo_role.key, allowed=True).values_list('permission', flat=True))
        ops = set(RolePermission.objects.filter(role=Technician.Role.OPERATIONS_MANAGER, allowed=True)
                  .values_list('permission', flat=True))
        self.assertEqual(allowed, ops)
        tickets = {RolePermission.Permission.MANAGE_TICKETS, RolePermission.Permission.OPEN_TICKETS,
                   RolePermission.Permission.TICKETS_ALL_COUNTRIES, RolePermission.Permission.DECIDE_ESCALATED_TICKETS}
        RolePermission.objects.filter(role=self.coo_role.key, permission__in=tickets).update(allowed=False)

    def add_coo(self, username='karim'):
        self.client.login(username='admin9', password='pass12345')
        self.client.post('/tasks/technicians/new/', {
            'role': self.coo_role.key, 'full_name': 'Karim COO', 'country': self.country.pk, 'email': f'{username}@example.com',
            'employment_type': 'staff', 'language': 'en',
        })
        coo = Technician.objects.get(full_name='Karim COO')
        coo.user.set_password('pass12345')
        coo.user.save()
        return coo

    def test_the_role_screen_lists_every_role(self):
        page = self.client.get('/tasks/roles/')
        keys = [column['key'] for column in page.context['columns']]
        self.assertIn(Technician.Role.PROJECT_MANAGER, keys)  # missing from the screen before
        self.assertIn(self.coo_role.key, keys)

    def test_a_coo_is_an_operations_manager_without_tickets(self):
        coo = self.add_coo()
        self.assertEqual((coo.role, coo.custom_role, coo.get_role_display()),
                         (Technician.Role.OPERATIONS_MANAGER, self.coo_role, 'COO'))
        # The Operations Manager's fixed rules: steps in on any task, sees every country.
        task = Task.objects.create(
            task_number='AE-0777', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        from tasks.views import can_step_in
        self.assertTrue(can_step_in(coo, task))
        self.assertTrue(coo.reaches_all_countries)
        # Its own switches: no Tickets.
        self.client.login(username='karim@example.com', password='pass12345')
        self.assertEqual(self.client.get('/tasks/tickets/').status_code, 403)
        self.assertEqual(self.client.get('/tasks/').status_code, 200)

    def test_a_coo_without_tickets_is_never_picked_for_requests(self):
        from people.models import NotificationSettings
        from tasks.alarms import new_ticket_people
        from tasks.forms import escalation_deciders
        coo = self.add_coo()
        settings_row = NotificationSettings.load()
        settings_row.new_ticket_roles = [Technician.Role.OPERATIONS_MANAGER]
        settings_row.save()
        self.assertNotIn(coo, list(new_ticket_people(self.country)))
        support = Technician.objects.create(
            user=__import__('django.contrib.auth.models', fromlist=['User']).User.objects.create_user('tsm9', password='x'),
            country=self.country, full_name='TSM', language='en', role=Technician.Role.SUPPORT_MANAGER,
            employment_type='staff',
        )
        self.assertNotIn(coo, list(escalation_deciders(support, self.country)))

    def test_a_role_in_use_cannot_be_deleted(self):
        self.add_coo()
        self.client.post('/tasks/roles/', {'action': 'delete_role', 'role': self.coo_role.pk})
        self.assertTrue(type(self.coo_role).objects.filter(pk=self.coo_role.pk).exists())
        Technician.objects.filter(custom_role=self.coo_role).update(custom_role=None)
        self.client.post('/tasks/roles/', {'action': 'delete_role', 'role': self.coo_role.pk})
        self.assertFalse(type(self.coo_role).objects.filter(pk=self.coo_role.pk).exists())
        self.assertFalse(RolePermission.objects.filter(role=self.coo_role.key).exists())


class StaffAccountSettingsTests(TaskTestCase):
    """My profile: a new email is confirmed by a link sent there, the old
    address is told, and the sign-in name follows; signing out of the app
    on every phone (website) or the other phones (app)."""

    def setUp(self):
        super().setUp()
        self.tech_user.username = self.tech_user.email = 'tech1@example.com'
        self.tech_user.save()
        self.client.login(username='tech1@example.com', password='pass12345')

    def link_in(self, message):
        import re
        return re.search(r'http://testserver(/tasks/my-profile/email/[^/\s]+/)', message.body).group(1)

    def test_a_new_email_changes_only_once_its_link_is_opened(self):
        from django.contrib.admin.models import LogEntry
        response = self.client.post('/tasks/my-profile/', {
            'action': 'change_email', 'email': 'new.tech@example.com', 'password': 'wrong',
        })
        self.assertIn('password', response.context['email_form'].errors)
        self.assertEqual(len(mail.outbox), 0)

        self.client.post('/tasks/my-profile/', {
            'action': 'change_email', 'email': 'new.tech@example.com', 'password': 'pass12345',
        })
        self.tech_user.refresh_from_db()
        self.assertEqual(self.tech_user.email, 'tech1@example.com')  # not yet
        self.assertEqual(mail.outbox[-1].to, ['new.tech@example.com'])
        link = self.link_in(mail.outbox[-1])

        self.client.logout()  # works signed out too
        self.client.get(link)
        self.tech_user.refresh_from_db()
        self.assertEqual((self.tech_user.email, self.tech_user.username), ('new.tech@example.com', 'new.tech@example.com'))
        self.assertEqual(mail.outbox[-1].to, ['tech1@example.com'])  # the old address is told
        self.assertIn('new.tech@example.com', mail.outbox[-1].body)
        self.assertIn('Staff email changed from tech1@example.com to new.tech@example.com',
                      LogEntry.objects.latest('pk').change_message)
        # Once only.
        self.client.get(link)
        self.assertTrue(self.client.login(username='new.tech@example.com', password='pass12345'))
        self.assertEqual(User.objects.filter(email='new.tech@example.com').count(), 1)

    def test_a_link_is_refused_once_the_email_is_taken(self):
        self.client.post('/tasks/my-profile/', {
            'action': 'change_email', 'email': 'new.tech@example.com', 'password': 'pass12345',
        })
        link = self.link_in(mail.outbox[-1])
        User.objects.create_user('new.tech@example.com', email='new.tech@example.com', password='x')
        self.client.get(link)
        self.tech_user.refresh_from_db()
        self.assertEqual(self.tech_user.email, 'tech1@example.com')

    def test_the_app_needs_the_password_for_a_new_email(self):
        token = Token.objects.create(user=self.tech_user)
        auth = {'HTTP_AUTHORIZATION': f'Token {token.key}'}
        response = self.client.post('/api/me/', {'email': 'new.tech@example.com', 'phone': '0501'}, **auth)
        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.json())
        self.technician.refresh_from_db()
        self.assertNotEqual(self.technician.phone, '0501')  # nothing saved
        response = self.client.post('/api/me/', {
            'email': 'new.tech@example.com', 'password': 'pass12345', 'phone': '0501',
        }, **auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn('new.tech@example.com', response.json()['notice'])
        self.tech_user.refresh_from_db()
        self.assertEqual(self.tech_user.email, 'tech1@example.com')
        self.assertEqual(mail.outbox[-1].to, ['new.tech@example.com'])

    def test_signing_out_of_the_phones(self):
        token = Token.objects.create(user=self.tech_user)
        PushDevice.objects.create(technician=self.technician, token='ExponentPushToken[this]', created_at=timezone.now())
        PushDevice.objects.create(technician=self.technician, token='ExponentPushToken[lost]', created_at=timezone.now())
        # From the app: the others go; this phone gets a fresh sign-in and keeps its notifications.
        response = self.client.post('/api/me/sign-out-others/', {'push_token': 'ExponentPushToken[this]'},
                                    HTTP_AUTHORIZATION=f'Token {token.key}')
        fresh = response.json()['token']
        self.assertNotEqual(fresh, token.key)
        self.assertFalse(Token.objects.filter(key=token.key).exists())
        self.assertEqual(list(PushDevice.objects.values_list('token', flat=True)), ['ExponentPushToken[this]'])
        # From the website: every phone.
        self.client.post('/tasks/my-profile/', {'action': 'sign_out_phones'})
        self.assertFalse(Token.objects.filter(user=self.tech_user).exists())
        self.assertFalse(PushDevice.objects.exists())

    def test_the_admin_cannot_give_someone_an_email_already_in_use(self):
        manager_user = User.objects.create_user('manager9', password='pass12345')
        manager = Technician.objects.create(user=manager_user, country=self.country, full_name='Manager', language='en',
                                            role=Technician.Role.MANAGER, employment_type='staff')
        self.client.login(username='manager9', password='pass12345')
        self.assertNotIn('email', self.client.get(f'/tasks/technicians/{self.technician.pk}/edit/').context['form'].fields)
        manager.role = Technician.Role.ADMIN  # their login email: the Admin's alone
        manager.save(update_fields=['role'])
        User.objects.create_user('taken@example.com', email='taken@example.com', password='x')
        self.client.login(username='manager9', password='pass12345')
        page = self.client.get(f'/tasks/technicians/{self.technician.pk}/edit/')
        form = page.context['form']
        data = {name: ('' if form[name].value() is None else form[name].value()) for name in form.fields if name != 'photo'}
        data.update({'email': 'taken@example.com', 'has_transport': '', 'can_carry_large': ''})
        response = self.client.post(f'/tasks/technicians/{self.technician.pk}/edit/', data)
        self.assertContains(response, 'An account with this email address already exists.')
        data['email'] = 'tech.renamed@example.com'
        self.client.post(f'/tasks/technicians/{self.technician.pk}/edit/', data)
        self.tech_user.refresh_from_db()
        self.assertEqual((self.tech_user.email, self.tech_user.username),
                         ('tech.renamed@example.com', 'tech.renamed@example.com'))
        self.assertEqual(mail.outbox[-1].to, ['tech1@example.com'])


class PartsNeededTests(TaskTestCase):
    """The spare parts a repair needs, before the visit (tasks.parts_needed):
    from our stock — held for it — or ordered, the customer reading
    "Waiting for parts" until they arrive."""

    def setUp(self):
        super().setUp()
        self.owner = Technician.objects.get(user=self.supervisor_user)
        self.task = Task.objects.create(
            task_number='AE-0901', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            responsible_supervisor=self.owner,
        )
        self.url = f'/tasks/{self.task.pk}/'
        self.client.login(username='supervisor1', password='pass12345')

    def add(self, **data):
        return self.client.post(self.url, {'action': 'add_part_needed', 'quantity': 1, 'source': 'stock', **data})

    def test_a_part_from_stock_is_held_for_the_repair(self):
        from reference.models import Part
        from tasks.stock import parts_held
        Part.objects.create(code='BELT-01', description='Running belt')
        self.add(product_code='BELT-01', quantity=2)
        line = self.task.parts_needed.get()
        self.assertEqual((line.description, line.quantity), ('Running belt', 2))  # the catalogue's description
        self.assertEqual(parts_held(self.country)['BELT-01'], [(self.task, 2)])

    def test_an_ordered_part_keeps_the_customer_waiting_until_it_arrives(self):
        from customers.progress import BEING_ARRANGED, progress_steps, visit_progress
        self.add(product_code='CONSOLE-9', description='Console board', source='ordered', expected_on='2026-11-20')
        progress = visit_progress(self.task)
        self.assertIn('20 November', str(progress[0]))
        self.assertEqual([state for _label, state in progress_steps(progress)][0], 'current')
        # Scheduled meanwhile: the task page warns.
        self.task.scheduled_date = timezone.localdate()
        self.task.save(update_fields=['scheduled_date'])
        self.assertTrue(self.client.get(self.url).context['scheduled_before_parts'])
        self.task.scheduled_date = None
        self.task.save(update_fields=['scheduled_date'])
        line = self.task.parts_needed.get()
        self.client.post(self.url, {'action': 'part_arrived', 'line': line.pk})
        self.assertEqual(visit_progress(self.task), BEING_ARRANGED)

    def test_the_warehouse_prepares_them_and_the_report_starts_with_them(self):
        self.add(product_code='BELT-01', description='Belt', quantity=2)
        warehouse = User.objects.create_user('wh1', password='pass12345')
        Technician.objects.create(user=warehouse, country=self.country, full_name='Wael Warehouse', language='en',
                                  role=Technician.Role.WAREHOUSE_MANAGER, employment_type='staff')
        self.client.login(username='wh1', password='pass12345')
        page = self.client.get('/tasks/parts-to-prepare/')
        self.assertEqual([row['task'] for row in page.context['rows']], [self.task])
        self.client.post(self.url, {'action': 'parts_prepared'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.parts_prepared_by, warehouse)
        self.assertEqual(self.client.get('/tasks/parts-to-prepare/').context['rows'], [])
        # A technician can't mark them, nor open the list.
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.client.get('/tasks/parts-to-prepare/').status_code, 403)
        # The lead sees them, and the report's "Parts used" starts with them.
        TaskAssignment.objects.create(task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now(), is_active=True)
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=['status'])
        self.assertContains(self.client.get(f'/tasks/my/{self.task.pk}/'), 'BELT-01')
        report = self.client.get(f'/tasks/my/{self.task.pk}/report/')
        self.assertEqual(report.context['part_formset'].initial[0]['part_code'], 'BELT-01')

    def test_only_its_owner_changes_them(self):
        self.client.login(username='tech1', password='pass12345')
        self.assertEqual(self.add(product_code='X-1').status_code, 403)
        self.assertFalse(self.task.parts_needed.exists())

    def test_the_customer_is_told_once_when_parts_are_ordered(self):
        self.site.contact_email = 'ali@example.com'
        self.site.save(update_fields=['contact_email'])
        self.add(product_code='BELT-01', description='Belt')  # from stock: nothing to tell
        self.assertEqual(mail.outbox, [])
        self.add(product_code='CONSOLE-9', description='Console board', source='ordered', expected_on='2026-11-20')
        (email,) = mail.outbox
        self.assertEqual(email.to, ['ali@example.com'])
        self.assertIn('Parts ordered', email.subject)
        self.assertIn('20 November 2026', email.body)
        self.assertIn('arrange the visit', email.body)
        self.add(product_code='MOTOR-2', description='Motor', source='ordered')  # a second one: not again
        self.assertEqual(len(mail.outbox), 1)


class PartsArrivalTests(PartsNeededTests):
    """The last ordered part in: "All parts are in", once, to the people on
    the task and the country's warehouse manager; correcting a part is a
    manager's; a part shipment keeps its own preparing step."""

    def setUp(self):
        super().setUp()
        self.warehouse = Technician.objects.create(
            user=User.objects.create_user('wh2', password='pass12345'), country=self.country, full_name='Wael Warehouse',
            language='en', role=Technician.Role.WAREHOUSE_MANAGER, employment_type='staff',
        )

    def arrivals(self):
        return list(self.task.events.filter(event_type=TaskEvent.EventType.PARTS_ARRIVED))

    def test_told_once_to_the_owner_and_the_warehouse(self):
        from tasks.models import TaskEventRecipient
        from tasks.updates import describe
        self.add(product_code='CONSOLE-9', source='ordered')
        self.add(product_code='BELT-01', source='ordered')
        first, second = self.task.parts_needed.all()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {'action': 'part_arrived', 'line': first.pk})
        self.assertEqual(self.arrivals(), [])  # one still on order
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {'action': 'part_arrived', 'line': second.pk})
        (event,) = self.arrivals()
        self.assertEqual(describe(event), 'All parts are in — schedule the visit')
        told = set(TaskEventRecipient.objects.filter(event=event).values_list('technician', flat=True))
        self.assertTrue({self.owner.pk, self.warehouse.pk} <= told)

    def test_received_at_the_warehouse_counts(self):
        from reference.models import TaskType
        from tasks.parts_needed import check_received
        self.add(product_code='CONSOLE-9', source='ordered')
        receiving = Task.objects.create(
            task_number='AE-0902', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.INTERNAL,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
            task_type=TaskType.objects.create(code='recv9', name='Receiving', name_ar='استلام', category='receiving'),
        )
        TaskProduct.objects.create(task=receiving, kind='part', product_code='CONSOLE-9', status='done', for_task=self.task)
        check_received(receiving, self.supervisor_user)
        self.assertEqual(len(self.arrivals()), 1)

    def _as_manager(self):
        manager = User.objects.create_user('manager6', password='pass12345')
        Technician.objects.create(user=manager, country=self.country, full_name='Mona Manager', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        self.client.login(username='manager6', password='pass12345')

    def _correct(self, line, **fields):
        data = {'action': 'edit_part_needed', 'line': line.pk, f'part{line.pk}-quantity': 1}
        data.update({f'part{line.pk}-{name}': value for name, value in fields.items()})
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, data)

    def test_the_last_ordered_part_corrected_to_stock_is_all_in(self):
        self.add(product_code='CONSOLE-9', source='ordered')
        line = self.task.parts_needed.get()
        self._as_manager()
        self._correct(line, product_code='CONSOLE-9', source='stock')
        self.assertEqual(len(self.arrivals()), 1)

    def test_a_part_corrected_to_ordered_tells_the_customer(self):
        self.site.contact_email = 'ali@example.com'
        self.site.save(update_fields=['contact_email'])
        self.add(product_code='BELT-01', description='Belt')
        self._as_manager()
        self._correct(self.task.parts_needed.get(), product_code='BELT-01', source='ordered')
        self.assertIn('Parts ordered', mail.outbox[-1].subject)

    def test_another_code_takes_the_catalogues_description(self):
        from reference.models import Part
        Part.objects.create(code='MOTOR-2', description='Drive motor')
        self.add(product_code='BELT-01', description='Belt')
        line = self.task.parts_needed.get()
        self._as_manager()
        self._correct(line, product_code='MOTOR-2', source='stock')
        line.refresh_from_db()
        self.assertEqual(line.description, 'Drive motor')

    def test_dates_in_the_bell_follow_the_readers_language(self):
        from tasks.updates import describe
        self.task.scheduled_date = date(2026, 11, 20)
        self.task.save(update_fields=['scheduled_date'])
        event = TaskEvent.objects.create(task=self.task, event_type=TaskEvent.EventType.PARTS_ARRIVED,
                                         occurred_at=timezone.now(), actor=self.supervisor_user)
        with translation.override('ar'):
            self.assertNotIn('Nov', describe(event))

    def test_correcting_a_part_is_a_managers_and_it_counts_as_new(self):
        from tasks.parts_needed import mark_prepared
        self.add(product_code='BELT-01', source='ordered')
        line = self.task.parts_needed.get()
        self.client.post(self.url, {'action': 'part_arrived', 'line': line.pk})
        mark_prepared(self.task, self.supervisor_user)
        edit = {'action': 'edit_part_needed', 'line': line.pk, f'part{line.pk}-product_code': 'BELT-02',
                f'part{line.pk}-quantity': 3, f'part{line.pk}-source': 'ordered'}
        self.assertEqual(self.client.post(self.url, edit).status_code, 403)  # the owner (a supervisor) can't
        manager = User.objects.create_user('manager5', password='pass12345')
        Technician.objects.create(user=manager, country=self.country, full_name='Mona Manager', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        self.client.login(username='manager5', password='pass12345')
        self.client.post(self.url, edit)
        line.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual((line.product_code, line.quantity, self.task.parts_prepared_at), ('BELT-02', 3, None))
        # It arrives again: told again.
        self.client.post(self.url, {'action': 'part_arrived', 'line': line.pk})
        self.assertEqual(len(self.arrivals()), 2)

    def test_a_part_shipment_keeps_its_own_preparing_step(self):
        from reference.models import TaskType
        from tasks.parts_needed import to_prepare
        from tasks.shipments import for_customer
        self.task.task_type = TaskType.objects.create(code='ship9', name='Spare Part Shipment', name_ar='شحن', category='part_shipment')
        self.task.save(update_fields=['task_type'])
        self.add(product_code='BELT-01', source='ordered', expected_on='2026-11-20')
        self.assertIn('20 November', str(for_customer(self.task)['progress'][0]))
        self.client.post(self.url, {'action': 'part_arrived', 'line': self.task.parts_needed.get().pk})
        self.assertEqual(to_prepare(self.country), [])
        self.assertFalse(self.client.get(self.url).context['can_prepare_parts'])


class VisitReminderTests(TaskTestCase):
    """The day before a visit, from 9 AM in its country: one reminder, only
    for a visit the customer was told of at least a week before
    (tasks.views.send_visit_reminders, run hourly)."""

    def setUp(self):
        super().setUp()
        self.site.contact_email = 'ali@example.com'
        self.site.save(update_fields=['contact_email'])
        dubai = ZoneInfo('Asia/Dubai')
        self.now = datetime(2026, 11, 12, 10, 0, tzinfo=dubai)
        self.task = Task.objects.create(
            task_number='AE-0950', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=self.now - timedelta(days=5),
            created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
            scheduled_for=datetime(2026, 11, 13, 14, 30, tzinfo=dubai),
            schedule_notified_at=self.now - timedelta(days=7),
        )

    def remind(self, now=None):
        from tasks.views import send_visit_reminders
        return send_visit_reminders(now or self.now)

    def test_reminded_once_the_day_before(self):
        self.assertEqual(self.remind(), 1)
        (email,) = mail.outbox
        self.assertIn('Reminder: your visit tomorrow', email.subject)
        self.assertIn('2:30 PM', email.body)
        self.assertEqual(self.remind(self.now + timedelta(hours=1)), 0)

    def test_not_before_nine_in_the_morning(self):
        self.assertEqual(self.remind(self.now.replace(hour=8)), 0)

    def test_not_for_a_visit_planned_less_than_a_week_before(self):
        self.task.schedule_notified_at = self.now - timedelta(days=5)  # 6 days before it
        self.task.save(update_fields=['schedule_notified_at'])
        self.assertEqual(self.remind(), 0)

    def test_not_for_a_time_they_were_not_told(self):
        # Moved after they were told, without telling them again.
        TaskEvent.objects.create(task=self.task, event_type=TaskEvent.EventType.RESCHEDULED,
                                 occurred_at=self.now - timedelta(days=1), actor=self.supervisor_user)
        self.assertEqual(self.remind(), 0)

    def test_not_for_a_visit_they_were_never_told_of(self):
        self.task.schedule_notified_at = None
        self.task.save(update_fields=['schedule_notified_at'])
        self.assertEqual(self.remind(), 0)


class SearchAsPeopleTypeTests(TaskTestCase):
    """Search finds names with Arabic spelling variants folded, and phone numbers by their
    digits whatever the format (spots/indexes.py: __matches, __phone)."""

    def setUp(self):
        super().setUp()
        self.customer.name = 'نادي أحمد الرياضى'
        self.customer.contact_phone = '+971 50 123 4567'
        self.customer.save(update_fields=['name', 'contact_phone'])
        self.task = Task.objects.create(
            task_number='AE-0777', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )

    def found(self, text):
        from tasks.views import search_tasks
        return list(search_tasks(Task.objects.all(), text))

    def test_arabic_spelling_variants_find_each_other(self):
        self.assertEqual(self.found('احمد'), [self.task])     # أ written as ا
        self.assertEqual(self.found('الرياضي'), [self.task])  # ى written as ي
        self.assertEqual(self.found('Marina'), [self.task])   # English as before
        self.assertEqual(self.found('marina branch'), [self.task])

    def test_a_phone_number_in_any_format(self):
        for typed in ('0501234567', '050 123 4567', '+971501234567', '50-123-4567'):
            self.assertEqual(self.found(typed), [self.task], typed)
        self.assertEqual(self.found('050'), [])  # too few digits to mean a number

    def test_a_request_by_its_contact_phone(self):
        from tasks.views import search_tickets
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0777', customer=self.customer, site=self.site,
            company_name='Fitness First', contact_name='علي', contact_phone='+971 55 765 4321',
            description='-', submitted_at=timezone.now(),
        )
        self.assertEqual(list(search_tickets(CustomerTicket.objects.all(), '055 765 4321')), [ticket])
        self.assertEqual(list(search_tickets(CustomerTicket.objects.all(), 'على')), [ticket])
