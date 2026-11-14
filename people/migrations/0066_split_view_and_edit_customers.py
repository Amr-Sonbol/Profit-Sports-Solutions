from django.db import migrations


def split(apps, schema_editor):
    """Seeing customers apart from editing them: whoever had the old switch
    ("View and edit customers and sites") keeps seeing them; a supervisor
    stops editing them (customers, branches, machines)."""
    RolePermission = apps.get_model('people', 'RolePermission')
    # Every role (built-in, and the Admin's own) gets a row: yes where it had the old switch.
    allowed = set(RolePermission.objects.filter(permission='manage_customers', allowed=True).values_list('role', flat=True))
    for role in set(RolePermission.objects.values_list('role', flat=True)):
        RolePermission.objects.update_or_create(role=role, permission='view_customers', defaults={'allowed': role in allowed})
    RolePermission.objects.filter(role='supervisor', permission='manage_customers').update(allowed=False)


def unsplit(apps, schema_editor):
    RolePermission = apps.get_model('people', 'RolePermission')
    RolePermission.objects.filter(role='supervisor', permission='manage_customers').update(allowed=True)
    RolePermission.objects.filter(permission='view_customers').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0065_view_customers_choice'),
    ]

    operations = [
        migrations.RunPython(split, unsplit),
    ]
