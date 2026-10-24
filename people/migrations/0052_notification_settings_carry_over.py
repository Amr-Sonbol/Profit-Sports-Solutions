from django.db import migrations

# The Notifications page (0051) starts from what the system did before:
# a new ticket's email goes to whichever roles handle tickets today
# (manage_tickets), and "visit rescheduled" is turned on.


def carry_over(apps, schema_editor):
    NotificationSettings = apps.get_model('people', 'NotificationSettings')
    RolePermission = apps.get_model('people', 'RolePermission')
    settings, _created = NotificationSettings.objects.get_or_create(pk=1)
    settings.auto_notify_on_reschedule = True
    ticket_roles = sorted(
        RolePermission.objects.filter(permission='manage_tickets', allowed=True).values_list('role', flat=True),
    )
    if ticket_roles:
        settings.new_ticket_roles = ticket_roles
    settings.save()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0051_notification_switches'),
    ]

    operations = [
        migrations.RunPython(carry_over, migrations.RunPython.noop),
    ]
