from django.db import migrations

# The warehouse manager receives shipments and needs exactly two things:
# to find a task by its PAK reference number (task_list already supports
# this search, for anyone with view_tasks), and to post a task message
# saying a part arrived — open to anyone who can already view a task, no
# separate permission of its own. view_dashboard matches every other role,
# since the dashboard is the universal landing page after signing in.
ROLE = 'warehouse_manager'
ALLOWED_PERMISSIONS = {'view_dashboard', 'view_tasks'}


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    all_permissions = set(RolePermission.objects.values_list('permission', flat=True).distinct())
    for permission in all_permissions:
        RolePermission.objects.update_or_create(
            role=ROLE, permission=permission, defaults={'allowed': permission in ALLOWED_PERMISSIONS},
        )


def unseed(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(role=ROLE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0028_alter_rolepermission_role_alter_technician_role'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
