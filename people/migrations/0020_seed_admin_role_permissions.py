from django.db import migrations

# Admin is a superset of manager — every configurable permission starts
# allowed, same as every fixed-floor screen (skills, brands, countries, ...)
# already reachable via require_manager's manager-tier check. Roles &
# permissions itself stays admin-only at the code level (require_admin),
# not through this table.
PERMISSIONS = [
    'view_dashboard', 'view_tasks', 'create_tasks', 'assign_tasks',
    'view_technicians', 'review_skills', 'manage_tickets',
    'manage_technicians', 'manage_customers',
]


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.bulk_create([
        RolePermission(role='admin', permission=permission, allowed=True)
        for permission in PERMISSIONS
    ])


def unseed(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(role='admin').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0019_alter_rolepermission_role_alter_technician_role'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
