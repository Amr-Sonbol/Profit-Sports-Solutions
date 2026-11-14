from django.db import migrations


class Migration(migrations.Migration):
    """The single login and its invitation state now live on PortalUser."""

    dependencies = [
        ('customers', '0028_move_logins_to_portal_users'),
    ]

    operations = [
        migrations.RemoveField(model_name='customer', name='user'),
        migrations.RemoveField(model_name='customer', name='must_change_password'),
        migrations.RemoveField(model_name='customer', name='invited_at'),
    ]
