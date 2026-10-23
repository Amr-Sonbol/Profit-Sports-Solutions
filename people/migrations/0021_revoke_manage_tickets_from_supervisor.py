from django.db import migrations

# Tickets are manager/admin-only now — a supervisor used to triage them
# day to day (0008_seed_manage_tickets_permission's default), but that's
# reversed: supervisors and technicians get no ticket access at all.
# Still just data on RolePermission, still editable from Roles &
# Permissions — this only changes where it starts.
ROLES = ['technician', 'supervisor']
PERMISSION = 'manage_tickets'


def revoke(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for role in ROLES:
        RolePermission.objects.update_or_create(
            role=role, permission=PERMISSION, defaults={'allowed': False},
        )


def restore_supervisor_default(apps, schema_editor):
    # Only supervisor was ever True by default (technician was already
    # False from 0008) — reversing just undoes that one flip.
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.update_or_create(
        role='supervisor', permission=PERMISSION, defaults={'allowed': True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0020_seed_admin_role_permissions'),
    ]

    operations = [
        migrations.RunPython(revoke, restore_supervisor_default),
    ]
