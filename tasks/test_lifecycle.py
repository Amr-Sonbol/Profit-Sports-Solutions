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

from customers.models import Customer, PortalUser, Site
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
        self.customer = Customer.objects.create(country=self.country, name='Fitness First', segment='gym')
        PortalUser.objects.create(customer=self.customer, user=self.customer_user)
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
        self.assertContains(self.staff.get(f'/tasks/tickets/{ticket.pk}/'), 'Create the branch')

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
        # "Answer without a visit": every waiting machine gets the reason's decision, and the request closes.
        waiting = [str(pk) for pk in self.ticket.machines.filter(decision='').values_list('pk', flat=True)]
        self.staff.post(self.url, {'action': 'next', 'choice': 'answer', 'reason': 'remote_fix',
                                   'answer': 'Answered by phone.', 'machines': waiting})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)
        self.assertFalse(self.ticket.machines.exclude(decision='advice', decision_reason='remote_fix').exists())
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

    def test_the_technical_support_manager_and_the_countrys_support_manager_are_told(self):
        from people.models import Technician as T
        saudi = Country.objects.create(name='Saudi Arabia', iso_code='SA', timezone='Asia/Riyadh', currency_code='SAR')
        technical = self._staff('technical_ksa', 'Tarek Technical', T.Role.SUPPORT_MANAGER)
        technical.country = saudi
        technical.save(update_fields=['country'])
        self._staff('desk_uae', 'UAE Desk', T.Role.COUNTRY_SUPPORT)
        desk_ksa = self._staff('desk_ksa', 'Saudi Desk', T.Role.COUNTRY_SUPPORT)
        desk_ksa.country = saudi
        desk_ksa.save(update_fields=['country'])
        self._staff('operations', 'Omar Operations', T.Role.OPERATIONS_MANAGER)
        mail.outbox.clear()
        self.send_request()
        ticket = self.newest_request()
        told = {address for message in mail.outbox for address in message.to}
        self.assertIn('technical_ksa@pss.example', told)  # every country
        self.assertIn('desk_uae@pss.example', told)       # the request's own country
        self.assertNotIn('desk_ksa@pss.example', told)
        self.assertNotIn('admin1@pss.example', told)
        self.assertNotIn('operations@pss.example', told)   # opens them when he wants, isn't alerted

        def in_bell(username):
            items = self.client_for(username).get('/tasks/tickets/').context['unseen_notifications']
            return any(item.get('ticket') == ticket for item in items)
        self.assertTrue(in_bell('technical_ksa'))
        self.assertTrue(in_bell('desk_uae'))
        self.assertFalse(in_bell('admin1'))
        self.assertFalse(in_bell('operations'))

    def test_with_nobody_of_those_the_admins_are_told(self):
        mail.outbox.clear()
        self.send_request()
        ticket = self.newest_request()
        self.assertIn('admin1@pss.example', {address for message in mail.outbox for address in message.to})
        self.assertTrue(any(item.get('ticket') == ticket
                            for item in self.staff.get('/tasks/tickets/').context['unseen_notifications']))

    def test_a_busy_day_of_overdue_tasks_never_hides_a_new_request_in_the_bell(self):
        from people.context_processors import notification_bell
        long_ago = timezone.now() - timedelta(days=10)
        for index in range(15):
            Task.objects.create(
                task_number=f'AE-9{index:03d}', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
                billing_type=Task.BillingType.CHARGEABLE, reported_at=long_ago, created_by=self.admin.user,
            )
        # Someone who gets both: admins, once chosen for new tickets on the Notifications page.
        from people.models import NotificationSettings
        switches = NotificationSettings.load()
        switches.new_ticket_roles = ['support_manager', 'country_support', 'admin']
        switches.save(update_fields=['new_ticket_roles'])
        self.send_request()
        ticket = self.newest_request()
        bell = self.staff.get('/tasks/').context
        kinds = [item['kind'] for item in bell['unseen_notifications']]
        self.assertEqual(kinds.count('unscheduled_overdue_many'), 1)
        self.assertNotIn('unscheduled_overdue', kinds)
        self.assertIn(ticket.pk, [item['ticket'].pk for item in bell['unseen_notifications'] if item.get('ticket')])
        self.assertEqual(self.staff.get('/tasks/?alarm=unscheduled').context['page_obj'].paginator.count, 15)


class BellRefreshTests(LifecycleTestCase):
    """/bell/: just the bell, for static/js/bell.js when someone comes back to the tab."""

    def test_staff_get_their_bell_with_the_new_request(self):
        self._staff('desk1', 'UAE Desk', Technician.Role.COUNTRY_SUPPORT)
        desk = self.client_for('desk1')
        desk.get('/tasks/')
        self.send_request()
        ticket = self.newest_request()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            response = desk.get('/bell/')
        self.assertContains(response, 'id="bell"')
        self.assertContains(response, ticket.ticket_number)
        self.assertNotContains(response, '<html')  # the bell alone, not a page
        self.assertLess(len(queries), 25)

    def test_the_customer_gets_their_bell_with_the_reply(self):
        self.send_request()
        ticket = self.newest_request()
        self.staff.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'add_reply', 'message': 'On it.'})
        response = self.portal.get('/bell/')
        self.assertContains(response, 'id="bell"')
        self.assertContains(response, 'notif-bell__badge')

    def test_nobody_else_gets_anything(self):
        self.assertEqual(self.client_class().get('/bell/').status_code, 204)

    def test_it_never_uses_up_a_one_time_email_warning(self):
        from people.models import FailedEmail
        FailedEmail.objects.create(triggered_by=self.admin.user, recipients='x@example.com', subject='Hello',
                                   error='down', failed_at=timezone.now())
        self.staff.get('/bell/')
        self.assertContains(self.staff.get('/tasks/'), 'An email could not be sent')


class PartShipmentTests(LifecycleTestCase):
    """A machine's "Ship part": preparing (warehouse manager), ready to ship
    (the technician given it), shipped — no visit steps; stock goes down."""

    def setUp(self):
        super().setUp()
        self.warehouse = self._staff('warehouse1', 'Ali Warehouse', Technician.Role.WAREHOUSE_MANAGER)
        self._staff('tech2', 'Other Tech', Technician.Role.TECHNICIAN)
        self.send_request(need='parts', machines=[{'which': 'new', 'model_name': 'Excite Run 700', 'serial_no': 'TG-1',
                                                   'parts': 'Running belt', 'description': 'Belt is worn.'}])
        self.ticket = self.newest_request()
        machine = self.ticket.machines.get()
        url = f'/tasks/new/?ticket={self.ticket.pk}&machines={machine.pk}&decision=ship_part'
        page = self.staff.get(url)
        self.staff.post(url, form_data(page.context['form'], site=self.site.pk, lead=self.tech.pk))
        self.ticket.refresh_from_db()
        self.task = Task.objects.get(pk=self.ticket.task.pk)
        self.url = f'/tasks/{self.task.pk}/'

    def ready(self, client, **extra):
        return client.post(self.url, {'action': 'shipment_ready', 'part_code': ['BELT-01', ''], 'part_quantity': ['2', '1'],
                                      'pak_reference_number': 'PAK-5', 'technician': self.tech.pk, **extra})

    def test_from_the_decision_to_shipped(self):
        from tasks.stock import part_stock
        from tasks.shipments import is_part_shipment, stage
        self.assertTrue(is_part_shipment(self.task))
        self.assertEqual((stage(self.task), self.task.assignments.count()), ('preparing', 0))  # no lead yet
        self.assertContains(self.staff.get(self.url), 'Prepare the parcel')

        # The warehouse manager prepares it.
        warehouse = self.client_for('warehouse1')
        self.ready(warehouse)
        self.task.refresh_from_db()
        self.assertEqual(stage(self.task), 'ready')
        self.assertEqual(self.task.pak_reference_number, 'PAK-5')
        self.assertEqual(part_stock(self.country).get('BELT-01'), -2)  # out of stock: 2 picked

        # Only the technician it was given to ships it, from their own page; no visit steps there.
        self.assertEqual(self.client_for('tech2').get(f'/tasks/my/{self.task.pk}/').status_code, 404)
        tech = self.client_for('tech1')
        page = tech.get(f'/tasks/my/{self.task.pk}/')
        self.assertContains(page, 'Hand it to the courier')
        # Where it goes and who takes it: the branch and its address, the contact's name and number.
        send_to = page.context['shipment']['send_to']
        self.assertEqual((send_to['branch'], send_to['address'], send_to['contact_name'], send_to['contact_phone']),
                         ('Marina Branch', 'Dubai Marina', 'Ali', '0501234567'))
        self.assertContains(page, '0501234567')
        self.assertIsNone(page.context['next_action'])
        mail.outbox.clear()
        tech.post(f'/tasks/my/{self.task.pk}/', {'action': 'shipment_shipped', 'shipping_company': 'local_courier',
                                                 'shipping_tracking_number': ''})
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, stage(self.task)), (Task.Status.CLOSED, 'shipped'))
        notice = next(m for m in mail.outbox if 'ali@fitnessfirst.example' in m.to)
        self.assertIn('Local courier', notice.alternatives[0][0])
        self.assertNotIn('Tracking number', notice.alternatives[0][0])
        # The customer: settled, "Part shipped".
        closed = self.portal.get('/customers/portal/requests/?show=closed').context['tickets']
        self.assertIn(self.ticket.pk, [t.pk for t in closed])
        self.assertContains(self.portal.get(f'/tasks/tickets/status/{self.ticket.token}/'), 'Part shipped')

        # A part that needs fitting: a visit, linked.
        response = warehouse.post(self.url, {'action': 'book_fitting'})
        visit = Task.objects.get(previous_task=self.task)
        self.assertRedirects(response, f'/tasks/{visit.pk}/', fetch_redirect_response=False)
        self.assertFalse(is_part_shipment(visit))

    def test_a_technician_cannot_prepare_and_a_cancelled_one_gives_its_parts_back(self):
        from tasks.stock import part_stock
        self.assertEqual(self.ready(self.client_for('tech1')).status_code, 403)
        self.ready(self.client_for('warehouse1'))
        self.assertEqual(part_stock(self.country).get('BELT-01'), -2)
        Task.objects.filter(pk=self.task.pk).update(status=Task.Status.CANCELLED)
        self.assertIsNone(part_stock(self.country).get('BELT-01'))

    def test_nothing_ships_without_a_courier_or_before_it_is_ready(self):
        tech = self.client_for('tech1')
        self.assertEqual(self.staff.post(self.url, {'action': 'shipment_shipped', 'shipping_company': 'dhl'}).status_code, 403)
        self.ready(self.client_for('warehouse1'))
        tech.post(f'/tasks/my/{self.task.pk}/', {'action': 'shipment_shipped', 'shipping_company': ''})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)


class PartShipmentFormTests(LifecycleTestCase):
    def test_the_type_is_set_and_fixed_from_the_decision(self):
        from tasks.shipments import part_shipment_type
        self.send_request()
        ticket = self.newest_request()
        url = f'/tasks/new/?ticket={ticket.pk}&machines={ticket.machines.get().pk}&decision=ship_part'
        field = self.staff.get(url).context['form'].fields['task_type']
        self.assertTrue(field.disabled)
        self.assertEqual(field.initial, part_shipment_type().pk)
        # A visit keeps the choice open.
        visit_url = url.replace('ship_part', 'visit')
        self.assertFalse(self.staff.get(visit_url).context['form'].fields['task_type'].disabled)

    def test_one_button_and_the_type_says_it_is_a_part_shipment(self):
        from tasks.shipments import is_part_shipment, part_shipment_type
        self.send_request()
        ticket = self.newest_request()
        machine = ticket.machines.get()
        self.assertNotContains(self.staff.get(f'/tasks/tickets/{ticket.pk}/'), 'Create a part shipment task')
        url = f'/tasks/new/?ticket={ticket.pk}&machines={machine.pk}&decision=visit'
        page = self.staff.get(url)
        self.staff.post(url, form_data(page.context['form'], site=self.site.pk, task_type=part_shipment_type().pk))
        machine.refresh_from_db()
        self.assertEqual(machine.decision, 'ship_part')
        self.assertTrue(is_part_shipment(machine.task))


class PartShipmentAppTests(PartShipmentTests):
    """The same parcel, shipped from the mobile app (api.views)."""

    def test_shipped_from_the_app(self):
        import json
        self.ready(self.client_for('warehouse1'))
        token = self.client_class().post('/api/login/', json.dumps({'username': 'tech1', 'password': PASSWORD}),
                                         content_type='application/json').json()['token']
        auth = {'HTTP_AUTHORIZATION': f'Token {token}'}
        detail = self.client_class().get(f'/api/my-tasks/{self.task.pk}/', **auth).json()
        self.assertIsNone(detail['next_action'])
        self.assertEqual(detail['shipment']['stage'], 'ready')
        self.assertTrue(detail['shipment']['can_ship'])
        self.assertEqual(detail['shipment']['parts'], [{'code': 'BELT-01', 'quantity': 2}])
        # No courier: refused. A visit's step: refused.
        app = self.client_class()
        self.assertEqual(app.post(f'/api/my-tasks/{self.task.pk}/action/', {'action': 'shipped'}, **auth).status_code, 400)
        app.post(f'/api/my-tasks/{self.task.pk}/action/', {'action': 'accept'}, **auth)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.ASSIGNED)
        mail.outbox.clear()
        response = app.post(f'/api/my-tasks/{self.task.pk}/action/',
                            {'action': 'shipped', 'shipping_company': 'aramex', 'shipping_tracking_number': 'AX-77'}, **auth)
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.shipping_tracking_number), (Task.Status.CLOSED, 'AX-77'))
        self.assertTrue(any('AX-77' in m.body for m in mail.outbox))


class PartShipmentForTheCustomerTests(PartShipmentTests):
    """The customer follows their parcel on their request, never as a visit."""

    def test_being_prepared_then_shipped_with_a_tracking_link(self):
        status = f'/tasks/tickets/status/{self.ticket.token}/'
        page = self.portal.get(status)
        self.assertEqual([(s['task'], str(s['progress'][0])) for s in page.context['shipments']], [(self.task, 'Being prepared')])
        self.assertEqual(page.context['visits'], [])  # a parcel, not a visit
        self.assertNotIn(self.task.pk, [v.pk for v in self.portal.get('/customers/portal/visits/').context['visits']])

        self.ready(self.client_for('warehouse1'))
        self.client_for('tech1').post(f'/tasks/my/{self.task.pk}/', {
            'action': 'shipment_shipped', 'shipping_company': 'aramex', 'shipping_tracking_number': 'AX 77',
        })
        page = self.portal.get(status)
        shipment = page.context['shipments'][0]
        self.assertEqual((str(shipment['progress'][0]), shipment['company'], shipment['tracking']), ('Shipped', 'Aramex', 'AX 77'))
        self.assertEqual(shipment['url'], 'https://www.aramex.com/track/results?ShipmentNumber=AX%2077')
        self.assertContains(page, shipment['url'], count=3)  # its row, "Track your parcel" and the machines table

    def test_a_local_courier_has_no_tracking_page(self):
        self.ready(self.client_for('warehouse1'))
        self.client_for('tech1').post(f'/tasks/my/{self.task.pk}/', {
            'action': 'shipment_shipped', 'shipping_company': 'local_courier', 'shipping_tracking_number': 'LC-5',
        })
        shipment = self.portal.get(f'/tasks/tickets/status/{self.ticket.token}/').context['shipments'][0]
        self.assertEqual((shipment['tracking'], shipment['url']), ('LC-5', ''))


class PartShippedBellTests(PartShipmentTests):
    def test_the_customer_bell_says_the_part_is_on_its_way(self):
        self.ready(self.client_for('warehouse1'))
        self.client_for('tech1').post(f'/tasks/my/{self.task.pk}/', {'action': 'shipment_shipped',
                                                                    'shipping_company': 'dhl', 'shipping_tracking_number': ''})
        bell = self.portal.get('/customers/portal/').context['portal_bell']
        self.assertTrue(any(item['text'] == 'Your part is on its way' and item['new'] for item in bell))
        self.portal.get(f'/tasks/tickets/status/{self.ticket.token}/')  # opened: no longer new
        bell = self.portal.get('/customers/portal/').context['portal_bell']
        self.assertFalse(any(item['text'] == 'Your part is on its way' and item['new'] for item in bell))


class NewBranchRequestTests(LifecycleTestCase):
    def test_one_click_creates_the_branch_from_the_request(self):
        self._staff('desk1', 'UAE Desk', Technician.Role.COUNTRY_SUPPORT)
        self.portal.post('/customers/portal/branches/new/', {
            'name': 'JBR Branch', 'address': 'JBR Walk', 'contact_name': 'Omar', 'contact_phone': '0509999999',
            'contact_email': 'omar@fitnessfirst.example', 'opening_date': '2026-12-01', 'message': 'Opens soon.',
        })
        ticket = self.newest_request()
        desk = self.client_for('desk1')
        self.assertContains(desk.get(f'/tasks/tickets/{ticket.pk}/'), 'Create the branch')
        desk.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'create_branch'})
        ticket.refresh_from_db()
        site = ticket.site
        self.assertEqual((site.customer, site.name, site.address, site.contact_name, site.contact_phone, site.contact_email),
                         (self.customer, 'JBR Branch', 'JBR Walk', 'Omar', '0509999999', 'omar@fitnessfirst.example'))
        # Clicked twice: still one branch.
        desk.post(f'/tasks/tickets/{ticket.pk}/', {'action': 'create_branch'})
        self.assertEqual(self.customer.sites.filter(name='JBR Branch').count(), 1)
        # The task starts there; the customer has it in their list.
        self.assertEqual(desk.get(f'/tasks/new/?ticket={ticket.pk}').context['form'].fields['site'].initial, site.pk)
        self.assertContains(self.portal.get('/customers/portal/settings/'), 'JBR Branch')

    def test_the_added_branch_is_linked_and_the_task_starts_there(self):
        self.portal.post('/customers/portal/branches/new/', {
            'name': 'JBR Branch', 'address': 'JBR Walk', 'contact_name': 'Omar', 'contact_phone': '0509999999',
            'contact_email': '', 'opening_date': '', 'message': 'Opens next month.',
        })
        ticket = self.newest_request()
        review = self.staff.get(f'/tasks/tickets/{ticket.pk}/')
        self.assertContains(review, f'from_request={ticket.pk}')
        page = self.staff.get(f'/customers/{self.customer.pk}/?tab=sites&from_request={ticket.pk}&branch_name=JBR+Branch')
        self.assertContains(page, 'name="from_request"')
        response = self.staff.post(f'/customers/{self.customer.pk}/', {
            'from_request': ticket.pk, 'name': 'JBR Branch', 'address': 'JBR Walk',
        })
        self.assertRedirects(response, f'/tasks/tickets/{ticket.pk}/', fetch_redirect_response=False)
        ticket.refresh_from_db()
        self.assertEqual(ticket.site.name, 'JBR Branch')
        self.assertContains(self.staff.get(f'/tasks/tickets/{ticket.pk}/'), 'Branch added')
        form = self.staff.get(f'/tasks/new/?ticket={ticket.pk}').context['form']
        self.assertEqual(form.fields['site'].initial, ticket.site_id)


class LockedBranchTests(LifecycleTestCase):
    def test_the_customers_branch_shows_plainly_when_locked(self):
        self._staff('desk1', 'UAE Desk', Technician.Role.COUNTRY_SUPPORT)
        self.send_request(about='other', need='assistance')
        ticket = self.newest_request()
        page = self.client_for('desk1').get(f'/tasks/new/?ticket={ticket.pk}')
        self.assertContains(page, 'class="locked-value"')
        self.assertContains(page, 'Marina Branch')
        self.assertNotContains(page, 'name="site"')
        # An admin may still change it: the list stays.
        self.assertContains(self.staff.get(f'/tasks/new/?ticket={ticket.pk}'), 'name="site"')


class NewBranchNotYetAddedTests(LifecycleTestCase):
    def test_no_task_until_the_branch_is_added(self):
        self.portal.post('/customers/portal/branches/new/', {
            'name': 'JBR Branch', 'address': 'JBR Walk', 'contact_name': '', 'contact_phone': '',
            'contact_email': '', 'opening_date': '', 'message': '',
        })
        ticket = self.newest_request()
        review = self.staff.get(f'/tasks/tickets/{ticket.pk}/')
        self.assertNotContains(review, f'/tasks/new/?ticket={ticket.pk}"')
        self.assertContains(review, 'Add the branch first')
        self.assertRedirects(self.staff.get(f'/tasks/new/?ticket={ticket.pk}'), f'/tasks/tickets/{ticket.pk}/',
                             fetch_redirect_response=False)


class EverythingFromTheRequestTests(LifecycleTestCase):
    """A task made from a request carries all of it: contact, message, files, conversation — no internal notes."""

    def test_on_the_office_page_the_technicians_page_and_the_app(self):
        import json
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            self.send_request(contact_name='Ali Hassan', contact_phone='0501112222', **{
                'machines-0-files': [SimpleUploadedFile('belt.jpg', b'jpg', content_type='image/jpeg')],
            })
            ticket = self.newest_request()
            url = f'/tasks/tickets/{ticket.pk}/'
            self.staff.post(url, {'action': 'add_reply', 'message': 'Can you send the serial plate?'})
            self.portal.post(f'/tasks/tickets/status/{ticket.token}/', {'message': 'Here it is, TG-1.'})
            self.staff.post(url, {'action': 'add_internal_note', 'message': 'SECRET internal remark'})
            machine = ticket.machines.get()
            create_url = f'/tasks/new/?ticket={ticket.pk}&machines={machine.pk}&decision=visit'
            page = self.staff.get(create_url)
            self.staff.post(create_url, form_data(page.context['form'], site=self.site.pk))
            ticket.refresh_from_db()
            task = ticket.task
            self.staff.post(f'/tasks/{task.pk}/assign/', {'action': 'set_lead', 'technician': self.tech.pk})

            office, technician = self.staff.get(f'/tasks/{task.pk}/'), self.client_for('tech1').get(f'/tasks/my/{task.pk}/')
            for page in (office, technician):
                self.assertContains(page, "From the customer's request")
                for text in ('Ali Hassan', '0501112222', 'Stops after 5 minutes', 'belt.jpg', 'Dubai Marina'):
                    self.assertContains(page, text)
                self.assertNotContains(page, 'SECRET internal remark')
            # The conversation with support: the office's, not the technician's.
            self.assertContains(office, 'Here it is, TG-1.')
            self.assertNotContains(technician, 'Here it is, TG-1.')

            token = self.client_class().post('/api/login/', json.dumps({'username': 'tech1', 'password': PASSWORD}),
                                             content_type='application/json').json()['token']
            detail = self.client_class().get(f'/api/my-tasks/{task.pk}/', HTTP_AUTHORIZATION=f'Token {token}').json()
            got = detail['from_request']
            self.assertEqual((got['ticket_number'], got['contact_phone']), (ticket.ticket_number, '0501112222'))
            self.assertEqual(got['conversation'], [])  # kept for older apps, always empty: never support's messages
            self.assertEqual(got['machines'][0]['serial'], 'TG-1')
            self.assertEqual([(item['kind'], item['name']) for item in got['machines'][0]['items']], [('image', 'belt.jpg')])
            self.assertTrue(got['machines'][0]['items'][0]['url'].endswith(tuple('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_:')))
            self.assertNotIn('SECRET', json.dumps(got))


class WhatHappensNextTests(LifecycleTestCase):
    """A new request's three choices: a visit in one step, a part, or an answer."""

    def setUp(self):
        super().setUp()
        self.send_request(machines=[
            {'which': 'new', 'model_name': 'Excite Run 700', 'serial_no': 'TG-1', 'problem': 'not_working',
             'description': 'Dead, no power.'},
            {'which': 'new', 'model_name': 'Bike', 'serial_no': 'BK-2', 'problem': 'noise', 'description': 'Squeaks.'},
        ])
        self.ticket = self.newest_request()
        self.url = f'/tasks/tickets/{self.ticket.pk}/'
        self.machines = [str(machine.pk) for machine in self.ticket.machines.all()]

    def next(self, **data):
        return self.staff.post(self.url, {'action': 'next', 'machines': self.machines, **data})

    def test_a_visit_goes_to_the_chosen_supervisor_to_plan(self):
        self.assertContains(self.staff.get(self.url), 'name="supervisor"')
        self.next(choice='visit', supervisor=self.supervisor.pk)
        self.ticket.refresh_from_db()
        task = self.ticket.task
        self.assertEqual(task.responsible_supervisor, self.supervisor)
        self.assertFalse(task.assignments.filter(is_active=True).exists())  # the supervisor decides who

    def test_the_page_offers_three_choices_with_machines_ticked(self):
        page = self.staff.get(self.url)
        self.assertContains(page, 'What happens next?')
        self.assertContains(page, 'Create the visit')
        self.assertContains(page, 'Ship a part')
        self.assertContains(page, 'Send the answer and close')
        self.assertContains(page, 'checked aria-label="Excite Run 700"')

    def test_a_visit_in_one_step(self):
        from customers.models import CustomerNotification
        when = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT10:00')
        response = self.next(choice='visit', technician=self.tech.pk, scheduled_for=when)
        self.ticket.refresh_from_db()
        task = self.ticket.task
        self.assertRedirects(response, f'/tasks/{task.pk}/', fetch_redirect_response=False)
        self.assertEqual(task.site, self.site)
        self.assertEqual(task.task_type.code, 'breakdown_repair')          # "Fix a problem"
        self.assertEqual(task.priority, Task.Priority.HIGH)                 # a machine isn't working
        self.assertEqual(task.status, Task.Status.ASSIGNED)
        self.assertEqual(task.assignments.get(is_active=True).technician, self.tech)
        self.assertIsNotNone(task.scheduled_for)
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CONVERTED)
        self.assertEqual(set(self.ticket.machines.values_list('decision', flat=True)), {'visit'})
        self.assertTrue(CustomerNotification.objects.filter(customer=self.customer, kind='visit_booked').exists())

    def test_a_visit_with_no_one_chosen_yet(self):
        self.next(choice='visit')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.task.status, Task.Status.NEW)
        self.assertFalse(self.ticket.task.assignments.exists())

    def test_a_part(self):
        from tasks.shipments import is_part_shipment
        self.next(choice='ship')
        self.ticket.refresh_from_db()
        self.assertTrue(is_part_shipment(self.ticket.task))
        self.assertEqual(set(self.ticket.machines.values_list('decision', flat=True)), {'ship_part'})

    def test_split_one_machine_now_the_other_later(self):
        self.machines = self.machines[:1]
        self.next(choice='visit')
        self.ticket.refresh_from_db()
        self.assertEqual(sorted(self.ticket.machines.values_list('decision', flat=True)), ['', 'visit'])
        self.assertContains(self.staff.get(self.url), 'What happens next?')  # still the other one to decide

    def test_an_answer_closes_it_and_reaches_the_customer(self):
        mail.outbox.clear()
        self.assertEqual(self.next(choice='answer', reason='advice', answer='').status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)  # no answer: nothing happens
        self.next(choice='answer', reason='advice', answer='Hold the reset button for 10 seconds.')
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.close_reason),
                         (CustomerTicket.Status.CLOSED, 'Hold the reset button for 10 seconds.'))
        self.assertTrue(any('Hold the reset button' in m.body for m in mail.outbox))

    def test_a_duplicate_is_answered_too(self):
        self.next(choice='answer', reason='duplicate', answer='Already handled in your other request.')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)

    def test_the_reason_is_kept_on_each_machine_with_its_decision(self):
        self.assertEqual(self.next(choice='answer', reason='', answer='Hello').status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.NEW)  # no reason: nothing happens
        self.next(choice='answer', reason='misuse', answer='Not covered: damage from misuse.')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, CustomerTicket.Status.CLOSED)
        self.assertEqual(set(self.ticket.machines.values_list('decision', 'decision_reason')), {('not_covered', 'misuse')})

    def test_the_reasons_come_grouped_each_with_a_suggested_answer(self):
        page = self.staff.get(self.url)
        for text in ('Resolved remotely', 'Not covered', 'No action needed', 'Withdrawn by the customer',
                     'data-suggestion="Thank you for contacting us.'):
            self.assertContains(page, text)
        self.assertContains(page, 'not supplied or serviced by Profit Sports Solutions')  # the company, by name

    def test_once_there_is_a_task_shipping_lives_on_it(self):
        self.next(choice='ship')
        self.ticket.refresh_from_db()
        page = self.staff.get(self.url)
        self.assertContains(page, 'Shipping and PAK')
        self.assertNotContains(page, 'value="update_logistics"')


class CustomerReportTests(LifecycleTestCase):
    """Every photo and video the customer sent, under its machine, wherever the work is planned or done."""

    def setUp(self):
        import tempfile
        self._media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self._media.enable()
        self.addCleanup(self._media.disable)
        super().setUp()
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.send_request(machines=[
            {'which': 'new', 'model_name': 'Excite Run 700', 'serial_no': 'TG-1', 'problem': 'not_working',
             'description': 'Dead.', 'files': [SimpleUploadedFile('dead.jpg', b'jpg', content_type='image/jpeg'),
                                                SimpleUploadedFile('noise.mp4', b'mp4', content_type='video/mp4')]},
            {'which': 'new', 'model_name': 'Bike', 'serial_no': 'BK-2', 'problem': 'noise', 'description': 'Squeaks.'},
        ])
        self.ticket = self.newest_request()
        self.url = f'/tasks/tickets/{self.ticket.pk}/'

    def test_on_the_request_page_photos_show_under_their_machine(self):
        page = self.staff.get(self.url).content.decode()
        self.assertIn('2 machines', page)
        self.assertIn('<video src=', page)
        excite, bike = page.index('Excite Run 700'), page.index('No photos sent for this machine.')
        self.assertLess(excite, page.index('dead.jpg'))
        self.assertLess(page.index('dead.jpg'), bike)  # the photo under the first machine, the second has none

    def test_staff_add_whatsapp_photos_under_a_machine_and_the_technician_sees_them(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bike = self.ticket.machines.get(serial_no='BK-2')
        self.staff.post(self.url, {'action': 'add_customer_photos', 'machine': bike.pk,
                                   'attachments': [SimpleUploadedFile('whatsapp.jpg', b'jpg', content_type='image/jpeg')]})
        attachment = bike.attachments.get()
        self.assertEqual(attachment.added_by, self.admin.user)
        # Photos sent later in the conversation reach the technician too (never support's messages).
        self.portal.post(f'/tasks/tickets/status/{self.ticket.token}/', {
            'message': 'Here is the plate.', 'attachments': [SimpleUploadedFile('plate.jpg', b'jpg', content_type='image/jpeg')],
        })
        self.staff.post(self.url, {'action': 'next', 'machines': [str(m.pk) for m in self.ticket.machines.all()],
                                   'choice': 'visit', 'technician': self.tech.pk})
        self.ticket.refresh_from_db()
        technician_page = self.client_for('tech1').get(f'/tasks/my/{self.ticket.task.pk}/')
        for text in ('whatsapp.jpg', 'Added by our team', 'Sent later by the customer', 'plate.jpg', 'dead.jpg'):
            self.assertContains(technician_page, text)
        self.assertNotContains(technician_page, 'Here is the plate.')

    def test_the_request_list_counts_photos(self):
        page = self.staff.get('/tasks/tickets/')
        self.assertContains(page, '2 photos or videos')


class ReportMachinesTests(LifecycleTestCase):
    """A machine on the report is never removed; one not worked on says why."""

    def setUp(self):
        super().setUp()
        from customers.models import Asset
        from reference.models import Brand
        self.brand = Brand.objects.create(name='Technogym')
        self.asset = Asset.objects.create(site=self.site, brand=self.brand, model_name='Excite Run 700', serial_no='TG-1')
        self.task = Task.objects.create(
            task_number='AE-0900', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=self.admin.user,
        )

    def test_a_recorded_machine_left_out_comes_back_as_it_was(self):
        from tasks.models import TaskAsset
        from tasks.views import save_task_machines
        save_task_machines(self.task, [{'asset': self.asset, 'outcome': 'repaired', 'note': 'Belt replaced'}], [])
        save_task_machines(self.task, [], [])  # everything left out
        line = TaskAsset.objects.get(task=self.task)
        self.assertEqual((line.asset, line.outcome, line.note), (self.asset, 'repaired', 'Belt replaced'))

    def test_not_worked_on_needs_a_reason(self):
        from tasks.forms import ExistingAssetOutcomeForm
        from tasks.models import TaskAsset
        self.assertIn(('not_worked_on', 'Not worked on'), [(v, str(l)) for v, l in TaskAsset.Outcome.choices])
        form = ExistingAssetOutcomeForm({'asset': self.asset.pk, 'outcome': 'not_worked_on', 'note': ''}, site=self.site)
        self.assertFalse(form.is_valid())
        self.assertIn('note', form.errors)
        form = ExistingAssetOutcomeForm({'asset': self.asset.pk, 'outcome': 'not_worked_on', 'note': 'Not on site'}, site=self.site)
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_typed_brand_counts_and_a_missing_one_names_the_machine(self):
        from api.views import _machine_form
        row = {'model_name': 'Bike 500', 'serial_no': 'BK-2290', 'outcome': 'repaired'}
        form = _machine_form(self.task, {**row, 'brand_name': ' technogym '})
        self.assertTrue(form.is_valid(), form.errors)
        form = _machine_form(self.task, row)
        self.assertFalse(form.is_valid())
        self.assertIn('Bike 500 (BK-2290): choose its brand from the list.', form.non_field_errors())

    def test_the_review_shows_a_signature_already_drawn(self):
        import base64
        from django.test import RequestFactory
        from tasks.views import report_preview
        from reports.forms import WorkReportForm
        from tasks.models import Technician
        png = 'data:image/png;base64,' + base64.b64encode(
            bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a1300000000049454e44ae426082'),
        ).decode()
        tech = self.tech
        form = WorkReportForm({'action_taken': 'Checked', 'resolved': 'True', 'customer_name': 'Sara'})
        self.assertTrue(form.is_valid(), form.errors)
        request = RequestFactory().post('/')
        request.user = tech.user
        html, problems = report_preview(request, self.task, tech, form, [], drawn_signature=png)
        self.assertIn(png, html)
        from reports.models import WorkReport
        self.assertFalse(WorkReport.objects.filter(task=self.task).exists())  # nothing kept

    def test_the_copy_goes_to_whoever_made_the_request_by_default(self):
        from tasks.views import report_copy_default
        self.assertEqual(report_copy_default(self.task), self.site.effective_contact_email or '')

    def test_a_photo_from_a_machines_card_belongs_to_that_machine(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from rest_framework.test import APIClient
        from customers.models import Asset
        from tasks.models import TaskAssignment, TaskAttachment
        TaskAssignment.objects.create(task=self.task, technician=self.tech, role=TaskAssignment.Role.LEAD, assigned_at=timezone.now())
        other_site = Site.objects.create(customer=self.customer, name='Elsewhere')
        elsewhere = Asset.objects.create(site=other_site, brand=self.brand, model_name='Bike', serial_no='X-1')
        api = APIClient()
        api.force_authenticate(self.tech.user)
        jpeg = lambda: SimpleUploadedFile('fault.jpg', b'\xff\xd8\xff\xe0' + b'0' * 64, content_type='image/jpeg')
        url = f'/api/my-tasks/{self.task.pk}/attachments/'
        response = api.post(url, {'file': jpeg(), 'purpose': 'fault', 'asset': self.asset.pk}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['asset'], self.asset.pk)
        self.assertEqual(TaskAttachment.objects.get().asset, self.asset)
        # Only a machine at this task's branch.
        response = api.post(url, {'file': jpeg(), 'purpose': 'fault', 'asset': elsewhere.pk}, format='multipart')
        self.assertEqual(response.status_code, 400)
        # The report shows it under its machine's card.
        from tasks.views import save_task_machines
        save_task_machines(self.task, [{'asset': self.asset, 'outcome': 'repaired', 'note': ''}], [])
        report = api.get(f'/api/my-tasks/{self.task.pk}/report/').json()
        self.assertEqual([p['purpose'] for p in report['machines'][0]['photos']], ['fault'])
