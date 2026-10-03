import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import (
    RELIABLE_LEVEL, NotificationSettings, RolePermission, Technician, TechnicianConduct,
    TechnicianConductAssessment, TechnicianSkill, TechnicianSkillAssessment,
)
from reference.models import Brand, ConductArea, Country, Skill, TaskType
from reports.models import CustomerFeedback, PartUsed, WorkReport

from .models import (
    CustomerTicket, ScheduleChangeRequest, Task, TaskAssignment, TaskAsset, TaskAttachment, TaskEvent,
    TaskMessage, TaskMessageRecipient, TaskNotification, TicketInternalNote, TicketNotification, TicketReply,
)

User = get_user_model()


def dubai_time(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo('Asia/Dubai'))


class TaskTestCase(TestCase):
    """Shared reference/customer/people fixtures for the tasks screens."""

    def setUp(self):
        self.country = Country.objects.create(
            name='UAE', name_ar='الإمارات', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
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
            media_type=TaskAttachment.MediaType.PHOTO, purpose=TaskAttachment.Purpose.FAULT,
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

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertEqual(response.status_code, 302)

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
        self.assertContains(response, 'Set a scheduled time')
        self.assertEqual(len(mail.outbox), 0)

    def test_notify_requires_a_contact_email(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_schedule'}, follow=True)

        self.task.refresh_from_db()
        self.assertIsNone(self.task.schedule_notified_at)
        self.assertContains(response, 'Add a contact email')
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

        self.assertContains(response, 'Explain the reason for the delay')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.task.events.filter(event_type=TaskEvent.EventType.DELAY_NOTICE).exists())

    def test_delay_notice_requires_a_contact_email(self):
        self.task.scheduled_for = dubai_time(2026, 9, 20, 9, 0)
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(
            f'/tasks/{self.task.pk}/', {'action': 'notify_delay', 'delay_reason': 'Traffic'}, follow=True,
        )

        self.assertContains(response, 'Add a contact email')
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
        self.assertContains(response, 'Add a contact email')

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

    def test_min_level_above_scale_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(min_level='5'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('min_level'))

    def test_rescheduling_does_not_auto_notify_when_setting_is_off(self):
        self.site.contact_email = 'manager@fitnessfirst.example'
        self.site.save()

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

    def test_resubmitting_products_replaces_the_previous_set(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'PNL-OLD', 'products-0-serial_number': '', 'products-0-quantity': '1',
        }))
        self.assertEqual(self.task.products.count(), 1)

        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'PNL-NEW', 'products-0-serial_number': '', 'products-0-quantity': '3',
        }))

        products = list(self.task.products.all())
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].product_code, 'PNL-NEW')
        self.assertEqual(products[0].quantity, 3)

    def test_product_row_saves_the_installation_checklist_fields(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'BENCH-01', 'products-0-serial_number': 'SN-1',
            'products-0-quantity': '1', 'products-0-replacement': 'Swapped for BENCH-02 (out of stock)',
            'products-0-frame': 'Matte black', 'products-0-arm': 'Red',
            'products-0-padding': 'Charcoal', 'products-0-trim': 'Silver',
            'products-0-comment': 'Customer requested custom colors.', 'products-0-note': 'Fragile — handle with care.',
        }))
        self.assertEqual(response.status_code, 302)

        product = self.task.products.get()
        self.assertEqual(product.replacement, 'Swapped for BENCH-02 (out of stock)')
        self.assertEqual(product.frame, 'Matte black')
        self.assertEqual(product.arm, 'Red')
        self.assertEqual(product.padding, 'Charcoal')
        self.assertEqual(product.trim, 'Silver')
        self.assertEqual(product.comment, 'Customer requested custom colors.')
        self.assertEqual(product.note, 'Fragile — handle with care.')

    def test_deleting_a_product_row_via_checkbox(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1),
            'products-0-product_code': 'BENCH-01', 'products-0-serial_number': 'SN-1',
            'products-0-quantity': '1', 'products-0-frame': 'Matte black',
        }))
        self.assertEqual(self.task.products.count(), 1)

        # Ticking Delete removes the row outright — no need to also blank
        # out product_code or any of its other fields by hand.
        response = self.client.post(self.url, self._payload(**{
            **self._management_form('products', 1, initial=1),
            'products-0-product_code': 'BENCH-01', 'products-0-serial_number': 'SN-1',
            'products-0-quantity': '1', 'products-0-frame': 'Matte black',
            'products-0-DELETE': 'on',
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.task.products.count(), 0)

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
        self.assertContains(response, 'not locked')

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
        self.assertContains(response, 'Add a shipping tracking number')

    def test_requires_a_contact_email(self):
        self.task.shipping_tracking_number = 'TRACK-99'
        self.task.save()

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'notify_shipping'}, follow=True)

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'Add a contact email')

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

    def test_manager_approves_and_closes(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'approve_report'})
        self.assertEqual(response.status_code, 302)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)

        event = self.task.events.get(event_type=TaskEvent.EventType.REPORT_APPROVED)
        self.assertEqual(event.actor, self.manager_user)

    def test_cannot_approve_a_task_with_no_report_awaiting_approval(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()

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

    def test_close_works_from_new(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'close', 'close_reason': 'Advice given by phone.'})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)
        self.assertEqual(self.ticket.close_reason, 'Advice given by phone.')
        self.assertEqual(self.ticket.reviewed_by, self.support_user)

    def test_close_works_from_dismissed_too(self):
        # Close reaches a ticket from any status but already-closed — a
        # dismissed one can still be closed instead, no reopen needed first.
        self.ticket.status = CustomerTicket.Status.DISMISSED
        self.ticket.dismissal_reason = 'Wrong call.'
        self.ticket.save(update_fields=['status', 'dismissal_reason'])

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'close', 'close_reason': 'Actually handled by phone.'})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_close_works_from_converted_too(self):
        self.ticket.status = CustomerTicket.Status.CONVERTED
        self.ticket.save(update_fields=['status'])

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'close', 'close_reason': 'Closing it out too.'})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_cannot_close_an_already_closed_ticket(self):
        self.ticket.status = CustomerTicket.Status.CLOSED
        self.ticket.close_reason = 'Already handled.'
        self.ticket.save(update_fields=['status', 'close_reason'])

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {'action': 'close', 'close_reason': 'Trying again.'})
        self.assertEqual(response.status_code, 200)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.close_reason, 'Already handled.')

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
        self.assertContains(response, 'Add a shipping tracking number')

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
        self.assertContains(response, 'only an admin can change it')

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
        self.assertContains(response, 'as admin, you can still change it')

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
    def test_support_manager_can_attach_a_file_to_an_internal_note(self):
        photo = SimpleUploadedFile('vendor_quote.pdf', b'%PDF-1.4 not a real pdf', content_type='application/pdf')

        self.client.login(username='support1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'add_internal_note', 'message': 'Vendor quote attached.', 'attachment': photo,
        })
        self.assertEqual(response.status_code, 302)

        note = TicketInternalNote.objects.get(ticket=self.ticket)
        self.assertTrue(note.attachment.name.endswith('.pdf'))

        response = self.client.get(self.url)
        self.assertContains(response, 'vendor_quote')

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
        self.assertIn('New reply on your ticket', mail.outbox[0].subject)
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
            'action': 'add_reply', 'message': 'Please see the attached quotation.', 'attachment': quote_file,
        })
        self.assertEqual(response.status_code, 302)

        self.assertEqual(TicketReply.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('New reply on your ticket', mail.outbox[0].subject)
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

    def test_customer_reply_creates_a_new_reply_notification(self):
        portal_user = User.objects.create_user('fitnessfirst_portal2', password='pass12345')
        self.customer.user = portal_user
        self.customer.save(update_fields=['user'])
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
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])
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

    def test_a_different_customers_login_gets_403(self):
        other_customer = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        other_user = User.objects.create_user('other_portal', password='pass12345')
        other_customer.user = other_user
        other_customer.save(update_fields=['user'])

        self.client.login(username='other_portal', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

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
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )

    def test_support_manager_sees_the_unseen_ticket_notification(self):
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

    def test_support_manager_does_not_see_task_notifications(self):
        # The technical support manager isn't manager-tier, so the task
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

    def test_admin_sees_both_halves_of_the_bell(self):
        # Admin is a superset of both — manage_tickets and is_manager_tier
        # — so it's the one role that sees everything together.
        task = Task.objects.create(
            task_number='AE-0001', site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        TaskNotification.objects.create(task=task, created_at=timezone.now())

        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertEqual(response.context['unseen_notification_count'], 2)
        kinds = {item['kind'] for item in response.context['unseen_notifications']}
        self.assertIn('new_task', kinds)

    def test_plain_manager_does_not_see_an_all_tickets_link(self):
        # Found live: this link used to sit under is_manager_tier instead
        # of manage_tickets, so a plain manager saw a link to a screen
        # that then 403'd them.
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertNotContains(response, '/tasks/tickets/all/')

    def test_support_manager_sees_the_all_tickets_link(self):
        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertContains(response, '/tasks/tickets/all/')

    def test_supervisor_gets_no_bell_context_at_all(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/')
        self.assertNotIn('unseen_notification_count', response.context)

    def test_technician_gets_no_bell_context_at_all(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')
        self.assertNotIn('unseen_notification_count', response.context)

    def test_a_different_countrys_notification_does_not_count(self):
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

        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.context['unseen_notification_count'], 1)

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
        response = self.client.get('/tasks/my-week/')

        self.assertEqual(list(response.context['unscheduled']), [task])
        self.assertEqual(response.context['unscheduled'][0].my_role, 'responsible')

    def test_unscheduled_open_task_of_mine_appears(self):
        task = self._make_task('AE-0001', scheduled_for=None, status=Task.Status.NEW)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-week/')

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
        response = self.client.get('/tasks/my-week/')

        self.assertEqual(list(response.context['unscheduled']), [task])

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
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-skills/')
        self.assertEqual(response.status_code, 302)

    def _evidence(self, name='proof.jpg', content=b'not a real image', content_type='image/jpeg'):
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

    def test_cannot_self_rate_a_skill_twice(self):
        TechnicianSkill.objects.create(
            technician=self.technician, skill=self.skill, level=2, source=TechnicianSkill.Source.SELF,
            set_by=self.technician, set_on=timezone.now().date(),
        )

        self.client.login(username='tech1', password='pass12345')
        self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 4,
        })

        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.level, 2)

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

    def test_shows_supervisors_and_managers_in_the_country(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_supervisor_user = User.objects.create_user('egypt_sup', password='pass12345')
        Technician.objects.create(
            user=other_supervisor_user, country=other_country, full_name='Nour Cairo',
            language='ar', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')

        names = {s.full_name for s in response.context['supervisors']}
        self.assertEqual(names, {'Sara Super', 'Mona Manager'})

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
        photo = SimpleUploadedFile('me.jpg', b'not a real image', content_type='image/jpeg')
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

    def test_technician_updates_own_phone_and_email(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-profile/', {
            'action': 'save_profile', 'language': Technician.Language.EN,
            'phone': '0501234567', 'email': 'tarek@example.com',
        })
        self.assertEqual(response.status_code, 302)

        self.technician.refresh_from_db()
        self.tech_user.refresh_from_db()
        self.assertEqual(self.technician.phone, '0501234567')
        self.assertEqual(self.tech_user.email, 'tarek@example.com')

    def test_get_prefills_email_from_the_linked_user(self):
        self.tech_user.email = 'tarek@example.com'
        self.tech_user.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-profile/')
        self.assertEqual(response.context['profile_form'].fields['email'].initial, 'tarek@example.com')

    def test_technician_changes_own_password(self):
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

        self.client.logout()
        self.assertTrue(self.client.login(username='tech1', password='new-pass-98765'))

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
        self.assertContains(response, 'No technicians match that search.')


class DashboardTests(TaskTestCase):
    def _make_task(self, number, **overrides):
        fields = dict(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.NEW,
        )
        fields.update(overrides)
        return Task.objects.create(**fields)

    def test_technician_can_view_it_too(self):
        # Every role has view_dashboard now — it's the universal landing
        # page (spots.views.home) — so a technician gets a normal 200,
        # not the 403 this used to be.
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/dashboard/')
        self.assertEqual(response.status_code, 200)

    def test_shows_technician_availability(self):
        self.technician.is_available = False
        self.technician.unavailable_reason = Technician.UnavailableReason.SICK
        self.technician.save()

        self.client.login(username='supervisor1', password='pass12345')
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

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/dashboard/')

        self.assertEqual(list(response.context['technicians']), [self.technician])

    def test_active_task_count_reflects_open_assignments(self):
        task = self._make_task('AE-0001', status=Task.Status.ASSIGNED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        closed_task = self._make_task('AE-0002', status=Task.Status.CLOSED)
        TaskAssignment.objects.create(
            task=closed_task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/dashboard/')

        technician = response.context['technicians'][0]
        self.assertEqual(technician.active_task_count, 1)

    def test_shows_open_tasks_with_lead_and_schedule(self):
        task = self._make_task('AE-0001', scheduled_for=dubai_time(2026, 9, 9, 9, 0), status=Task.Status.ASSIGNED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/dashboard/')

        tasks = list(response.context['tasks'])
        self.assertEqual(tasks, [task])
        self.assertEqual(tasks[0].lead_technician, self.technician)

    def test_closed_task_excluded(self):
        self._make_task('AE-0001', status=Task.Status.CLOSED)

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/dashboard/')

        self.assertEqual(list(response.context['tasks']), [])

    def test_other_country_task_excluded(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek Branch', address='Cairo')
        self._make_task('EG-0001', site=other_site, status=Task.Status.NEW)

        self.client.login(username='supervisor1', password='pass12345')
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
        evidence = SimpleUploadedFile('proof.jpg', b'not a real image', content_type='image/jpeg')
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
        }
        payload.update(overrides)
        return payload

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

    def test_created_in_the_admins_active_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )

        self.client.login(username='admin1', password='pass12345')
        self.client.post('/tasks/active-country/', {'country': other_country.pk})

        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 302)

        technician = Technician.objects.get(full_name='Nour New')
        self.assertEqual(technician.country, other_country)


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

    def _photo(self, name='photo.jpg', content=b'not a real image', content_type='image/jpeg'):
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
        # A real, unguessable password was set — not left blank/unusable.
        self.assertTrue(self.helper.user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['hani@example.com'])
        self.assertIn('hani@example.com', mail.outbox[0].body)

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

    def test_manager_can_reset_an_existing_password(self):
        # self.technician (tech1) already has a login, from TaskTestCase.
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(f'/tasks/technicians/{self.technician.pk}/edit/', {
            'action': 'reset_password', 'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertEqual(response.status_code, 302)
        self.tech_user.refresh_from_db()
        self.assertTrue(self.tech_user.check_password('br4nd-New-Pass!'))

    def test_no_login_form_once_one_exists(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/tasks/technicians/{self.technician.pk}/edit/')
        self.assertIsNone(response.context['login_form'])
        self.assertIsNotNone(response.context['password_form'])

    def test_no_password_form_before_one_exists(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(self.url)
        self.assertIsNotNone(response.context['login_form'])
        self.assertIsNone(response.context['password_form'])


class SkillListTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Maya Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
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

    def test_notification_setting_defaults_to_off(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertFalse(response.context['notification_settings'].auto_notify_on_reschedule)

    def test_admin_can_turn_on_auto_notify(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/tasks/roles/', {
            'action': 'save_notifications', 'auto_notify_on_reschedule': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(NotificationSettings.load().auto_notify_on_reschedule)

    def test_admin_can_turn_off_auto_notify(self):
        settings = NotificationSettings.load()
        settings.auto_notify_on_reschedule = True
        settings.save()

        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/tasks/roles/', {'action': 'save_notifications'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(NotificationSettings.load().auto_notify_on_reschedule)

    def test_saving_notifications_does_not_touch_the_permission_matrix(self):
        RolePermission.objects.filter(
            role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS,
        ).update(allowed=True)

        self.client.login(username='admin1', password='pass12345')
        self.client.post('/tasks/roles/', {'action': 'save_notifications', 'auto_notify_on_reschedule': 'on'})

        self.assertTrue(
            RolePermission.objects.filter(
                role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS, allowed=True,
            ).exists(),
        )

    def test_technician_cannot_change_notification_setting(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/roles/', {
            'action': 'save_notifications', 'auto_notify_on_reschedule': 'on',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(NotificationSettings.load().auto_notify_on_reschedule)


class MonthlyReportTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
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
        self.assertEqual(response.context['ticket_total'], 2)
        self.assertEqual(response.context['task_total'], 2)
        self.assertContains(response, 'Cairo Gym')
        self.assertNotContains(response, 'Old Ticket')

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
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
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

    def test_new_customer_is_created_in_the_active_country(self):
        # customer_create is admin-only (see customers.views), so this
        # exercises the active-country switch and the creation itself
        # under the same admin login.
        self.client.login(username='admin1', password='pass12345')
        self.client.post('/tasks/active-country/', {'country': self.egypt.pk})

        response = self.client.post('/customers/new/', {'name': 'Nile Gym', 'segment': 'gym'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.get(name='Nile Gym').country, self.egypt)


class HomeRedirectTests(TaskTestCase):
    # The dashboard is now everyone's landing page, regardless of role —
    # see spots.views.home and migration 0024_dashboard_is_the_default_landing_page.

    def test_supervisor_lands_on_dashboard(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_technician_lands_on_dashboard(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_plain_manager_lands_on_dashboard(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_support_manager_lands_on_dashboard(self):
        support_user = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=support_user, country=self.country, full_name='Sara Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.client.login(username='support1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_admin_lands_on_dashboard(self):
        admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.client.login(username='admin1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_customer_lands_on_portal_home(self):
        portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = portal_user
        self.customer.save(update_fields=['user'])
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
        self.customer.user = portal_user
        self.customer.must_change_password = True
        self.customer.save(update_fields=['user', 'must_change_password'])
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
        self.assertRedirects(response, '/tasks/dashboard/')

    def test_submitting_sets_the_password_and_clears_the_flag(self):
        response = self.client.post('/tasks/first-login/', {
            'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertRedirects(response, '/tasks/dashboard/')

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
        self.customer.user = portal_user
        self.customer.save(update_fields=['user'])

        self.client.login(username='fitnessfirst_portal', password='pass12345')
        response = self.client.post('/i18n/setlang/', {'language': 'ar', 'next': '/customers/portal/'})
        self.assertRedirects(response, '/customers/portal/', fetch_redirect_response=False)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.language, 'ar')

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
        upload = SimpleUploadedFile('serial.jpg', b'fake-image-bytes', content_type='image/jpeg')

        response = self.client.post(self.url, {
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.SERIAL_PLATE,
        })
        self.assertEqual(response.status_code, 302)

        attachment = self.task.attachments.get()
        self.assertEqual(attachment.media_type, TaskAttachment.MediaType.PHOTO)
        self.assertEqual(attachment.purpose, TaskAttachment.Purpose.SERIAL_PLATE)
        self.assertEqual(attachment.storage_kind, TaskAttachment.StorageKind.FILE)
        self.assertEqual(attachment.uploaded_by, self.tech_user)
        self.assertTrue(attachment.url.startswith('http'))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_helper_can_upload_a_photo(self):
        self.client.login(username='helper1', password='pass12345')
        upload = SimpleUploadedFile('before.jpg', b'fake-image-bytes', content_type='image/jpeg')

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
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.FAULT,
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
            'action': 'upload', 'file': upload, 'purpose': TaskAttachment.Purpose.FAULT,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.task.attachments.count(), 0)


class MyReportFormTests(TaskTestCase):
    EXISTING_ASSET_ROWS = 4
    NEW_ASSET_ROWS = 4
    PART_ROWS = 5

    def setUp(self):
        super().setUp()
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

        signature = SimpleUploadedFile('sig.png', b'png bytes', content_type='image/png')
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

    def test_resubmitting_after_approval_stays_closed(self):
        self.client.login(username='tech1', password='pass12345')
        self.client.post(self.url, self._base_payload())

        self.task.refresh_from_db()
        self.task.status = Task.Status.CLOSED
        self.task.save(update_fields=['status'])

        self.client.post(self.url, self._base_payload(findings='Belt worn out, fixed again'))

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.COMPLETED).count(), 1)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.REPORT_SUBMITTED).count(), 2)

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
        payload['new-0-outcome'] = TaskAsset.Outcome.INSPECTED_OK
        self.client.post(self.url, payload)

        new_asset = Asset.objects.get(model_name='Excite Run 900')
        self.assertEqual(new_asset.site, self.site)
        self.assertEqual(new_asset.serial_no, 'SN-42')
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

    def test_invalid_currency_code_is_rejected(self):
        self.client.login(username='tech1', password='pass12345')
        payload = self._base_payload()
        payload['parts-0-part_code'] = 'BELT-42'
        payload['parts-0-quantity'] = '1'
        payload['parts-0-unit_cost'] = '85.00'
        payload['parts-0-currency_code'] = 'aed'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())

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
        signature = SimpleUploadedFile('sig.png', b'fake-png-bytes', content_type='image/png')
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
        self.assertEqual(str(response.context['total_hours']), '3.50')

    def test_helper_gets_a_count_not_the_hours(self):
        self.client.login(username='supervisor1', password='pass12345')
        row = self._row(self.client.get(self.url), self.helper)
        self.assertEqual(row['hours'], 0)
        self.assertEqual(row['helped_on'], 1)

    def test_other_months_and_countries_are_excluded(self):
        self._task_with_report('AE-0003', hours='5.00', submitted_at=timezone.now() - timedelta(days=62))
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Zamalek', address='Cairo')
        self._task_with_report('EG-0001', hours='9.00', site=other_site)

        self.client.login(username='supervisor1', password='pass12345')
        row = self._row(self.client.get(self.url), self.technician)
        self.assertEqual(str(row['hours']), '3.50')

    def test_earlier_month_is_reachable(self):
        earlier = timezone.now() - timedelta(days=62)
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
        self.assertContains(response, 'Well over estimate')

    def test_task_detail_flags_an_overrun_report(self):
        task = self._task_with_report('AE-0011', hours='5.00', estimated='2.00')
        self.client.login(username='supervisor1', password='pass12345')
        self.assertContains(self.client.get(f'/tasks/{task.pk}/'), 'Well over estimate')
        self.assertNotContains(self.client.get(f'/tasks/{self.task.pk}/'), 'Well over estimate')

    def test_technician_sees_own_month_hours_on_my_progress(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.context['month_hours']), '3.50')
        self.assertEqual(response.context['month_report_count'], 2)

    def test_helper_gets_no_hours_on_my_progress(self):
        self.client.login(username='helper1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertEqual(response.context['month_hours'], 0)

    def test_csv_export_has_summary_and_report_rows(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/technicians/hours/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.decode()
        self.assertIn('Tarek Tech,2,3.50,2.00,0,0', body)
        self.assertIn('Omar Helper,0,0,,0,1', body)
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
        self.assertContains(response, 'Not scheduled after 24 working hours')
        self.assertEqual(response.context['unseen_notifications'][0]['kind'], 'unscheduled_overdue')
