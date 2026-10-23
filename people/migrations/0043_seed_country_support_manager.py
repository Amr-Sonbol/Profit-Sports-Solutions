from django.db import migrations

# Tickets in every country become their own permission, so a Country
# Support Manager can hold the ticket desk for one country only. The
# Technical Support Manager also starts deciding escalations: they're the
# middle of the chain (Country Support Manager -> Technical Support
# Manager -> Operations Manager). The new role starts with the Technical
# Support Manager's permissions, minus those two.
ALL_COUNTRIES = 'tickets_all_countries'
DECIDE = 'decide_escalated_tickets'
ALL_COUNTRIES_ROLES = ['support_manager', 'operations_manager', 'admin']
ALL_ROLES = [
    'technician', 'supervisor', 'manager', 'support_manager', 'country_support',
    'warehouse_manager', 'operations_manager', 'admin',
]
NEW_ROLE = 'country_support'


def seed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    for role in ALL_ROLES:
        RolePermission.objects.update_or_create(
            role=role, permission=ALL_COUNTRIES, defaults={'allowed': role in ALL_COUNTRIES_ROLES},
        )
    RolePermission.objects.update_or_create(role='support_manager', permission=DECIDE, defaults={'allowed': True})
    for row in RolePermission.objects.filter(role='support_manager'):
        RolePermission.objects.update_or_create(
            role=NEW_ROLE, permission=row.permission,
            defaults={'allowed': row.allowed and row.permission not in (ALL_COUNTRIES, DECIDE)},
        )


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.filter(permission=ALL_COUNTRIES).delete()
    RolePermission.objects.filter(role=NEW_ROLE).delete()
    RolePermission.objects.filter(role='support_manager', permission=DECIDE).update(allowed=False)


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0042_country_support_manager_role'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
