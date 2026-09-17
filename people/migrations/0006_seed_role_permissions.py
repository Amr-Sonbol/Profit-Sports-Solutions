from django.db import migrations

# Every permission this app has today. Seeded to match exactly what the
# code used to hardcode (require_supervisor let in supervisor + manager,
# nothing else) — this migration changes nothing about who can do what;
# it just makes it editable from here on.
PERMISSIONS = [
    'view_dashboard', 'view_tasks', 'create_tasks', 'assign_tasks',
    'view_technicians', 'review_skills', 'review_reports',
]
ROLES_ALLOWED_BY_DEFAULT = ['supervisor', 'manager']
ALL_ROLES = ['technician', 'supervisor', 'manager']


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    rows = [
        RolePermission(role=role, permission=permission, allowed=role in ROLES_ALLOWED_BY_DEFAULT)
        for permission in PERMISSIONS
        for role in ALL_ROLES
    ]
    RolePermission.objects.bulk_create(rows)


def unseed(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(permission__in=PERMISSIONS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0005_rolepermission'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
