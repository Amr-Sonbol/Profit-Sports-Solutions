from django.db import migrations

# Tickets narrow further: not every manager, just the technical support
# manager (plus admin, unchanged). manager keeps every other permission
# it already had — only manage_tickets moves.
PERMISSION = 'manage_tickets'


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.update_or_create(
        role='manager', permission=PERMISSION, defaults={'allowed': False},
    )
    RolePermission.objects.update_or_create(
        role='support_manager', permission=PERMISSION, defaults={'allowed': True},
    )


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.update_or_create(
        role='manager', permission=PERMISSION, defaults={'allowed': True},
    )
    RolePermission.objects.filter(role='support_manager', permission=PERMISSION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0022_alter_rolepermission_role_alter_technician_role'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
