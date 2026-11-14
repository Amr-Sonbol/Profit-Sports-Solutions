"""Processes end to end (docs/hardening_plan.md §3, docs/scenarios.md): what happens to
everything else when a step is cancelled, corrected or repeated."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from customers.models import Asset
from reference.models import TaskType
from tasks.equipment import lines_to_carry, register_installed_machines
from tasks.models import CustomerTicket, Task, TaskProduct, TicketMachine, TicketNotification
from tasks.tests import TaskTestCase
from tasks.views import cancel_task

User = get_user_model()


def make_task(test, number, category=None, **extra):
    task_type = extra.pop('task_type', None)
    if category:
        task_type = TaskType.objects.create(code=f'type_{number}', name=f'Type {number}', category=category)
    return Task.objects.create(
        task_number=number, site=test.site, task_type=task_type, brand=test.brand, priority=Task.Priority.NORMAL,
        source=Task.Source.INTERNAL, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
        created_by=test.supervisor_user, **extra,
    )


class CancelledInstallationTests(TaskTestCase):

    def test_its_loaded_machines_can_be_sent_on_again(self):
        loading = make_task(self, 'AE-1001', TaskType.Category.LOADING, status=Task.Status.CLOSED)
        line = TaskProduct.objects.create(task=loading, product_code='RUN-700', serial_number='S1',
                                          status=TaskProduct.Status.DONE)
        installation = make_task(self, 'AE-1002', TaskType.Category.INSTALLATION)
        TaskProduct.objects.create(task=installation, product_code='RUN-700', serial_number='S1', source_line=line)
        self.assertEqual(lines_to_carry(loading), [])
        installation.status = Task.Status.CANCELLED
        installation.save(update_fields=['status'])
        self.assertEqual(lines_to_carry(loading), [line])


class CorrectedInstallationTests(TaskTestCase):

    def test_a_corrected_serial_corrects_the_customers_machine(self):
        installation = make_task(self, 'AE-1101', TaskType.Category.INSTALLATION)
        line = TaskProduct.objects.create(task=installation, product_code='RUN-700', serial_number='WRONG-1',
                                          status=TaskProduct.Status.DONE)
        register_installed_machines(installation)
        machine = Asset.objects.get(installed_from=line)
        line.serial_number = 'RIGHT-1'
        line.save(update_fields=['serial_number'])
        register_installed_machines(installation)  # the corrected report, saved again
        machine.refresh_from_db()
        self.assertEqual(machine.serial_no, 'RIGHT-1')
        self.assertFalse(Asset.objects.filter(serial_no='WRONG-1').exists())
        self.assertEqual(Asset.objects.filter(installed_from=line).count(), 1)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False)
class CancelledVisitTests(TaskTestCase):

    def test_its_request_goes_back_to_the_desk_and_the_customer_is_told(self):
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T1201', customer=self.customer, site=self.site,
            company_name='Fitness First', site_description='Marina Branch', contact_name='Ali',
            contact_email='ali@fitnessfirst.example', contact_phone='050', description='-',
            submitted_at=timezone.now(), status=CustomerTicket.Status.CONVERTED,
        )
        visit = make_task(self, 'AE-1201')
        machine = TicketMachine.objects.create(ticket=ticket, model_name='Treadmill', serial_no='T-1', position=0,
                                               problem=TicketMachine.Problem.NOISE, task=visit,
                                               decision=TicketMachine.Decision.VISIT)
        ticket.task = visit
        ticket.save(update_fields=['task'])
        mail.outbox.clear()
        cancel_task(visit, self.supervisor_user, 'Customer closed the gym that day')
        ticket.refresh_from_db()
        machine.refresh_from_db()
        self.assertEqual((ticket.status, ticket.task, machine.task, machine.decision),
                         (CustomerTicket.Status.NEW, None, None, ''))
        self.assertTrue(TicketNotification.objects.filter(ticket=ticket, kind=TicketNotification.Kind.NEW_TICKET).exists())
        self.assertEqual([message.to for message in mail.outbox], [['ali@fitnessfirst.example']])
        self.assertIn('cancelled', mail.outbox[0].subject)


class ConsultationTests(TaskTestCase):

    def test_a_consultation_is_not_an_installation(self):
        from tasks.equipment import is_installation
        consultation = TaskType.objects.get(code='site_consultation_assessment')
        self.assertEqual(consultation.category, TaskType.Category.CONSULTATION)
        self.assertFalse(is_installation(make_task(self, 'AE-1301', task_type=consultation)))
        self.assertEqual(TaskType.objects.get(code='relocation_reinstallation').category, TaskType.Category.INSTALLATION)


class SomeoneLeavingTests(TaskTestCase):

    def test_what_they_own_goes_to_their_successor(self):
        from people.models import Technician
        from tasks.views import move_open_tasks
        leaving = self.supervisor_user.technician
        successor = Technician.objects.create(
            user=User.objects.create_user('sup2', password='pass12345'), country=self.country, full_name='Sami',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        owned = make_task(self, 'AE-1401')
        self.assertEqual(owned.responsible_supervisor, leaving)
        held = CustomerTicket.objects.create(
            ticket_number='AE-T1401', kind=CustomerTicket.Kind.INTERNAL, subject='Order belts', country=self.country,
            company_name='Profit', contact_name='-', contact_phone='-', description='-', submitted_at=timezone.now(),
            assigned_to=leaving,
        )
        move_open_tasks(leaving, successor, self.supervisor_user)
        owned.refresh_from_db()
        held.refresh_from_db()
        self.assertEqual((owned.responsible_supervisor, held.assigned_to), (successor, successor))


class SplitRequestTests(TaskTestCase):

    def test_a_request_is_not_done_while_one_of_its_visits_is_to_come(self):
        from customers.progress import DONE, ticket_progress
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T1501', customer=self.customer, site=self.site,
            company_name='Fitness First', site_description='Marina', contact_name='Ali', contact_phone='050',
            description='-', submitted_at=timezone.now(), status=CustomerTicket.Status.CONVERTED,
        )
        done = make_task(self, 'AE-1501', status=Task.Status.CLOSED)
        to_come = make_task(self, 'AE-1502', status=Task.Status.ASSIGNED)
        for position, task in enumerate((done, to_come)):
            TicketMachine.objects.create(ticket=ticket, model_name=f'M{position}', position=position, task=task,
                                         problem=TicketMachine.Problem.NOISE, decision=TicketMachine.Decision.VISIT)
        ticket.task = done
        ticket.save(update_fields=['task'])
        self.assertIsNot(ticket_progress(ticket), DONE)


class FollowUpsTests(TaskTestCase):

    def test_a_damaged_machine_waits_for_its_replacement_and_a_second_follow_up_takes_the_rest(self):
        from reports.models import WorkReport
        from tasks.equipment import create_follow_up, still_to_install
        installation = make_task(self, 'AE-1601', TaskType.Category.INSTALLATION, status=Task.Status.COMPLETED)
        WorkReport.objects.create(task=installation, resolved=False, labour_hours=1, customer_name='Ali',
                                  submitted_at=timezone.now())
        damaged = TaskProduct.objects.create(task=installation, product_code='RUN', status=TaskProduct.Status.DAMAGED)
        later = TaskProduct.objects.create(task=installation, product_code='BIKE', status=TaskProduct.Status.LATER)
        self.assertEqual(set(still_to_install(installation)), {damaged, later})
        first = create_follow_up(installation, self.supervisor_user)
        self.assertEqual(still_to_install(installation), [])
        first.status = Task.Status.CANCELLED  # called off: its machines are still to install
        first.save(update_fields=['status'])
        self.assertEqual(set(still_to_install(installation)), {damaged, later})
