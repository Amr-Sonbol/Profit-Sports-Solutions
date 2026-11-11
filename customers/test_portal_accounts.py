"""A customer's account, end to end, through the real pages: the welcome
link, a temporary password read out by phone, a forgotten password, and
changing the sign-in email. Run before a release:

    python manage.py test customers.test_portal_accounts
"""
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from customers.models import Customer, Site
from people.models import Technician
from reference.models import Country

User = get_user_model()
PORTAL = 'portal.example.test'
SETUP = {
    'contact_name': 'Ali', 'contact_phone': '0501234567', 'contact_email': 'ali@newgym.example',
    'billing_address': 'Office 4, Dubai', 'shipping_address': 'Warehouse 2, Dubai',
    'new_password1': 'Strong-Pass-2026!', 'new_password2': 'Strong-Pass-2026!',
}


def portal_link(message):
    links = re.findall(rf'http://{re.escape(PORTAL)}(/[^\s"<>]+)', message.body)
    assert links, message.body
    return links[0]


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SEND_EMAILS_IN_BACKGROUND=False,
    PORTAL_HOST=PORTAL, ALLOWED_HOSTS=['testserver', PORTAL],
)
class PortalAccountTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED')
        admin = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(user=admin, country=self.country, full_name='Amina Admin', language='en',
                                  role=Technician.Role.ADMIN, employment_type='staff')
        self.customer = Customer.objects.create(country=self.country, name='New Gym', segment='gym',
                                                contact_email='ali@newgym.example')
        Site.objects.create(customer=self.customer, name='New Gym Main', address='Dubai')
        self.staff = self.client_class()
        self.staff.login(username='admin1', password='pass12345')

    def visitor(self):
        return self.client_class(HTTP_HOST=PORTAL)

    def accounts(self, action, **extra):
        return self.staff.post('/customers/portal-login/', {'action': action, 'customer': self.customer.pk, **extra})

    def can_sign_in(self, password, username='ali@newgym.example'):
        response = self.visitor().post('/customers/portal/login/', {'username': username, 'password': password})
        return response.status_code == 302

    def test_the_welcome_email_sets_up_the_account(self):
        self.accounts('invite')
        self.assertEqual(mail.outbox[-1].to, ['ali@newgym.example'])
        path = portal_link(mail.outbox[-1])
        customer = self.visitor()
        self.assertEqual(customer.get(path).status_code, 200)
        response = customer.post(path, SETUP)
        self.assertEqual(response.status_code, 302, getattr(response.context, 'get', lambda _: None)('form') and response.context['form'].errors)
        self.assertEqual(customer.get('/customers/portal/').status_code, 200)  # signed in, home opens
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.must_change_password)
        self.assertEqual(self.customer.billing_address, 'Office 4, Dubai')
        self.assertTrue(self.can_sign_in('Strong-Pass-2026!'))
        # The link works once.
        self.assertTemplateUsed(self.visitor().get(path), 'customers/portal_link_invalid.html')

    def test_a_temporary_password_by_phone_then_their_own(self):
        password = self.accounts('temporary_password').context['password']
        customer = self.visitor()
        response = customer.post('/customers/portal/login/', {'username': 'ali@newgym.example', 'password': password})
        self.assertEqual(response.status_code, 302)
        # Straight to choosing their own password and completing their details; nothing else opens first.
        self.assertIn('/customers/portal/first-login/', customer.get('/customers/portal/requests/', follow=True).redirect_chain[-1][0])
        customer.post('/customers/portal/first-login/', SETUP)
        self.assertEqual(customer.get('/customers/portal/requests/').status_code, 200)
        self.assertFalse(self.can_sign_in(password))
        self.assertTrue(self.can_sign_in('Strong-Pass-2026!'))

    def test_a_forgotten_password(self):
        self.accounts('temporary_password')
        Customer.objects.filter(pk=self.customer.pk).update(must_change_password=False)
        mail.outbox.clear()
        self.visitor().post('/customers/portal/password-help/', {'email': 'ali@newgym.example'})
        self.assertEqual(len(mail.outbox), 1)
        path = portal_link(mail.outbox[0])
        customer = self.visitor()
        customer.post(path, {'new_password1': 'Another-Pass-2026!', 'new_password2': 'Another-Pass-2026!'})
        self.assertTrue(self.can_sign_in('Another-Pass-2026!'))

    def test_an_unknown_address_on_forgotten_password_reveals_nothing(self):
        page = self.visitor().post('/customers/portal/password-help/', {'email': 'nobody@nowhere.example'})
        known = self.visitor().post('/customers/portal/password-help/', {'email': 'ali@newgym.example'})
        self.assertEqual(page.status_code, known.status_code)
        self.assertEqual(len([m for m in mail.outbox if 'nobody@nowhere.example' in m.to]), 0)

    def test_changing_the_sign_in_email(self):
        self.accounts('temporary_password')
        Customer.objects.filter(pk=self.customer.pk).update(must_change_password=False)
        user = Customer.objects.get(pk=self.customer.pk).user
        user.set_password('Strong-Pass-2026!')
        user.save()
        customer = self.visitor()
        customer.login(username='ali@newgym.example', password='Strong-Pass-2026!')
        mail.outbox.clear()
        customer.post('/customers/portal/settings/', {'action': 'email', 'email': 'owner@newgym.example',
                                                      'password': 'Strong-Pass-2026!'})
        confirmation = next(m for m in mail.outbox if 'owner@newgym.example' in m.to)
        # Nothing changes until the new address confirms it.
        self.assertTrue(self.can_sign_in('Strong-Pass-2026!'))
        self.visitor().get(portal_link(confirmation), follow=True)
        self.assertTrue(self.can_sign_in('Strong-Pass-2026!', username='owner@newgym.example'))
        self.assertFalse(self.can_sign_in('Strong-Pass-2026!'))
        self.assertTrue(any('ali@newgym.example' in m.to for m in mail.outbox))  # the old address is told

    def test_a_wrong_password_never_changes_the_email(self):
        self.accounts('temporary_password')
        Customer.objects.filter(pk=self.customer.pk).update(must_change_password=False)
        user = Customer.objects.get(pk=self.customer.pk).user
        user.set_password('Strong-Pass-2026!')
        user.save()
        customer = self.visitor()
        customer.login(username='ali@newgym.example', password='Strong-Pass-2026!')
        mail.outbox.clear()
        customer.post('/customers/portal/settings/', {'action': 'email', 'email': 'thief@evil.example', 'password': 'guess'})
        self.assertFalse(any('thief@evil.example' in m.to for m in mail.outbox))

    def test_a_disabled_account_cannot_sign_in(self):
        password = self.accounts('temporary_password').context['password']
        self.accounts('disable')
        self.assertFalse(self.can_sign_in(password))
