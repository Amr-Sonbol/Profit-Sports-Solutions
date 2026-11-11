import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Several people per customer, each with their own portal login."""

    dependencies = [
        ('customers', '0026_part_shipped_bell'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PortalUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('must_change_password', models.BooleanField(default=False, help_text='invited or given a temporary password: at the next sign-in they set their own before anything else', verbose_name='must change password')),
                ('invited_at', models.DateTimeField(blank=True, help_text='when the portal welcome email was last sent; its link is valid for 7 days', null=True, verbose_name='invited at')),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portal_users', to='customers.customer', verbose_name='customer')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='portal_user', to=settings.AUTH_USER_MODEL, verbose_name='login account')),
            ],
            options={'verbose_name': 'portal user', 'verbose_name_plural': 'portal users', 'ordering': ['pk']},
        ),
    ]
