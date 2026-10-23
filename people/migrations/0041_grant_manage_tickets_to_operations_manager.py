from django.db import migrations

# The Operations Manager is the Technical Support Manager's own manager,
# so they get the whole ticket desk too — not only the tickets escalated
# to them (0034's original scope).
ROLE = 'operations_manager'
PERMISSION = 'manage_tickets'


def grant(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.update_or_create(
        role=ROLE, permission=PERMISSION, defaults={'allowed': True},
    )


def revoke(apps, schema_editor):
    apps.get_model('people', 'RolePermission').objects.update_or_create(
        role=ROLE, permission=PERMISSION, defaults={'allowed': False},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0040_seed_open_tickets_permission'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
