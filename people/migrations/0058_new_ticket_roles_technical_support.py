"""New tickets told to the Technical Support Manager (every country) and each
country's Country Support Manager, not the Operations Manager — where the
setting is still 0056's; one an admin chose by hand is left as it is."""
from django.db import migrations

BEFORE = {'operations_manager', 'country_support'}
AFTER = ['support_manager', 'country_support']


def forwards(apps, schema_editor):
    NotificationSettings = apps.get_model('people', 'NotificationSettings')
    for settings in NotificationSettings.objects.all():
        if set(settings.new_ticket_roles) == BEFORE:
            settings.new_ticket_roles = AFTER
            settings.save(update_fields=['new_ticket_roles'])


def backwards(apps, schema_editor):
    NotificationSettings = apps.get_model('people', 'NotificationSettings')
    for settings in NotificationSettings.objects.all():
        if set(settings.new_ticket_roles) == set(AFTER):
            settings.new_ticket_roles = sorted(BEFORE)
            settings.save(update_fields=['new_ticket_roles'])


class Migration(migrations.Migration):
    dependencies = [('people', '0057_ops_and_support_see_everything')]
    operations = [migrations.RunPython(forwards, backwards)]
