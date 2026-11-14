"""Launch blockers (docs/hardening_plan.md §7, step 0): nothing done twice by a double
press or two people at once; phone notifications only for a change that's kept."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from people.models import PushDevice, Technician
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskEvent, TicketMachine
from tasks.tests import TaskTestCase

User = get_user_model()


class NothingTwiceTests(TaskTestCase):

    def setUp(self):
        super().setUp()
        Technician.objects.create(
            user=User.objects.create_user('support1', password='pass12345'), country=self.country,
            full_name='Dana Support', language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0901', customer=self.customer, site=self.site,
            company_name='Fitness First', site_description='Marina Branch', contact_name='Ali',
            contact_phone='0501234567', description='(from the machines)', submitted_at=timezone.now(),
        )
        self.machine = TicketMachine.objects.create(
            ticket=self.ticket, model_name='Treadmill', serial_no='T-1', problem=TicketMachine.Problem.NOISE, position=0,
        )

    def test_a_request_decided_twice_makes_one_task(self):
        self.client.login(username='support1', password='pass12345')
        data = {'action': 'next', 'choice': 'visit', 'machines': [self.machine.pk],
                'supervisor': self.supervisor_user.technician.pk}
        before = Task.objects.count()
        self.client.post(f'/tasks/tickets/{self.ticket.pk}/', data)
        self.client.post(f'/tasks/tickets/{self.ticket.pk}/', data)  # the double click
        self.assertEqual(Task.objects.count(), before + 1)

    def test_a_tap_sent_twice_is_recorded_once(self):
        task = Task.objects.create(
            task_number='AE-0901', site=self.site, task_type=self.task_type, priority=Task.Priority.NORMAL,
            source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(),
            created_by=self.supervisor_user, status=Task.Status.ACCEPTED, scheduled_for=timezone.now(),
        )
        TaskAssignment.objects.create(task=task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=timezone.now(), is_active=True)
        token = Token.objects.create(user=self.tech_user).key
        for _ in range(2):  # an app retry on a slow connection
            self.client.post(f'/api/my-tasks/{task.pk}/action/', json.dumps({'action': 'en_route'}),
                             content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(task.events.filter(event_type=TaskEvent.EventType.EN_ROUTE).count(), 1)


class PushOnlyWhenKeptTests(TaskTestCase):

    def test_no_push_for_a_change_that_was_undone(self):
        from people.push import send_push
        PushDevice.objects.create(technician=self.technician, token='ExponentPushToken[test]', created_at=timezone.now())
        with self.settings(SEND_EMAILS_IN_BACKGROUND=True, EXPO_PUSH_ENABLED=True), \
                mock.patch('people.push.urlopen') as sent, \
                self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    send_push([self.technician], 'Hello', 'A preview')
                    raise RuntimeError('rolled back, like the report review step')
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        sent.assert_not_called()


from django.core.files.base import ContentFile  # noqa: E402
from django.core.files.storage import InMemoryStorage  # noqa: E402
from django.test import override_settings  # noqa: E402


class FakeR2Storage(InMemoryStorage):
    """Stands in for spots.storage.PrivateMediaStorage: the app's own /media/ addresses,
    and a "signed link" that says what it was asked for."""

    def url(self, name):
        return '/media/' + name

    def signed_url(self, name, download=False):
        return f'https://r2.example/{name}?signed=1' + ('&download=1' if download else '')


@override_settings(STORAGES={
    'default': {'BACKEND': 'tasks.test_launch_blockers.FakeR2Storage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class FilesOnR2Tests(TaskTestCase):

    def test_staff_get_a_short_signed_link_after_the_check(self):
        from django.core.files.storage import default_storage
        name = default_storage.save('attachments/1/belt.jpg', ContentFile(b'jpeg'))
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/media/' + name)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'https://r2.example/{name}?signed=1')
        # Not a photo, video or PDF: it downloads.
        other = default_storage.save('attachments/1/list.xlsx', ContentFile(b'xls'))
        self.assertTrue(self.client.get('/media/' + other)['Location'].endswith('&download=1'))

    def test_nobody_else_gets_a_link(self):
        from django.core.files.storage import default_storage
        name = default_storage.save('attachments/1/belt.jpg', ContentFile(b'jpeg'))
        self.assertEqual(self.client.get('/media/' + name).status_code, 404)  # signed out
        customer = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        from customers.models import PortalUser
        PortalUser.objects.create(customer=self.customer, user=customer)
        self.client.force_login(customer)
        self.assertEqual(self.client.get('/media/' + name).status_code, 404)  # not theirs

    def test_the_pdf_reads_its_images_from_the_storage(self):
        from django.core.files.storage import default_storage

        from reports.pdf import _url_fetcher
        name = default_storage.save('signatures/1/sign.png', ContentFile(b'png-bytes'))
        self.assertEqual(_url_fetcher('http://testserver/media/' + name).read(), b'png-bytes')
        with self.assertRaises(ValueError):
            _url_fetcher('http://testserver/media/../secret.txt')
