from django.db import migrations

# The Operations Manager decides tickets the support desk escalates to
# them — not a second ticket desk, so no manage_tickets: they open only the
# tickets escalated to them. Admin can decide escalations too, as the
# superset role. Every other role gets the new permission switched off.
ROLE = 'operations_manager'
ROLE_ALLOWED = {'view_dashboard', 'view_tasks', 'view_technicians', 'view_machines', 'decide_escalated_tickets'}
PERMISSION = 'decide_escalated_tickets'
OTHER_ROLES = ['technician', 'supervisor', 'manager', 'support_manager', 'warehouse_manager', 'admin']


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for role in OTHER_ROLES:
        RolePermission.objects.update_or_create(
            role=role, permission=PERMISSION, defaults={'allowed': role == 'admin'},
        )
    all_permissions = set(RolePermission.objects.values_list('permission', flat=True).distinct())
    for permission in all_permissions:
        RolePermission.objects.update_or_create(
            role=ROLE, permission=permission, defaults={'allowed': permission in ROLE_ALLOWED},
        )


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.filter(role=ROLE).delete()
    RolePermission.objects.filter(permission=PERMISSION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0033_alter_rolepermission_permission_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
