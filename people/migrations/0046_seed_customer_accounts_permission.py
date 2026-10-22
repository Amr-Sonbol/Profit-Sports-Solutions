from django.db import migrations

# Managing customer portal accounts (invite, unlock, password links,
# disable): Admin, the Technical Support Manager and the Operations
# Manager. Every other role gets the row switched off.
PERMISSION = 'customer_accounts'
ALLOWED_ROLES = ['support_manager', 'operations_manager', 'admin']
ALL_ROLES = [
    'technician', 'supervisor', 'manager', 'support_manager', 'country_support',
    'warehouse_manager', 'operations_manager', 'admin',
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
        ('people', '0045_customer_accounts_permission'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
