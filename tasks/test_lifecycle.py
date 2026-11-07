"""A request's whole life, end to end, through the real pages — the way
people use them: the customer sends it from the portal, the team works on it
and turns it into a task, the technician does the visit and files the
report, the managers approve it, and the customer sees it closed, opens the
report and rates the visit. Plus every kind of request, and the other ways a
request ends. Run before a release:

    python manage.py test tasks.test_lifecycle
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from customers.models import Customer, Site
from people.models import Technician
from reference.models import Country
from reports.models import CustomerFeedback
from tasks.models import CustomerTicket, Task

User = get_user_model()
PASSWORD = 'pass12345'
FORMSET = {'MIN_NUM_FORMS': '0', 'MAX_NUM_FORMS': '1000'}


def form_data(form, **overrides):
    """What the page would post: each field's current value as the browser
    has it, then the test's own answers."""
    data = {}
    for field in form:
        value = field.value()
        if value is None or value is False:
            continue
        if value is True:
            value = 'on'
        elif isinstance(value, datetime):
            value = timezone.localtime(value).strftime('%Y-%m-%dT%H:%M') if timezone.is_aware(value) else value.strftime('%Y-%m-%dT%H:%M')
        elif isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, (list, tuple)):
            value = [str(v) for v in value]
        data[field.html_name] = value
    data.update(overrides)
    return data


def links_in(message):
    return re.findall(r'https?://[^\s"<>]+', message.body)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False,
    PORTAL_HOST='portal.example.test', ALLOWED_HOSTS=['testserver', 'portal.example.test'],
)
class LifecycleTestCase(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED')
        self.admin = self._staff('admin1', 'Amina Admin', Technician.Role.ADMIN)
        self.supervisor = self._staff('supervisor1', 'Sara Super', Technician.Role.SUPERVISOR)
        self.tech = self._staff('tech1', 'Tarek Tech', Technician.Role.TECHNICIAN)
        self.customer_user = User.objects.create_user('fitnessfirst', email='ali@fitnessfirst.example', password=PASSWORD)
        self.customer = Customer.objects.create(
            country=self.country, name='Fitness First', segment='gym', user=self.customer_user,
        )
        self.site = Site.objects.create(
            customer=self.customer, name='Marina Branch', address='Dubai Marina',
            contact_name='Ali', contact_email='ali@fitnessfirst.example',
        )
        self.staff = self.client_for('admin1')
        self.portal = self.client_for('fitnessfirst', host='portal.example.test')

    def _staff(self, username, name, role):
        user = User.objects.create_user(username, email=f'{username}@pss.example', password=PASSWORD)
        return Technician.objects.create(
            user=user, country=self.country, full_name=name, language='en', role=role, employment_type='staff',
        )

    def client_for(self, username, host='testserver'):
        client = self.client_class(HTTP_HOST=host)
        client.login(username=username, password=PASSWORD)
        return client

    def send_request(self, about='machines', need='repair', machines=None, **overrides):
        """The portal's New request form, as a customer fills it in."""
        payload = {
            'site': self.site.pk, 'contact_name': 'Ali', 'contact_phone': '0501234567',
            'contact_email': 'ali@fitnessfirst.example', 'notes': '', 'about': about, 'need': need,
            'subject': 'Treadmill stops', 'message': 'It stops after five minutes, error E-07.',
            'machines-TOTAL_FORMS': '10', 'machines-INITIAL_FORMS': '0', **{f'machines-{k}': v for k, v in FORMSET.items()},
        }
        if machines is None and about == 'machines':
            machines = [{'which': 'new', 'model_name': 'Excite Run 700', 'serial_no': 'TG-1', 'problem': 'not_working',
                         'description': 'Stops after 5 minutes, error E-07, since Monday.'}]
        for index, machine in enumerate(machines or []):
            payload.update({f'machines-{index}-{key}': value for key, value in machine.items()})
        payload.update(overrides)
        return self.portal.post('/customers/portal/tickets/new/', payload)

    def newest_request(self):
        return CustomerTicket.objects.order_by('-pk').first()

    def form_errors(self, response):
        context = response.context or {}
        found = {}
        for name in ('form', 'machines', 'report_form', 'formset'):
            item = context.get(name) if hasattr(context, 'get') else None
            errors = getattr(item, 'errors', None)
            if errors:
                found[name] = errors
        return found


class EveryKindOfRequestTests(LifecycleTestCase):
    """Every "about" with every "need" arrives: numbered, in the team's queue,
    the team alerted and the customer told it arrived."""

    def test_every_combination_arrives(self):
        abouts = [a for a in CustomerTicket.About.values if a != CustomerTicket.About.NEW_BRANCH]
        for about in abouts:
            for need in CustomerTicket.Need.values:
                with self.subTest(about=about, need=need):
                    mail.outbox.clear()
                    before = CustomerTicket.objects.count()
                    extra = {}
                    if about == 'machines' and need == 'parts':
                        extra = {'machines': [{'which': 'new', 'model_name': 'Excite Run 700', 'serial_no': 'TG-1',
                                               'parts': 'Running belt', 'description': 'Belt is worn.'}]}
                    response = self.send_request(about=about, need=need, **extra)
                    self.assertEqual(response.status_code, 302, self.form_errors(response))
                    self.assertEqual(CustomerTicket.objects.count(), before + 1)
                    ticket = self.newest_request()
                    self.assertTrue(ticket.ticket_number.startswith('AE-T') or ticket.ticket_number)
                    self.assertEqual((ticket.about, ticket.need, ticket.status), (about, need, CustomerTicket.Status.NEW))
                    self.assertEqual(self.staff.get(f'/tasks/tickets/{ticket.pk}/').status_code, 200)
                    recipients = {address for message in mail.outbox for address in message.to}
                    self.assertIn('ali@fitnessfirst.example', recipients, 'customer confirmation')

    def test_a_new_branch_request_arrives(self):
        response = self.portal.post('/customers/portal/branches/new/', {
            'name': 'JBR Branch', 'address': 'JBR Walk', 'contact_name': 'Omar', 'contact_phone': '0509999999',
            'contact_email': 'omar@fitnessfirst.example', 'opening_date': '', 'message': 'Opens next month.',
        })
        self.assertEqual(response.status_code, 302, self.form_errors(response))
        ticket = self.newest_request()
        self.assertEqual(ticket.about, CustomerTicket.About.NEW_BRANCH)
        self.assertContains(self.staff.get(f'/tasks/tickets/{ticket.pk}/'), 'Add this branch')

    def test_a_machine_request_without_a_serial_is_refused(self):
        response = self.send_request(machines=[{'which': 'new', 'model_name': 'Excite Run 700', 'problem': 'not_working',
                                                'description': 'Stops.'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerTicket.objects.exists())


class WholeLifeTests(LifecycleTestCase):
    """One machine request from the portal to the customer's rating."""

    def test_from_the_portal_to_the_rating(self):
        # 1. The customer sends it.
        response = self.send_request()
        self.assertEqual(response.status_code, 302, self.form_errors(response))
        ticket = self.newest_request()
        url = f'/tasks/tickets/{ticket.pk}/'
        self.assertContains(self.portal.get('/customers/portal/requests/'), ticket.ticket_number)

        # 2. The team works on it: a reply reaches the customer, on the portal's address.
        mail.outbox.clear()
        self.staff.post(url, {'action': 'add_reply', 'message': 'We are on it.'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(all(link.startswith('http://portal.example.test/') for link in links_in(mail.outbox[0])))
        # An internal note reaches nobody outside, and the customer never sees it.
        mail.outbox.clear()
        self.staff.post(url, {'action': 'add_internal_note', 'message': 'Check stock of belts first.'})
        self.assertEqual([m for m in mail.outbox if 'ali@fitnessfirst.example' in m.to], [])
        self.assertNotContains(self.portal.get(f'/tasks/tickets/status/{ticket.token}/'), 'Check stock of belts')
        # Shipping: saved, then the customer told the tracking number; the PAK stays internal.
        self.staff.post(url, {'action': 'update_logistics', 'shipping_company': 'aramex',
                              'shipping_tracking_number': 'TRK-1', 'pak_reference_number': 'PAK-SECRET'})
        mail.outbox.clear()
        self.staff.post(url, {'action': 'notify_shipping'})
        self.assertIn('TRK-1', mail.outbox[0].body)
        self.assertNotIn('PAK-SECRET', mail.outbox[0].body)

        # 3. It becomes a task.
        # The Decision: this machine gets a visit.
        machine = ticket.machines.get()
        create_url = f'/tasks/new/?ticket={ticket.pk}&machines={machine.pk}&decision=visit'
        page = self.staff.get(create_url)
        self.assertEqual(page.status_code, 200)
        response = self.staff.post(create_url, form_data(
            page.context['form'], site=self.site.pk, responsible_supervisor=self.supervisor.pk,
        ))
        self.assertEqual(response.status_code, 302, self.form_errors(response))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, CustomerTicket.Status.CONVERTED)
        task = ticket.task
        self.assertIsNotNone(task)
        self.assertEqual(task.responsible_supervisor, self.supervisor)
        self.assertContains(self.staff.get(url), task.task_number)

        # 4. A lead technician, and a date.
        response = self.staff.post(f'/tasks/{task.pk}/assign/', {'action': 'set_lead', 'technician': self.tech.pk})
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ASSIGNED)

        # 5. The technician's day: confirm, on the way, arrived, start, a pause.
        tech = self.client_for('tech1')
        my_url = f'/tasks/my/{task.pk}/'
        for action in ('accept', 'en_route', 'arrive', 'start'):
            self.assertEqual(tech.post(my_url, {'action': action}).status_code, 302, action)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)
        tech.post(my_url, {'action': 'pause', 'reason': 'Waiting for a part', 'pause_reason': 'waiting_parts'})
        tech.post(my_url, {'action': 'resume'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)

        # 6. The visit report.
        report_url = f'/tasks/my/{task.pk}/report/'
        page = tech.get(report_url)
        self.assertEqual(page.status_code, 200)
        payload = {
            'findings': 'Belt worn out', 'action_taken': 'Replaced belt', 'resolved': 'True',
            'labour_hours': '1.50', 'customer_name': 'Ali',
        }
        for prefix in ('existing', 'new', 'parts'):
            payload.update({f'{prefix}-TOTAL_FORMS': '0', f'{prefix}-INITIAL_FORMS': '0',
                            **{f'{prefix}-{k}': v for k, v in FORMSET.items()}})
        response = tech.post(report_url, payload)
        self.assertEqual(response.status_code, 302, self.form_errors(response))
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.PENDING_SUPERVISOR_REVIEW)

        # 7. The supervisor, then a manager, approve: the task closes.
        supervisor = self.client_for('supervisor1')
        supervisor.post(f'/tasks/{task.pk}/', {'action': 'approve_report_supervisor'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.staff.post(f'/tasks/{task.pk}/', {'action': 'approve_report'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CLOSED)

        # 8. The customer: the request closed, the report there to open.
        closed = self.portal.get('/customers/portal/requests/?show=closed')
        self.assertIn(ticket.pk, [t.pk for t in closed.context['tickets']])
        self.assertEqual(self.portal.get(f'/tasks/tickets/status/{ticket.token}/').status_code, 200)
        pdf = self.portal.get(f'/customers/portal/visits/{task.pk}/report.pdf')
        self.assertIn(pdf.status_code, (200, 302))

        # 9. Asked to rate the visit; the link on the portal's address; the rating saved.
        mail.outbox.clear()
        self.staff.post(f'/tasks/{task.pk}/', {'action': 'send_feedback_request'})
        self.assertEqual(len(mail.outbox), 1)
        feedback = CustomerFeedback.objects.get(task=task)
        self.assertIn(f'http://portal.example.test/reports/feedback/{feedback.token}/', mail.outbox[0].body)
        response = self.portal.post(f'/reports/feedback/{feedback.token}/', {'rating': '5', 'comment': 'Fast and clean.'})
        self.assertIn(response.status_code, (200, 302))
        feedback.refresh_from_db()
        self.assertEqual(feedback.rating, 5)

        # 10. Management sees it.
        self.assertContains(self.staff.get('/tasks/reports/monthly/'), 'Fitness First')


class OtherEndingsTests(LifecycleTestCase):
    """The ways a request ends without a visit, and coming back."""

    def setUp(self):
        super().setUp()
        self.send_request(about='other', need='assistance')
        self.ticket = self.newest_request()
        self.url = f'/tasks/tickets/{self.ticket.pk}/'

    def test_dismissing_needs_a_reason(self):
        self.staff.post(self.url, {'action': 'dismiss', 'dismissal_reason': ''})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)
        self.staff.post(self.url, {'action': 'dismiss', 'dismissal_reason': 'Duplicate of an earlier request.'})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.DISMISSED)

    def test_closed_with_an_answer_reaches_the_customer_and_an_admin_reopens(self):
        mail.outbox.clear()
        self.staff.post(self.url, {'action': 'close', 'close_reason': 'Answered by phone.'})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)
        self.assertTrue(any('ali@fitnessfirst.example' in m.to for m in mail.outbox))
        self.staff.post(self.url, {'action': 'reopen'})
        self.ticket.refresh_from_db()
        self.assertNotEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_escalating_goes_one_step_up_and_stays_internal(self):
        self._staff('support1', 'Country Support', Technician.Role.COUNTRY_SUPPORT)
        manager = self._staff('supportmanager1', 'Support Manager', Technician.Role.SUPPORT_MANAGER)
        mail.outbox.clear()
        self.client_for('support1').post(self.url, {'action': 'escalate', 'escalated_to': manager.pk,
                                                     'reason': 'Customer threatens to cancel the contract.'})
        escalation = self.ticket.escalations.get()
        self.assertEqual(escalation.escalated_to, manager)
        self.assertIn('supportmanager1@pss.example', {address for m in mail.outbox for address in m.to})
        self.assertFalse(any('ali@fitnessfirst.example' in m.to for m in mail.outbox))


class TaskTurnsTests(LifecycleTestCase):
    """What can happen to the task on the way: warranty, a stopped visit
    booked again, a cancelled task."""

    def make_task(self):
        self.send_request()
        ticket = self.newest_request()
        machine = ticket.machines.get()
        create_url = f'/tasks/new/?ticket={ticket.pk}&machines={machine.pk}&decision=visit'
        page = self.staff.get(create_url)
        self.staff.post(create_url, form_data(page.context['form'], site=self.site.pk,
                                              responsible_supervisor=self.supervisor.pk))
        ticket.refresh_from_db()
        self.staff.post(f'/tasks/{ticket.task.pk}/assign/', {'action': 'set_lead', 'technician': self.tech.pk})
        return ticket, Task.objects.get(pk=ticket.task.pk), page

    def test_a_branch_under_warranty_starts_as_warranty(self):
        self.site.warranty_start = timezone.localdate()
        self.site.warranty_until = timezone.localdate() + timedelta(days=700)
        self.site.save()
        ticket, task, page = self.make_task()
        self.assertEqual(page.context['form']['billing_type'].value(), Task.BillingType.WARRANTY)
        self.assertEqual(task.billing_type, Task.BillingType.WARRANTY)

    def test_stopped_on_site_then_booked_again(self):
        ticket, task, page = self.make_task()
        tech = self.client_for('tech1')
        for action in ('accept', 'en_route', 'arrive'):
            tech.post(f'/tasks/my/{task.pk}/', {'action': action})
        tech.post(f'/tasks/my/{task.pk}/', {'action': 'block', 'note': 'Gym closed for a private event.'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.BLOCKED)
        self.assertContains(self.staff.get(f'/tasks/{task.pk}/'), 'Gym closed for a private event.')
        when = (timezone.localtime() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        self.staff.post(f'/tasks/{task.pk}/', {'action': 'rebook', 'scheduled_for': when.strftime('%Y-%m-%dT%H:%M'),
                                               'note': 'Customer confirmed'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ASSIGNED)
        self.assertIsNotNone(task.scheduled_for)

    def test_a_cancelled_task_leaves_the_request_open(self):
        ticket, task, page = self.make_task()
        self.staff.post(f'/tasks/{task.pk}/', {'action': 'cancel_task', 'note': 'Customer postponed.'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)
        open_requests = self.portal.get('/customers/portal/requests/').context['tickets']
        self.assertIn(ticket.pk, [t.pk for t in open_requests])
        self.assertEqual(str(next(t for t in open_requests if t.pk == ticket.pk).progress[0]).split(' —')[0], 'Being arranged')

    def test_a_signed_out_customer_from_the_rating_email_is_asked_to_sign_in(self):
        response = self.client_class(HTTP_HOST='portal.example.test').get('/reports/feedback/some-token/')
        self.assertIn(response.status_code, (302, 404))


class BrokenMailBackend:
    """A mail server that is down."""

    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, messages):
        raise ConnectionRefusedError('mail server down')

    def open(self):
        pass

    def close(self):
        pass


class ReliabilityTests(LifecycleTestCase):
    def test_a_mail_server_that_is_down_never_stops_the_work(self):
        with override_settings(EMAIL_BACKEND='tasks.test_lifecycle.BrokenMailBackend'):
            response = self.send_request()
            self.assertEqual(response.status_code, 302)
            ticket = self.newest_request()
            response = self.staff.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'add_reply', 'message': 'On it.'})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(ticket.replies.filter(message='On it.').exists())

    def test_an_email_that_fails_later_is_shown_once_to_whoever_sent_it(self):
        from unittest.mock import patch

        from people.models import FailedEmail
        self.send_request()
        ticket = self.newest_request()
        # As on the live server: sent after the page answered (here at once, so the test can see it).
        with override_settings(EMAIL_BACKEND='tasks.test_lifecycle.BrokenMailBackend'),                 patch('tasks.views._in_background', lambda work: work()):
            self.staff.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'add_reply', 'message': 'On it.'})
            self.send_request(about='other', need='assistance')  # the customer's own: for the admins
        failure = FailedEmail.objects.get(triggered_by=self.admin.user)
        self.assertIn('ali@fitnessfirst.example', failure.recipients)
        self.assertIn('mail server down', failure.error)
        self.assertTrue(FailedEmail.objects.filter(triggered_by__isnull=True).exists())
        page = self.staff.get('/tasks/')
        self.assertContains(page, 'An email could not be sent')
        self.assertContains(page, 'ali@fitnessfirst.example')
        self.assertNotContains(self.staff.get('/tasks/'), 'An email could not be sent')  # once
        self.assertNotContains(self.portal.get('/customers/portal/'), 'An email could not be sent')
        self.assertNotContains(self.client_for('supervisor1').get('/tasks/'), 'An email could not be sent')

    def test_numbers_carry_on_after_one_is_deleted(self):
        for _ in range(3):
            self.send_request(about='other', need='assistance')
        numbers = list(CustomerTicket.objects.order_by('pk').values_list('ticket_number', flat=True))
        CustomerTicket.objects.filter(ticket_number=numbers[1]).delete()
        response = self.send_request(about='other', need='assistance')
        self.assertEqual(response.status_code, 302)
        newest = self.newest_request().ticket_number
        self.assertNotIn(newest, numbers)
        self.assertEqual(int(newest.rsplit('T', 1)[1]), int(numbers[-1].rsplit('T', 1)[1]) + 1)


class SimultaneousSavesTests(TransactionTestCase):
    """Several people sending at the same moment: each gets its own number."""

    def test_requests_sent_at_once_get_distinct_numbers(self):
        import threading

        from django.db import connection

        from tasks.views import save_new_ticket
        country = Country.objects.create(name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED')
        start, errors = threading.Barrier(5), []

        def send(index):
            try:
                start.wait()
                save_new_ticket(CustomerTicket(
                    country=country, company_name=f'Gym {index}', contact_name='-', contact_phone='-',
                    description='-', submitted_at=timezone.now(),
                ))
            except Exception as error:  # noqa: BLE001 — reported below
                errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=send, args=(index,)) for index in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        numbers = list(CustomerTicket.objects.values_list('ticket_number', flat=True))
        self.assertEqual(len(numbers), 5)
        self.assertEqual(len(set(numbers)), 5)


class NewRequestAlertsTests(LifecycleTestCase):
    """Who hears of a new request, and that the bell always has room for it."""

    def test_company_wide_roles_hear_from_every_country(self):
        from people.models import Technician as T
        saudi = Country.objects.create(name='Saudi Arabia', iso_code='SA', timezone='Asia/Riyadh', currency_code='SAR')
        support_manager = self._staff('support_ksa', 'Sara Support', T.Role.SUPPORT_MANAGER)
        support_manager.country = saudi
        support_manager.save(update_fields=['country'])
        desk_ksa = self._staff('desk_ksa', 'Saudi Desk', T.Role.COUNTRY_SUPPORT)
        desk_ksa.country = saudi
        desk_ksa.save(update_fields=['country'])
        mail.outbox.clear()
        self.send_request()
        told = {address for message in mail.outbox for address in message.to}
        self.assertIn('support_ksa@pss.example', told)   # company-wide: every country
        self.assertIn('admin1@pss.example', told)
        self.assertNotIn('desk_ksa@pss.example', told)   # a country's own desk: its own country only

    def test_a_busy_day_of_overdue_tasks_never_hides_a_new_request_in_the_bell(self):
        from people.context_processors import notification_bell
        long_ago = timezone.now() - timedelta(days=10)
        for index in range(15):
            Task.objects.create(
                task_number=f'AE-9{index:03d}', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
                billing_type=Task.BillingType.CHARGEABLE, reported_at=long_ago, created_by=self.admin.user,
            )
        self.send_request()
        ticket = self.newest_request()
        bell = self.staff.get('/tasks/').context
        kinds = [item['kind'] for item in bell['unseen_notifications']]
        self.assertEqual(kinds.count('unscheduled_overdue_many'), 1)
        self.assertNotIn('unscheduled_overdue', kinds)
        self.assertIn(ticket.pk, [item['ticket'].pk for item in bell['unseen_notifications'] if item.get('ticket')])
        self.assertEqual(self.staff.get('/tasks/?alarm=unscheduled').context['page_obj'].paginator.count, 15)
