from django.db import migrations

# The dashboard is now everyone's landing page after signing in
# (spots.views.home), so every role needs to actually be able to see it —
# a technician and the new support_manager role didn't have this yet.
PERMISSION = 'view_dashboard'
ROLES = ['technician', 'support_manager']


def grant(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for role in ROLES:
        RolePermission.objects.update_or_create(
            role=role, permission=PERMISSION, defaults={'allowed': True},
        )


def revoke(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.filter(role__in=ROLES, permission=PERMISSION).update(allowed=False)


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0023_seed_technical_support_manager'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
