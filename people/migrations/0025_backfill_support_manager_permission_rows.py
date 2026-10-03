from django.db import migrations

# 0023 only created a row for support_manager on manage_tickets, and 0024
# only added one for view_dashboard — every other permission never got a
# row for this role at all. Functionally identical to allowed=False (both
# require_permission and the role_permissions screen already treat a
# missing row that way), but every role should have a row for every
# permission so the matrix is complete and easy to audit.
ROLE = 'support_manager'


def backfill(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    existing = set(RolePermission.objects.filter(role=ROLE).values_list('permission', flat=True))
    all_permissions = set(RolePermission.objects.values_list('permission', flat=True).distinct())
    for permission in all_permissions - existing:
        RolePermission.objects.create(role=ROLE, permission=permission, allowed=False)


def unbackfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0024_dashboard_is_the_default_landing_page'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
