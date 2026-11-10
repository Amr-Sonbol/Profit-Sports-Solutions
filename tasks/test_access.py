"""Who sees, who changes — one test per rule in docs/access_rules.md."""
from django.contrib.auth import get_user_model
from django.utils import timezone

from customers.models import Customer, Site
from people.models import Technician
from reference.models import Country, TaskType
from tasks.models import Task, TaskEvent
from tasks.tests import TaskTestCase

User = get_user_model()


class AccessRulesTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.egypt = Country.objects.create(name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP')
        cairo = Site.objects.create(
            customer=Customer.objects.create(country=self.egypt, name='Cairo Gym', segment='gym'), name='Zamalek',
        )
        self.egypt_task = Task.objects.create(
            task_number='EG-0001', site=cairo, task_type=self.task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user,
        )
        self.task = Task.objects.create(  # a UAE visit, owned by Sara Super
            task_number='UAE-0001', site=self.site, task_type=self.task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, responsible_supervisor=self.supervisor_user.technician,
        )

    def person(self, username, role, country=None):
        user = User.objects.create_user(username, password='pass12345')
        Technician.objects.create(
            user=user, country=country or self.country, full_name=username.title(), language='en', role=role,
            employment_type='staff',
        )
        client = self.client_class()
        client.login(username=username, password='pass12345')
        return client

    def test_a_manager_stays_in_their_own_country(self):
        manager = self.person('uae_manager', Technician.Role.MANAGER)
        self.assertEqual(manager.get(f'/tasks/{self.task.pk}/').status_code, 200)
        self.assertEqual(manager.get(f'/tasks/{self.egypt_task.pk}/').status_code, 404)
        self.assertEqual(manager.get('/tasks/week/all/').status_code, 403)  # the all-countries board
        self.assertEqual(manager.get('/tasks/brands/').status_code, 403)  # a shared list

    def test_a_project_manager_reaches_every_country_but_has_no_tickets(self):
        project = self.person('project1', Technician.Role.PROJECT_MANAGER)
        self.assertEqual(project.get(f'/tasks/{self.egypt_task.pk}/').status_code, 200)
        self.assertEqual(project.get('/tasks/week/all/').status_code, 200)
        self.assertEqual(project.get('/tasks/tickets/').status_code, 403)

    def test_only_the_owner_changes_a_task_others_see_it(self):
        other_supervisor = self.person('supervisor2', Technician.Role.SUPERVISOR)
        support_manager = self.person('tsm1', Technician.Role.SUPPORT_MANAGER)
        for client in (other_supervisor, support_manager):
            self.assertEqual(client.get(f'/tasks/{self.task.pk}/').status_code, 200)  # sees it
            self.assertEqual(client.get(f'/tasks/{self.task.pk}/edit/').status_code, 403)  # can't change it
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/edit/').status_code, 200)  # the owner can

    def test_stepping_in_is_recorded(self):
        for username, role in (('ops1', Technician.Role.OPERATIONS_MANAGER), ('uae_manager', Technician.Role.MANAGER)):
            client = self.person(username, role)
            client.post(f'/tasks/{self.task.pk}/', {'action': 'notify_delay', 'delay_reason': ''})
        stepped = self.task.events.filter(event_type=TaskEvent.EventType.STEPPED_IN)
        self.assertEqual(stepped.count(), 2)
        self.assertEqual(stepped.first().note, 'Instead of Sara Super')

    def test_a_manager_cannot_step_in_abroad(self):
        manager = self.person('eg_manager', Technician.Role.MANAGER, country=self.egypt)
        self.assertEqual(manager.get(f'/tasks/{self.task.pk}/').status_code, 404)

    def test_a_new_task_belongs_to_whoever_created_it(self):
        from tasks.views import create_task  # noqa: F401 — the rule lives there
        manager = self.person('uae_manager', Technician.Role.MANAGER)
        response = manager.post('/tasks/new/', {
            'site': self.site.pk, 'task_type': self.task_type.pk, 'priority': 'normal', 'source': 'phone',
            'billing_type': 'chargeable', 'reported_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'description': 'Owner check',
        })
        task = Task.objects.filter(description='Owner check').first()
        self.assertIsNotNone(task, response.content[:300])
        self.assertEqual(task.responsible_supervisor.user.username, 'uae_manager')

    def test_the_warehouse_manager_sees_stock_work_only(self):
        warehouse = self.person('wm1', Technician.Role.WAREHOUSE_MANAGER)
        shipment = Task.objects.create(
            task_number='UAE-0900', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            task_type=TaskType.objects.create(code='ps', name='Part shipment', category='part_shipment'),
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.supervisor_user,
        )
        self.assertEqual(warehouse.get(f'/tasks/{shipment.pk}/').status_code, 200)
        self.assertEqual(warehouse.get(f'/tasks/{self.task.pk}/').status_code, 404)  # a maintenance visit

    def test_country_support_sees_tasks_without_changing_them(self):
        support = self.person('cs1', Technician.Role.COUNTRY_SUPPORT)
        self.assertEqual(support.get(f'/tasks/{self.task.pk}/').status_code, 200)
        self.assertEqual(support.get(f'/tasks/{self.task.pk}/edit/').status_code, 403)
