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
        task_number=number, site=extra.pop('site', test.site), task_type=task_type, brand=test.brand, priority=Task.Priority.NORMAL,
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


class WhatItsAboutTests(TaskTestCase):

    def test_a_flooring_request_becomes_a_flooring_task_and_the_list_filters_it(self):
        from tasks.views import create_task  # noqa: F401 — the path every New task takes
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T1701', customer=self.customer, site=self.site,
            company_name='Fitness First', site_description='Marina', contact_name='Ali', contact_phone='050',
            description='Tiles lifting near the racks', submitted_at=timezone.now(),
            about=CustomerTicket.About.FLOORING, need=CustomerTicket.Need.REPAIR,
        )
        self.client.login(username='supervisor1', password='pass12345')
        page = self.client.get(f'/tasks/new/?ticket={ticket.pk}')
        form = page.context['form']
        data = {name: ('' if form[name].value() is None else form[name].value()) for name in form.fields}
        data.update({'priority': Task.Priority.NORMAL, 'done_by': Task.DoneBy.SUBCONTRACTOR, 'subcontractor_name': ''})
        data = {k: v for k, v in data.items() if v not in (None, False)}
        refused = self.client.post(f'/tasks/new/?ticket={ticket.pk}', data)
        self.assertIn('subcontractor_name', refused.context['form'].errors)  # a subcontractor is named
        data['subcontractor_name'] = 'Gulf Floors LLC'
        self.client.post(f'/tasks/new/?ticket={ticket.pk}', data)
        task = Task.objects.get(task_number__isnull=False, site=self.site, description__icontains='Tiles')
        self.assertEqual((task.about, task.done_by, task.subcontractor_name),
                         (Task.About.FLOORING, Task.DoneBy.SUBCONTRACTOR, 'Gulf Floors LLC'))
        listed = self.client.get('/tasks/', {'about': 'flooring', 'status': 'all'}).context['page_obj']
        self.assertEqual([t.pk for t in listed], [task.pk])


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False)
class NewBranchWhileSendingOnTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        from customers.models import Customer, Site
        from people.models import Technician
        self.manager = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(user=self.manager, country=self.country, full_name='Mona Manager', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        ours = Customer.objects.create(country=self.country, name='Profit Sports Solutions', segment='other', is_internal=True)
        warehouse = Site.objects.create(customer=ours, name='Jebel Ali warehouse', address='Jebel Ali')
        loading_type = TaskType.objects.create(code='type_load', name='Loading', category=TaskType.Category.LOADING)
        self.loading = Task.objects.create(
            task_number='AE-1801', site=warehouse, task_type=loading_type, priority=Task.Priority.NORMAL,
            source=Task.Source.INTERNAL, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.manager, status=Task.Status.CLOSED,
        )
        self.lines = [TaskProduct.objects.create(task=self.loading, product_code=f'RUN-{n}', serial_number=f'S{n}',
                                                 status=TaskProduct.Status.DONE) for n in range(2)]
        self.customer.contact_email = 'owner@fitnessfirst.example'
        self.customer.save(update_fields=['contact_email'])
        self.client.force_login(self.manager)

    def send(self, line, **branch):
        return self.client.post(f'/tasks/{self.loading.pk}/', {
            'action': 'create_next_step', 'step': 'installation', 'lines': [line.pk], **branch,
        })

    def test_a_new_branch_made_right_there_and_one_installation_per_branch(self):
        mail.outbox.clear()
        self.send(self.lines[0], new_branch_customer=self.customer.pk, new_branch_name='JBR Branch',
                  new_branch_address='JBR Walk, Dubai', new_branch_contact='Omar', new_branch_phone='0509999999')
        branch = self.customer.sites.get(name='JBR Branch')
        installation = Task.objects.get(site=branch)
        self.assertEqual(installation.products.count(), 1)
        self.assertEqual([m.to for m in mail.outbox], [['owner@fitnessfirst.example']])
        # More machines for the same branch: added to its open installation, not a second task.
        self.send(self.lines[1], site=branch.pk)
        self.assertEqual(Task.objects.filter(site=branch).count(), 1)
        self.assertEqual(installation.products.count(), 2)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False)
class PhoneRequestsTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        from people.models import Technician
        self.desk = User.objects.create_user('desk1', password='pass12345')
        Technician.objects.create(user=self.desk, country=self.country, full_name='Desk', language='en',
                                  role=Technician.Role.COUNTRY_SUPPORT, employment_type='staff')
        self.support = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(user=self.support, country=self.country, full_name='Support', language='en',
                                  role=Technician.Role.SUPPORT_MANAGER, employment_type='staff')

    def open_ticket(self, **data):
        self.client.force_login(self.desk)
        base = {'source': CustomerTicket.Source.PHONE, 'description': 'Called in', 'contact_name': 'Omar',
                'contact_phone': '0509999999'}
        self.client.post('/tasks/tickets/open/', {**base, **data})
        return CustomerTicket.objects.latest('pk')

    def test_a_new_branch_by_phone(self):
        ticket = self.open_ticket(about='new_branch', new_branch_customer=self.customer.pk,
                                  site_description='JBR Branch', site_address='JBR Walk')
        self.assertEqual((ticket.customer, ticket.site, ticket.about), (self.customer, None, 'new_branch'))
        self.client.force_login(self.support)
        self.client.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'create_branch'})
        ticket.refresh_from_db()
        self.assertEqual(ticket.site.name, 'JBR Branch')

    def test_a_caller_who_is_not_a_customer_yet(self):
        ticket = self.open_ticket(about='machines', company_name='Iron Gym', site_description='Al Quoz',
                                  site_address='Al Quoz 3, Dubai')
        self.assertIsNone(ticket.customer)
        # The desk can't create customers; a manager can, in one click.
        self.client.force_login(self.desk)
        self.client.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'create_customer'})
        ticket.refresh_from_db()
        self.assertIsNone(ticket.customer)
        self.client.force_login(self.support)
        self.client.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'create_customer'})
        ticket.refresh_from_db()
        self.assertEqual((ticket.customer.name, ticket.site.name), ('Iron Gym', 'Al Quoz'))


class ClaimFollowThroughTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        from people.models import Technician
        self.manager = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(user=self.manager, country=self.country, full_name='Mona Manager', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        receiving = make_task(self, 'AE-1901', TaskType.Category.RECEIVING, status=Task.Status.COMPLETED)
        TaskProduct.objects.create(task=receiving, product_code='RUN-700', quantity=1,
                                   status=TaskProduct.Status.DAMAGED, note='Crushed corner')
        from tasks.equipment import open_equipment_follow_up
        self.claim = open_equipment_follow_up(receiving, self.supervisor_user)
        self.receiving = receiving

    def test_a_damaged_delivery_opens_a_linked_claim_due_in_two_weeks(self):
        from datetime import timedelta
        self.assertEqual(self.claim.linked_task, self.receiving)
        self.assertTrue(self.claim.is_claim)
        self.assertEqual(self.claim.due_on, timezone.localdate() + timedelta(days=14))
        self.assertEqual(self.claim.waiting_for, CustomerTicket.WaitingFor.FACTORY)

    def test_a_claim_closes_only_with_its_result(self):
        self.client.force_login(self.manager)
        url = f'/tasks/tickets/internal/{self.claim.pk}/'
        self.client.post(url, {'action': 'close', 'close_reason': 'Sorted'})
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, CustomerTicket.Status.NEW)
        self.client.post(url, {'action': 'close', 'close_reason': 'Panatta credited us', 'claim_outcome': 'credit'})
        self.claim.refresh_from_db()
        self.assertEqual((self.claim.status, self.claim.claim_outcome), (CustomerTicket.Status.CLOSED, 'credit'))

    def test_an_overdue_follow_up_reaches_the_bell(self):
        from datetime import timedelta
        self.claim.due_on = timezone.localdate() - timedelta(days=1)
        self.claim.save(update_fields=['due_on'])
        self.client.force_login(self.manager)
        self.assertContains(self.client.get('/tasks/'), 'follow-up is overdue')


class StockStaysRightTests(TaskTestCase):

    def test_left_behind_or_returned_machines_stay_in_stock(self):
        from customers.models import Customer, Site
        from tasks.stock import machine_stock
        ours = Customer.objects.create(country=self.country, name='Profit', segment='other', is_internal=True)
        warehouse = Site.objects.create(customer=ours, name='Jebel Ali', address='Jebel Ali')
        receiving = make_task(self, 'AE-2001', TaskType.Category.RECEIVING, status=Task.Status.CLOSED, site=warehouse)
        TaskProduct.objects.create(task=receiving, product_code='RUN', quantity=3, status=TaskProduct.Status.DONE)
        loading = make_task(self, 'AE-2002', TaskType.Category.LOADING, site=warehouse)
        loaded = TaskProduct.objects.create(task=loading, product_code='RUN', status=TaskProduct.Status.DONE)
        TaskProduct.objects.create(task=loading, product_code='RUN', status=TaskProduct.Status.LATER)  # left behind
        self.assertEqual(machine_stock(warehouse), {'RUN': 2})
        installation = make_task(self, 'AE-2003', TaskType.Category.INSTALLATION)
        TaskProduct.objects.create(task=installation, product_code='RUN', source_line=loaded,
                                   status=TaskProduct.Status.RETURNED)  # the customer refused it
        self.assertEqual(machine_stock(warehouse), {'RUN': 3})


class MoveOrRetireTests(TaskTestCase):

    def test_a_manager_moves_or_retires_a_machine_with_a_reason(self):
        from customers.models import Asset, Site
        from people.models import Technician
        manager = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(user=manager, country=self.country, full_name='Mona', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        other = Site.objects.create(customer=self.customer, name='JBR', address='JBR')
        self.client.force_login(manager)
        url = f'/tasks/machines/{self.asset.pk}/'
        self.client.post(url, {'action': 'move', 'site': other.pk, 'reason': ''})
        self.asset.refresh_from_db()
        self.assertNotEqual(self.asset.site, other)  # no reason, no move
        self.client.post(url, {'action': 'move', 'site': other.pk, 'reason': 'Relocated by the customer'})
        self.client.post(url, {'action': 'retire', 'reason': 'Scrapped after the flood'})
        self.asset.refresh_from_db()
        self.assertEqual((self.asset.site, self.asset.status), (other, Asset.Status.RETIRED))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False)
class VisitsThatDidntSettleItTests(TaskTestCase):

    def test_a_low_rating_opens_a_follow_up_to_the_owner(self):
        from reports.models import CustomerFeedback
        task = make_task(self, 'AE-2101', status=Task.Status.CLOSED)
        feedback = CustomerFeedback.objects.create(task=task, requested_at=timezone.now(), token='tok-2101')
        self.client.post('/reports/feedback/tok-2101/', {'rating': 2, 'comment': 'Late and messy'})
        feedback.refresh_from_db()
        follow_up = CustomerTicket.objects.get(linked_task=task)
        self.assertEqual(follow_up.assigned_to, task.responsible_supervisor)
        self.assertIn('2/5', follow_up.subject)

    def test_late_parts_reach_the_owners_bell(self):
        from datetime import timedelta

        from tasks.models import TaskPartNeeded
        task = make_task(self, 'AE-2201', status=Task.Status.ASSIGNED)
        TaskPartNeeded.objects.create(task=task, product_code='BELT', source=TaskPartNeeded.Source.ORDERED,
                                      expected_on=timezone.localdate() - timedelta(days=2), created_by=self.supervisor_user)
        self.client.login(username='supervisor1', password='pass12345')
        self.assertContains(self.client.get('/tasks/'), 'Ordered parts are late')


class PpmAndQuotationTests(TaskTestCase):

    def test_closing_a_ppm_plans_the_next_one_once(self):
        from tasks.views import _months_later, close_task_directly
        self.site.ppm_every_months = 3
        self.site.save(update_fields=['ppm_every_months'])
        ppm = make_task(self, 'TEST-PPM-1', task_type=TaskType.objects.get(code='preventive_maintenance'),
                        status=Task.Status.COMPLETED)
        close_task_directly(ppm, self.supervisor_user, 'Done')
        upcoming = Task.objects.get(previous_task=ppm)
        self.assertEqual(upcoming.scheduled_date, _months_later(timezone.localdate(), 3))
        other = make_task(self, 'TEST-PPM-2', task_type=ppm.task_type, status=Task.Status.COMPLETED)
        close_task_directly(other, self.supervisor_user, 'Done')
        self.assertFalse(Task.objects.filter(previous_task=other).exists())  # one is already planned

    def test_the_customer_answers_a_quotation_on_the_portal(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from customers.models import PortalUser
        task = make_task(self, 'AE-2401', status=Task.Status.NEW)
        task.quotation = SimpleUploadedFile('quote.pdf', b'%PDF-1.4 quote', content_type='application/pdf')
        task.quote_status, task.quote_status_at = Task.QuoteStatus.SENT, timezone.now()
        task.save()
        customer = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=customer)
        self.client.force_login(customer)
        page = self.client.get(f'/customers/portal/visits/{task.pk}/')
        self.assertContains(page, 'A quotation is waiting for your answer')
        self.client.post(f'/customers/portal/visits/{task.pk}/', {'action': 'quote_approve'})
        task.refresh_from_db()
        self.assertEqual(task.quote_status, Task.QuoteStatus.APPROVED)

    def test_an_unanswered_quotation_reminds_its_owner_after_a_week(self):
        from datetime import timedelta
        task = make_task(self, 'AE-2501', status=Task.Status.NEW)
        Task.objects.filter(pk=task.pk).update(quote_status=Task.QuoteStatus.SENT,
                                               quote_status_at=timezone.now() - timedelta(days=8))
        self.client.login(username='supervisor1', password='pass12345')
        self.assertContains(self.client.get('/tasks/'), 'unanswered for a week')


class ShowroomTransfersIncidentsTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        from customers.models import Customer, Site
        from people.models import Technician
        self.manager = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(user=self.manager, country=self.country, full_name='Mona', language='en',
                                  role=Technician.Role.MANAGER, employment_type='staff')
        ours = Customer.objects.create(country=self.country, name='Profit', segment='other', is_internal=True)
        self.warehouse = Site.objects.create(customer=ours, name='Jebel Ali', address='JA', kind=Site.Kind.WAREHOUSE)
        self.second = Site.objects.create(customer=ours, name='Al Quoz', address='AQ', kind=Site.Kind.WAREHOUSE)
        self.showroom = Site.objects.create(customer=ours, name='Showroom', address='SZR', kind=Site.Kind.SHOWROOM)

    def test_a_showroom_isnt_stock_and_a_loading_can_go_to_another_warehouse(self):
        from tasks.equipment import NEXT_STEPS, next_step_sites
        manager = self.manager.technician
        self.assertIn(TaskType.Category.RECEIVING, NEXT_STEPS[TaskType.Category.LOADING])
        warehouses = set(next_step_sites(manager, TaskType.Category.RECEIVING))
        self.assertEqual(warehouses, {self.warehouse, self.second})  # not the showroom

    def test_a_safety_incident_takes_the_machines_out_of_use_and_tells_the_manager(self):
        from customers.models import Asset
        from tasks.models import TaskAsset
        task = make_task(self, 'TEST-INC-1', status=Task.Status.IN_PROGRESS)
        TaskAsset.objects.create(task=task, asset=self.asset, outcome=TaskAsset.Outcome.choices[0][0])
        self.client.force_login(self.manager)
        self.client.post(f'/tasks/{task.pk}/', {'action': 'report_incident', 'incident_note': 'Belt snapped, a member fell'})
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.FAULTY)
        incident = CustomerTicket.objects.get(linked_task=task)
        self.assertEqual(incident.assigned_to, self.manager.technician)
        from zoneinfo import ZoneInfo
        self.assertEqual(incident.due_on, timezone.localdate(timezone=ZoneInfo(self.country.timezone)))  # its country's today

    def test_a_factory_recall_makes_a_visit_per_branch(self):
        from customers.models import Asset, Site
        branch = Site.objects.create(customer=self.customer, name='JBR', address='JBR')
        for site in (self.site, branch):
            Asset.objects.create(site=site, brand=self.brand, model_name='Run 900', serial_no=f'R9-{site.pk}')
        self.client.force_login(self.manager)
        self.client.post('/tasks/machines/?q=Run 900', {'action': 'recall', 'notice': 'Belt motor recall 2026-11'})
        recalls = Task.objects.filter(description__icontains='Belt motor recall')
        self.assertEqual(set(recalls.values_list('site', flat=True)), {self.site.pk, branch.pk})

    def test_a_receiving_without_its_delivery_note_is_followed_up(self):
        receiving = make_task(self, 'TEST-REC-1', TaskType.Category.RECEIVING, site=self.warehouse)
        from tasks.equipment import open_quality_follow_up as follow
        follow(receiving, self.manager, 'Delivery note missing: %(number)s')
        self.assertTrue(CustomerTicket.objects.filter(linked_task=receiving, subject__icontains='Delivery note').exists())
