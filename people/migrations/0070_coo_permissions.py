"""The COO starts with the Operations Manager's switches (every country, like
him; above him on Staff accounts — people.permissions.can_manage_account), and
is emailed whatever he is. Changeable later on Roles & permissions."""
from django.db import migrations


def copy_operations_manager(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for row in RolePermission.objects.filter(role='operations_manager'):
        RolePermission.objects.update_or_create(role='coo', permission=row.permission, defaults={'allowed': row.allowed})
    NotificationSettings = apps.get_model('people', 'NotificationSettings')
    for settings in NotificationSettings.objects.all():
        for field in ('new_ticket_roles', 'new_task_roles'):
            roles = getattr(settings, field)
            if 'operations_manager' in roles and 'coo' not in roles:
                setattr(settings, field, roles + ['coo'])
        settings.save()


def remove(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(role='coo').delete()


class Migration(migrations.Migration):
    dependencies = [('people', '0069_coo_role')]
    operations = [migrations.RunPython(copy_operations_manager, remove)]
