from django.db import migrations

# Every "office" role can look up a machine's history; a plain technician
# only ever sees the machines on their own current task, from the report
# screen — not a separate roster of everything the company has ever serviced.
PERMISSION = 'view_machines'
ALLOWED_ROLES = ['supervisor', 'manager', 'support_manager', 'admin']
ALL_ROLES = ['technician', 'supervisor', 'manager', 'support_manager', 'admin']


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
        ('people', '0026_alter_rolepermission_permission_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
