import secrets

from django.db import migrations, models


def backfill_tokens(apps, schema_editor):
    CustomerTicket = apps.get_model('tasks', 'CustomerTicket')
    for ticket in CustomerTicket.objects.filter(token=''):
        ticket.token = secrets.token_urlsafe(32)
        ticket.save(update_fields=['token'])


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0012_customerticket_pak_reference_number_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerticket',
            name='token',
            field=models.CharField(default='', editable=False, max_length=43, verbose_name='token'),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='customerticket',
            name='token',
            field=models.CharField(
                editable=False, max_length=43, unique=True, verbose_name='token',
                help_text=(
                    'lets the customer check this ticket’s status without an account — '
                    'see reports.CustomerFeedback'
                ),
            ),
        ),
    ]
