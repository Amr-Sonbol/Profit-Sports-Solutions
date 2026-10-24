from django.db import migrations

# The company dashboard (every open task, everyone's availability, the
# company charts) is for managers and above only. Technicians and
# supervisors have their own pages: My week and My progress. An admin can
# still grant it under Roles.
PERMISSION = 'view_dashboard'
ROLES = ['technician', 'supervisor']


def revoke(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.filter(role__in=ROLES, permission=PERMISSION).update(allowed=False)


def grant(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for role in ROLES:
        RolePermission.objects.update_or_create(role=role, permission=PERMISSION, defaults={'allowed': True})


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0049_formal_field_wording_restored'),
    ]

    operations = [
        migrations.RunPython(revoke, grant),
    ]
