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

    def test_progress_and_skills_are_the_field_teams(self):
        from django.urls import reverse
        office = self.person('wm2', Technician.Role.WAREHOUSE_MANAGER)
        self.assertNotContains(office.get('/tasks/'), reverse('tasks:my_progress'))
        self.assertFalse(Technician.objects.get(user__username='wm2').is_field_staff)
        self.client.login(username='tech1', password='pass12345')
        self.assertContains(self.client.get(reverse('tasks:my_week')), reverse('tasks:my_progress'))

    def test_the_final_approval_is_the_technical_support_managers(self):
        from unittest.mock import patch

        from tasks.views import approve_report_as_supervisor
        tsm = self.person('tsm1', Technician.Role.SUPPORT_MANAGER)
        manager = self.person('uae_manager', Technician.Role.MANAGER)
        ops = self.person('ops1', Technician.Role.OPERATIONS_MANAGER)
        self.task.status = Task.Status.PENDING_SUPERVISOR_REVIEW
        self.task.save()
        # The supervisor's review tells every Technical Support Manager.
        with patch('tasks.views.push_in_their_language') as push, self.captureOnCommitCallbacks(execute=True):
            approve_report_as_supervisor(self.task, self.supervisor_user)
        self.assertEqual([call.args[0].user.username for call in push.call_args_list], ['tsm1'])
        self.assertEqual(manager.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report'}).status_code, 403)
        # The Operations Manager may step in — recorded.
        self.assertEqual(ops.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report'}).status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.CLOSED)
        self.assertEqual(self.task.events.filter(event_type=TaskEvent.EventType.STEPPED_IN).get().note,
                         'Instead of the Technical Support Manager')
        self.assertIsNotNone(tsm)

    def test_a_new_request_reaches_supports_phones(self):
        from unittest.mock import patch

        from django.test import RequestFactory

        from tasks.models import CustomerTicket
        from tasks.views import send_new_ticket_email_to_staff
        self.person('tsm1', Technician.Role.SUPPORT_MANAGER)
        self.person('desk_uae', Technician.Role.COUNTRY_SUPPORT)
        self.person('desk_eg', Technician.Role.COUNTRY_SUPPORT, country=self.egypt)
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0900', company_name='Fitness First', site_description='Marina',
            contact_name='Ali', contact_phone='050', description='Stops', submitted_at=timezone.now(),
        )
        with patch('tasks.views.push_in_their_language') as push, self.captureOnCommitCallbacks(execute=True):
            send_new_ticket_email_to_staff(RequestFactory().get('/'), ticket)
        self.assertEqual(sorted(call.args[0].user.username for call in push.call_args_list), ['desk_uae', 'tsm1'])
        self.assertEqual(push.call_args_list[0].args[3], {'type': 'ticket', 'ticket_id': ticket.pk})

    def test_the_warehouse_manager_and_country_support_open_no_one(self):
        from tasks.views import overseen_roles
        for username, role in (('wm9', Technician.Role.WAREHOUSE_MANAGER), ('cs9', Technician.Role.COUNTRY_SUPPORT)):
            client = self.person(username, role)
            self.assertEqual(overseen_roles(Technician.objects.get(user__username=username)), [])
            self.assertIn(client.get(f'/tasks/technicians/{self.technician.pk}/').status_code, (403, 404))

    def test_a_report_waiting_over_two_working_days_reaches_the_operations_manager(self):
        from datetime import timedelta

        from tasks.alarms import late_reports
        ops = self.person('ops2', Technician.Role.OPERATIONS_MANAGER)
        self.task.status = Task.Status.COMPLETED
        self.task.save()
        event = TaskEvent.objects.create(task=self.task, event_type=TaskEvent.EventType.SUPERVISOR_APPROVED,
                                         occurred_at=timezone.now() - timedelta(hours=20), actor=self.supervisor_user)
        operations = Technician.objects.get(user__username='ops2')
        self.assertEqual(late_reports(operations), [])  # not yet
        event.occurred_at = timezone.now() - timedelta(days=6)
        event.save()
        self.assertEqual([task.pk for task in late_reports(operations)], [self.task.pk])
        self.assertEqual(late_reports(Technician.objects.get(user__username='supervisor1')), [])  # his alone
        page = ops.get(f'/tasks/{self.task.pk}/')  # the bell, on any page
        self.assertContains(page, 'Report waiting more than 2 working days for approval')

    def test_a_negligence_flag_never_reaches_the_technician_in_the_app(self):
        from rest_framework.authtoken.models import Token

        from tasks.models import TaskAssignment
        TaskAssignment.objects.create(task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now())
        TaskEvent.objects.create(task=self.task, event_type=TaskEvent.EventType.NEGLIGENCE, occurred_at=timezone.now(),
                                 actor=self.supervisor_user, note='Left the site early')

        def history(username, path):
            token, _ = Token.objects.get_or_create(user=User.objects.get(username=username))
            body = self.client.get(path, HTTP_AUTHORIZATION=f'Token {token.key}').json()
            return [event['event_type'] for event in body['events']]

        self.assertNotIn('negligence', history('tech1', f'/api/my-tasks/{self.task.pk}/'))
        self.assertNotIn('negligence', history('supervisor1', f'/api/tasks/{self.task.pk}/'))
        self.person('mgr1', Technician.Role.MANAGER)
        self.assertIn('negligence', history('mgr1', f'/api/tasks/{self.task.pk}/'))

    def test_the_app_speaks_the_countrys_language(self):
        from rest_framework.authtoken.models import Token
        self.egypt.report_language = 'ar'
        self.egypt.save()
        self.person('cairo_sup', Technician.Role.SUPERVISOR, country=self.egypt)
        Technician.objects.filter(user__username='cairo_sup').update(language='en')  # their own setting doesn't matter
        token, _ = Token.objects.get_or_create(user=User.objects.get(username='cairo_sup'))
        me = self.client.get('/api/me/', HTTP_AUTHORIZATION=f'Token {token.key}').json()
        self.assertEqual((me['app_language'], me['role_display']), ('ar', 'مشرف'))
        uae = Technician.objects.get(user__username='supervisor1')
        self.assertEqual(uae.app_language, 'en')
