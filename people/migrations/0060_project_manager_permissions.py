"""The Project Manager starts with the Manager's permissions (like a Manager,
in every country; no customer requests — docs/access_rules.md). Changeable
later on Roles & permissions."""
from django.db import migrations


def copy_manager_permissions(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for row in RolePermission.objects.filter(role='manager'):
        RolePermission.objects.update_or_create(
            role='project_manager', permission=row.permission, defaults={'allowed': row.allowed},
        )


def remove(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(role='project_manager').delete()


class Migration(migrations.Migration):
    dependencies = [('people', '0059_project_manager_role')]
    operations = [migrations.RunPython(copy_manager_permissions, remove)]
