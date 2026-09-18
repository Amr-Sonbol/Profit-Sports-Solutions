from django.db import migrations

# Mirrors the exact rows the original 0006 seed migration created for this
# permission — restorable if this ever needs to be reverted.
ROLES_ALLOWED_BY_DEFAULT = ['supervisor', 'manager']
ALL_ROLES = ['technician', 'supervisor', 'manager']
PERMISSION = 'review_reports'


def unseed(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.filter(permission=PERMISSION).delete()


def reseed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    rows = [
        RolePermission(role=role, permission=PERMISSION, allowed=role in ROLES_ALLOWED_BY_DEFAULT)
        for role in ALL_ROLES
    ]
    RolePermission.objects.bulk_create(rows)


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0014_alter_rolepermission_permission'),
    ]

    operations = [
        migrations.RunPython(unseed, reseed),
    ]
