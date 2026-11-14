"""One account per role (and a customer's portal login), for walking through the
system as each person — docs/role_walkthrough.md. Development only.

    python manage.py walkthrough_accounts --country AE --customer "Fitness First" --out walkthrough_logins.txt
    python manage.py walkthrough_accounts --delete

Each password is generated and written to --out (never printed), so it stays on
this machine; running it again gives each account a new one.
"""
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from customers.models import Customer, PortalUser
from people.models import Technician
from reference.models import Country

PREFIX = 'walk-'
ROLES = [
    'admin', 'coo', 'operations_manager', 'project_manager', 'support_manager', 'country_support', 'manager',
    'supervisor', 'warehouse_manager', 'technician',
]


class Command(BaseCommand):
    help = 'Make (or --delete) one walkthrough account per role and a customer portal login. Development only.'

    def add_arguments(self, parser):
        parser.add_argument('--country', default='AE', help="the staff's country (ISO code)")
        parser.add_argument('--customer', default='', help="the customer whose portal login is made (a name)")
        parser.add_argument('--out', default='walkthrough_logins.txt', help='where the sign-ins are written')
        parser.add_argument('--delete', action='store_true')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Refusing to run: DEBUG is off, so this may be a live database.')
        User = get_user_model()
        if options['delete']:
            Technician.objects.filter(user__username__startswith=PREFIX).update(is_active=False)
            count = User.objects.filter(username__startswith=PREFIX).update(is_active=False)
            self.stdout.write(f'{count} walkthrough accounts switched off.')
            return
        country = Country.objects.filter(iso_code=options['country'].upper()).first()
        if country is None:
            raise CommandError(f"No country {options['country']}.")
        lines = ['Walkthrough sign-ins (development only; keep this file to yourself)', '']
        for role in ROLES:
            username = f'{PREFIX}{role.replace("_", "-")}'
            user, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@example.invalid'})
            password = secrets.token_urlsafe(9)
            user.set_password(password)
            user.is_active = True
            user.save()
            Technician.objects.update_or_create(user=user, defaults={
                'country': country, 'full_name': f'Walk {role.replace("_", " ").title()}', 'language': 'en',
                'role': role, 'employment_type': 'staff', 'is_active': True, 'must_change_password': False,
            })
            lines.append(f'{role:20} website and app   {username}   {password}')
        if options['customer']:
            customer = Customer.objects.filter(name__iexact=options['customer'], is_internal=False).first()
            if customer is None:
                raise CommandError(f"No customer named {options['customer']}.")
            username = f'{PREFIX}customer@example.invalid'
            user, _ = User.objects.get_or_create(username=username, defaults={'email': username})
            password = secrets.token_urlsafe(9)
            user.set_password(password)
            user.is_active = True
            user.save()
            PortalUser.objects.update_or_create(user=user, defaults={'customer': customer, 'must_change_password': False})
            lines.append(f'{"customer":20} portal            {username}   {password}   ({customer.name})')
        with open(options['out'], 'w', encoding='utf-8') as out:
            out.write('\n'.join(lines) + '\n')
        self.stdout.write(f"{len(lines) - 2} accounts ready; their sign-ins are in {options['out']}.")
