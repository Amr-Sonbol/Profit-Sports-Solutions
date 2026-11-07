"""The Operations Manager and the Technical Support Manager see everything an
admin does (but Roles & permissions, Notifications and the Django admin): every
permission an admin has. The Country Support Manager can follow what becomes of
their requests: the tasks, and the customers."""
from django.db import migrations

FULL = ['operations_manager', 'support_manager']
COUNTRY_SUPPORT_ADDS = ['view_tasks', 'manage_customers']


def forwards(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    admin_permissions = list(RolePermission.objects.filter(role='admin', allowed=True).values_list('permission', flat=True))
    grants = [(role, permission) for role in FULL for permission in admin_permissions]
    grants += [('country_support', permission) for permission in COUNTRY_SUPPORT_ADDS]
    for role, permission in grants:
        RolePermission.objects.update_or_create(role=role, permission=permission, defaults={'allowed': True})


def backwards(apps, schema_editor):
    pass  # what each role had before isn't recorded; an admin sets it back on Roles & permissions


class Migration(migrations.Migration):
    dependencies = [('people', '0056_new_ticket_roles_support_chain')]
    operations = [migrations.RunPython(forwards, backwards)]
