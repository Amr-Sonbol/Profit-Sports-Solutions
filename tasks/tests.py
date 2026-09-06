from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Brand, Country, Skill, TaskType
from reports.models import PartUsed, WorkReport

from .models import Task, TaskAssignment, TaskAsset, TaskAttachment, TaskEvent

User = get_user_model()


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
