"""Who reviewed and who approved a report (Task.approvals): recorded, shown on the
task page, in the app, on the customer's visit page and on the PDF; two people."""
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token

from customers.models import PortalUser
from people.models import Technician
from reports.models import WorkReport
from tasks.models import Task, TaskEvent
from tasks.tests import TaskTestCase

User = get_user_model()


class ApprovalsTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        self.tsm_user = User.objects.create_user('tsm1', password='pass12345')
        Technician.objects.create(user=self.tsm_user, country=self.country, full_name='Omar Support', language='en',
                                  role=Technician.Role.SUPPORT_MANAGER, employment_type='staff')
        now = timezone.now()
        self.task = Task.objects.create(
            task_number='AE-0201', site=self.site, task_type=self.task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=now,
            created_by=self.supervisor_user, responsible_supervisor=self.supervisor_user.technician,
            status=Task.Status.PENDING_SUPERVISOR_REVIEW,
        )
        WorkReport.objects.create(task=self.task, resolved=True, labour_hours=1, customer_name='Ali', submitted_at=now)
        TaskEvent.objects.create(task=self.task, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=now,
                                 actor=self.tech_user)

    def approve_both(self):
        self.client.force_login(self.supervisor_user)
        self.client.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report_supervisor'})
        self.client.force_login(self.tsm_user)
        self.client.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report'})
        self.task.refresh_from_db()

    def test_who_reviewed_and_who_approved_are_recorded_and_shown(self):
        self.approve_both()
        self.assertEqual(self.task.status, Task.Status.CLOSED)
        approvals = self.task.approvals()
        self.assertEqual((approvals['reviewed']['name'], approvals['approved']['name']), ('Sara Super', 'Omar Support'))
        self.assertFalse(approvals['approved']['instead'])
        page = self.client.get(f'/tasks/{self.task.pk}/').content.decode()
        self.assertIn('Reviewed by', page)
        self.assertIn('Omar Support', page)
        # The app.
        token = Token.objects.create(user=self.tsm_user).key
        data = self.client.get(f'/api/tasks/{self.task.pk}/', HTTP_AUTHORIZATION=f'Token {token}').json()
        self.assertEqual(data['approvals']['approved']['name'], 'Omar Support')
        # The customer's own visit page and their PDF's page.
        customer = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=customer)
        self.client.force_login(customer)
        visit = self.client.get(f'/customers/portal/visits/{self.task.pk}/').content.decode()
        self.assertIn('Approved by', visit)
        self.assertIn('Omar Support', visit)
        from reports.pdf import render_visit_report_html
        self.assertIn('Omar Support', render_visit_report_html(self.task.report))

    def test_the_same_person_cannot_give_both_approvals(self):
        # The Technical Support Manager reviews (stepping in is not his; make him the owner).
        self.task.responsible_supervisor = self.tsm_user.technician
        self.task.save()
        self.client.force_login(self.tsm_user)
        self.client.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report_supervisor'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)
        page = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertFalse(page.context['can_give_final_approval'])
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report'}).status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_waiting_says_whose_turn(self):
        self.assertEqual(self.task.approvals()['waiting'], 'review')
        self.client.force_login(self.supervisor_user)
        self.client.post(f'/tasks/{self.task.pk}/', {'action': 'approve_report_supervisor'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.approvals()['waiting'], 'approval')
