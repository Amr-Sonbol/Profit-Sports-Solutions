from django.db import migrations

# Opening a ticket for a customer who phoned, messaged or emailed: the
# technical support manager and the operations manager, plus admin.
PERMISSION = 'open_tickets'
ALLOWED_ROLES = ['support_manager', 'operations_manager', 'admin']
ALL_ROLES = [
    'technician', 'supervisor', 'manager', 'support_manager', 'warehouse_manager', 'operations_manager', 'admin',
]


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for role in ALL_ROLES:
        RolePermission.objects.update_or_create(
            role=role, permission=PERMISSION, defaults={'allowed': role in ALLOWED_ROLES},
        )


def unseed(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(permission=PERMISSION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0039_alter_rolepermission_permission'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
