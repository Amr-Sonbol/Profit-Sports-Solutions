from django.db import migrations

ROLES_ALLOWED_BY_DEFAULT = ['supervisor', 'manager']
ALL_ROLES = ['technician', 'supervisor', 'manager']
PERMISSION = 'manage_customers'


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    rows = [
        RolePermission(role=role, permission=PERMISSION, allowed=role in ROLES_ALLOWED_BY_DEFAULT)
        for role in ALL_ROLES
    ]
    RolePermission.objects.bulk_create(rows)


def unseed(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(permission=PERMISSION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0012_alter_rolepermission_permission'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
