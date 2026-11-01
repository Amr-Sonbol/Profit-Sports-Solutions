from django.db import migrations

# Warehouse stock is for whoever runs or plans it: the warehouse manager,
# the operations manager, and the manager tier.
PERMISSION = 'view_stock'
ALLOWED_ROLES = ['manager', 'warehouse_manager', 'operations_manager', 'admin']
ALL_ROLES = [
    'technician', 'supervisor', 'manager', 'support_manager', 'country_support', 'warehouse_manager',
    'operations_manager', 'admin',
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
        ('people', '0053_view_stock_permission'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
