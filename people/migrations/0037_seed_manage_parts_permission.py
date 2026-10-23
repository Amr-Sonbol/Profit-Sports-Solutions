from django.db import migrations

# The parts catalogue belongs to whoever runs the stock: the warehouse
# manager, plus the manager tier.
PERMISSION = 'manage_parts'
ALLOWED_ROLES = ['manager', 'warehouse_manager', 'admin']
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
        ('people', '0036_alter_rolepermission_permission'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
