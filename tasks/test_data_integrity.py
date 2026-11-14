"""Data integrity (docs/hardening_plan.md §2): what the database itself keeps, and times
in the right country."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Country
from tasks.models import CustomerTicket, Task
from tasks.tests import TaskTestCase

User = get_user_model()


class OneSerialPerBrandTests(TaskTestCase):

    def test_the_database_refuses_a_second_machine_with_the_same_serial_and_brand(self):
        Asset.objects.create(site=self.site, brand=self.brand, model_name='Run 700', serial_no='TG-123')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Asset.objects.create(site=self.site, brand=self.brand, model_name='Run 700', serial_no='tg-123')
        # No serial: as many as needed (not known yet).
        Asset.objects.create(site=self.site, brand=self.brand, model_name='Bench')
        Asset.objects.create(site=self.site, brand=self.brand, model_name='Bench')


class TimesInTheRequestsOwnCountryTests(TaskTestCase):

    def test_a_visit_time_from_another_countrys_request_is_that_countrys(self):
        # Looking at the UAE, converting an Egyptian request: "10:00" is 10:00 in Cairo.
        egypt = Country.objects.create(name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP')
        cairo = Site.objects.create(customer=Customer.objects.create(country=egypt, name='Cairo Gym', segment='gym'),
                                    name='Zamalek')
        ticket = CustomerTicket.objects.create(
            country=egypt, ticket_number='EG-T0001', customer=cairo.customer, site=cairo, company_name='Cairo Gym',
            site_description='Zamalek', contact_name='Omar', contact_phone='0100', description='Belt',
            submitted_at=timezone.now(),
        )
        support = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(user=support, country=self.country, full_name='Dana Support', language='en',
                                  role=Technician.Role.SUPPORT_MANAGER, employment_type='staff')
        self.client.force_login(support)
        page = self.client.get(f'/tasks/new/?ticket={ticket.pk}')
        form = page.context['form']
        data = {name: ('' if form[name].value() is None else form[name].value()) for name in form.fields}
        data.update({'scheduled_for': '2026-12-02T10:00', 'priority': Task.Priority.NORMAL,
                     'responsible_supervisor': ''})
        data = {k: v for k, v in data.items() if v not in (None, False)}
        self.client.post(f'/tasks/new/?ticket={ticket.pk}', data)
        task = Task.objects.get(site=cairo)
        self.assertEqual(task.scheduled_for, datetime(2026, 12, 2, 10, 0, tzinfo=ZoneInfo('Africa/Cairo')))
