"""New tickets told to the Operations Manager and each country's Country
Support Manager only (by email and in the bell) — where the setting is still
the old default; one an admin chose by hand is left as it is."""
from django.db import migrations

OLD = {'support_manager', 'country_support', 'operations_manager', 'admin'}
NEW = ['operations_manager', 'country_support']


def forwards(apps, schema_editor):
    NotificationSettings = apps.get_model('people', 'NotificationSettings')
    for settings in NotificationSettings.objects.all():
        if set(settings.new_ticket_roles) == OLD:
            settings.new_ticket_roles = NEW
            settings.save(update_fields=['new_ticket_roles'])


def backwards(apps, schema_editor):
    NotificationSettings = apps.get_model('people', 'NotificationSettings')
    for settings in NotificationSettings.objects.all():
        if set(settings.new_ticket_roles) == set(NEW):
            settings.new_ticket_roles = sorted(OLD)
            settings.save(update_fields=['new_ticket_roles'])


class Migration(migrations.Migration):
    dependencies = [('people', '0055_failed_email')]
    operations = [migrations.RunPython(forwards, backwards)]
