import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import (
    NotificationSettings, RolePermission, Technician, TechnicianConduct, TechnicianConductAssessment,
    TechnicianSkill, TechnicianSkillAssessment,
)
from reference.models import Brand, ConductArea, Country, Skill, TaskType
from reports.models import PartUsed, WorkReport

from .models import CustomerTicket, Task, TaskAssignment, TaskAsset, TaskAttachment, TaskEvent

User = get_user_model()


def dubai_time(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo('Asia/Dubai'))


class TaskTestCase(TestCase):
    """Shared reference/customer/people fixtures for the tasks screens."""

    def setUp(self):
        self.country = Country.objects.create(
            name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
        )
        self.brand = Brand.objects.create(name='Technogym')
        self.skill = Skill.objects.create(brand=self.brand, name='Treadmill repair')
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

    def _payload(self, **overrides):
        payload = {
            'priority': Task.Priority.HIGH, 'source': Task.Source.PHONE,
            'billing_type': Task.BillingType.CONTRACT, 'description': 'Belt making noise', 'is_warranty': '',
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

    def test_filters_carry_across_status_tabs(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/', {'status': 'all', 'customer': self.customer.pk})

        self.assertContains(response, f'customer={self.customer.pk}')


class TaskCreateTests(TaskTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/new/')
        self.assertEqual(response.status_code, 403)

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
        self.client.login(username='supervisor1', password='pass12345')
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
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            new_site_customer=self.customer.pk, new_site_name=self.site.name,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_site_name'))

    def test_site_and_new_site_together_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
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

    def test_new_skill_wanted_creates_skill_for_the_chosen_brand(self):
        self.client.login(username='supervisor1', password='pass12345')
        other_brand = Brand.objects.create(name='Life Fitness')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, brand=other_brand.pk, new_skill_wanted='on',
        ))
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.required_skill.brand, other_brand)
        self.assertEqual(task.required_skill.name, other_brand.name)

    def test_new_skill_wanted_with_new_brand_uses_the_newly_created_brand(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_brand_name='Life Fitness', new_skill_wanted='on',
        ))
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.required_skill.brand, task.brand)
        self.assertEqual(task.brand.name, 'Life Fitness')

    def test_new_skill_wanted_without_a_brand_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/tasks/new/', self._base_new_task_payload(
            site=self.site.pk, new_skill_wanted='on',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(response.context['form'].errors.get('new_skill_wanted'))

    def test_oversized_new_site_name_is_rejected_cleanly(self):
        # Site.name is max_length=150 — this must fail as a normal form
        # error, not crash with a database "value too long" error.
        self.client.login(username='supervisor1', password='pass12345')
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
    def _payload(self, **overrides):
        payload = {
            'country': self.country.pk, 'company_name': 'Fitness First', 'site_description': 'Marina Branch',
            'contact_name': 'Ali Manager', 'contact_phone': '0501234567', 'contact_email': '',
            'description': 'The treadmill belt is squeaking loudly.',
        }
        payload.update(overrides)
        return payload

    def test_anonymous_can_view_the_form(self):
        response = self.client.get('/tasks/tickets/new/')
        self.assertEqual(response.status_code, 200)

    def test_submitting_creates_a_new_ticket_and_redirects_to_thank_you(self):
        response = self.client.post('/tasks/tickets/new/', self._payload())
        self.assertRedirects(response, '/tasks/tickets/new/thank-you/')

        ticket = CustomerTicket.objects.get()
        self.assertEqual(ticket.company_name, 'Fitness First')
        self.assertEqual(ticket.country, self.country)
        self.assertEqual(ticket.status, CustomerTicket.Status.NEW)
        self.assertIsNotNone(ticket.submitted_at)

    def test_missing_description_is_rejected(self):
        response = self.client.post('/tasks/tickets/new/', self._payload(description=''))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CustomerTicket.objects.count(), 0)

    def test_thank_you_page_is_public(self):
        response = self.client.get('/tasks/tickets/new/thank-you/')
        self.assertEqual(response.status_code, 200)


class TicketListTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = CustomerTicket.objects.create(
            country=self.country, company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_sees_new_tickets_by_default(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/tickets/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fitness First')

    def test_other_country_ticket_not_shown(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        CustomerTicket.objects.create(
            country=other_country, company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/tickets/', {'status': 'all'})
        self.assertNotContains(response, 'Cairo Gym')


class TicketReviewTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = CustomerTicket.objects.create(
            country=self.country, company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali Manager', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        self.url = f'/tasks/tickets/{self.ticket.pk}/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_other_country_ticket_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_ticket = CustomerTicket.objects.create(
            country=other_country, company_name='Cairo Gym', site_description='Zamalek',
            contact_name='Nour', contact_phone='0100000000',
            description='Something broke.', submitted_at=timezone.now(),
        )

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/tasks/tickets/{other_ticket.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_dismiss_requires_a_reason(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'dismiss', 'dismissal_reason': ''})
        self.assertEqual(response.status_code, 200)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)

    def test_dismiss_sets_status_and_reason(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'dismiss', 'dismissal_reason': 'Duplicate report.'})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.DISMISSED)
        self.assertEqual(self.ticket.dismissal_reason, 'Duplicate report.')
        self.assertEqual(self.ticket.reviewed_by, self.supervisor_user)

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

    def test_supervisor_assigns_a_technician(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'assign', 'assigned_to': self.technician.pk})
        self.assertEqual(response.status_code, 302)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, self.technician)
        self.assertIsNotNone(self.ticket.assigned_at)

    def test_assignee_can_view_but_not_dismiss_or_convert(self):
        self.ticket.assigned_to = self.technician
        self.ticket.save()

        self.client.login(username='tech1', password='pass12345')
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


class MyTicketsTests(TaskTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-tickets/')
        self.assertEqual(response.status_code, 302)

    def test_shows_only_tickets_assigned_to_me(self):
        mine = CustomerTicket.objects.create(
            country=self.country, company_name='Fitness First', site_description='Marina Branch',
            contact_name='Ali', contact_phone='0501234567', description='Belt squeaking.',
            submitted_at=timezone.now(), assigned_to=self.technician,
        )
        CustomerTicket.objects.create(
            country=self.country, company_name='Gold Gym', site_description='JBR Branch',
            contact_name='Sam', contact_phone='0509999999', description='AC not working.',
            submitted_at=timezone.now(),
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-tickets/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['tickets']), [mine])


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

    def test_pending_reports_count_counts_unreviewed_report_on_my_lead_task(self):
        task = self._make_task('AE-0001', status=Task.Status.COMPLETED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=task, findings='Belt worn', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertEqual(response.context['pending_reports_count'], 1)

    def test_pending_reports_count_excludes_approved_report(self):
        task = self._make_task('AE-0001', status=Task.Status.CLOSED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=task, findings='Belt worn', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(), approved_at=timezone.now(),
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertEqual(response.context['pending_reports_count'], 0)

    def test_pending_reports_count_excludes_rejected_report(self):
        task = self._make_task('AE-0001', status=Task.Status.COMPLETED)
        TaskAssignment.objects.create(
            task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=task, findings='Belt worn', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(), rejection_reason='Missing signature',
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertEqual(response.context['pending_reports_count'], 0)

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

    def test_solve_rate_is_none_without_any_submitted_report(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertIsNone(response.context['solve_rate'])

    def test_solve_rate_counts_approved_over_submitted(self):
        for i, approved in enumerate([True, True, False]):
            task = Task.objects.create(
                task_number=f'AE-000{i}', site=self.site, priority=Task.Priority.NORMAL,
                source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
                reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.COMPLETED,
            )
            TaskAssignment.objects.create(
                task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                assigned_at=timezone.now(), is_active=True,
            )
            WorkReport.objects.create(
                task=task, findings='Belt worn', resolved=True, labour_hours='1.00', customer_name='Ali',
                submitted_at=timezone.now(), approved_at=timezone.now() if approved else None,
            )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        self.assertAlmostEqual(response.context['solve_rate'], 2 / 3)

    def test_ninety_day_progress_is_none_without_a_hire_date(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')
        self.assertIsNone(response.context['ninety_day'])

    def test_on_track_when_fully_certified_well_within_90_days(self):
        Skill.objects.exclude(pk=self.skill.pk).filter(category=Skill.Category.OTHER).update(is_active=False)
        self.technician.hired_on = timezone.localtime().date() - timedelta(days=10)
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
        self.technician.hired_on = timezone.localtime().date() - timedelta(days=60)
        self.technician.save()

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/my-progress/')

        ninety_day = response.context['ninety_day']
        self.assertFalse(ninety_day['on_track'])
        self.assertEqual(ninety_day['days_remaining'], 30)


class MySkillsTests(TaskTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/tasks/my-skills/')
        self.assertEqual(response.status_code, 302)

    def test_self_rate_skill_creates_self_sourced_rating(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.post('/tasks/my-skills/', {
            'action': 'rate_skill', 'skill_id': self.skill.pk, 'level': 2,
        })
        self.assertEqual(response.status_code, 302)

        rating = TechnicianSkill.objects.get(technician=self.technician, skill=self.skill)
        self.assertEqual(rating.level, 2)
        self.assertEqual(rating.source, TechnicianSkill.Source.SELF)
        self.assertEqual(rating.set_by, self.technician)
        self.assertTrue(
            TechnicianSkillAssessment.objects.filter(
                technician=self.technician, skill=self.skill, source=TechnicianSkill.Source.SELF,
            ).exists(),
        )

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


class DashboardTests(TaskTestCase):
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
        response = self.client.get('/tasks/dashboard/')
        self.assertEqual(response.status_code, 403)

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


class RolePermissionsTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        self.manager = Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertEqual(response.status_code, 403)

    def test_manager_can_view_the_matrix(self):
        self.client.login(username='manager1', password='pass12345')
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

    def test_manager_can_update_the_matrix(self):
        self.client.login(username='manager1', password='pass12345')

        post_data = {}
        for permission in RolePermission.Permission:
            for role in [Technician.Role.SUPERVISOR, Technician.Role.MANAGER]:
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
        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/tasks/roles/')
        self.assertFalse(response.context['notification_settings'].auto_notify_on_reschedule)

    def test_manager_can_turn_on_auto_notify(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/tasks/roles/', {
            'action': 'save_notifications', 'auto_notify_on_reschedule': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(NotificationSettings.load().auto_notify_on_reschedule)

    def test_manager_can_turn_off_auto_notify(self):
        settings = NotificationSettings.load()
        settings.auto_notify_on_reschedule = True
        settings.save()

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/tasks/roles/', {'action': 'save_notifications'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(NotificationSettings.load().auto_notify_on_reschedule)

    def test_saving_notifications_does_not_touch_the_permission_matrix(self):
        RolePermission.objects.filter(
            role=Technician.Role.SUPERVISOR, permission=RolePermission.Permission.VIEW_TASKS,
        ).update(allowed=True)

        self.client.login(username='manager1', password='pass12345')
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


class HomeRedirectTests(TaskTestCase):
    def test_supervisor_lands_on_task_list(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/')

    def test_technician_lands_on_my_week(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/')
        self.assertRedirects(response, '/tasks/my-week/')


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

    def test_complete_advances_status(self):
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save()

        self.client.login(username='tech1', password='pass12345')
        self.client.post(self.url, {'action': 'complete'})

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)
        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.COMPLETED)

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
        self.assertIsNone(report.approved_at)
        self.assertEqual(report.rejection_reason, '')

        event = self.task.events.get()
        self.assertEqual(event.event_type, TaskEvent.EventType.REPORT_SUBMITTED)
        self.assertEqual(event.actor, self.tech_user)

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

    def test_pending_report_is_locked(self):
        WorkReport.objects.create(
            task=self.task, findings='x', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_edit'])

        response = self.client.post(self.url, self._base_payload(findings='changed'))
        self.assertEqual(response.status_code, 200)
        report = WorkReport.objects.get(task=self.task)
        self.assertEqual(report.findings, 'x')

    def test_approved_report_is_locked(self):
        WorkReport.objects.create(
            task=self.task, findings='x', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(), approved_at=timezone.now(),
        )
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_edit'])

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

    def test_resubmission_after_rejection_clears_reason_and_replaces_parts(self):
        report = WorkReport.objects.create(
            task=self.task, findings='old findings', resolved=False, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(), rejection_reason='Missing serial photo.',
        )
        PartUsed.objects.create(
            report=report, part_code='OLD-1', quantity=1, unit_cost='10.00', currency_code='AED',
        )

        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertTrue(response.context['can_edit'])

        payload = self._base_payload(findings='fixed findings')
        payload['parts-0-part_code'] = 'NEW-1'
        payload['parts-0-quantity'] = '2'
        payload['parts-0-unit_cost'] = '20.00'
        payload['parts-0-currency_code'] = 'AED'
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)

        report.refresh_from_db()
        self.assertEqual(report.findings, 'fixed findings')
        self.assertEqual(report.rejection_reason, '')
        parts = list(report.parts_used.all())
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].part_code, 'NEW-1')
