import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0044_alter_task_delivery_note_alter_task_factory_offer_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='taskevent',
            name='correction_reason',
            field=models.TextField(blank=True, verbose_name='correction reason'),
        ),
        migrations.AlterField(
            model_name='taskevent',
            name='corrected_at',
            field=models.DateTimeField(
                blank=True, help_text="a manager's or admin's correction", null=True, verbose_name='corrected at',
            ),
        ),
        migrations.AlterField(
            model_name='taskevent',
            name='corrected_by',
            field=models.ForeignKey(
                blank=True, help_text='managers and admins only', null=True,
                on_delete=django.db.models.deletion.PROTECT, related_name='task_event_corrections',
                to=settings.AUTH_USER_MODEL, verbose_name='corrected by',
            ),
        ),
    ]
