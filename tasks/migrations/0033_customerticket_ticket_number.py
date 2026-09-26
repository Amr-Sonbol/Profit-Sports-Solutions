from django.db import migrations, models


def backfill_ticket_numbers(apps, schema_editor):
    CustomerTicket = apps.get_model('tasks', 'CustomerTicket')
    Country = apps.get_model('reference', 'Country')
    for country in Country.objects.all():
        prefix = f'{country.task_prefix or country.iso_code}-T'
        tickets = CustomerTicket.objects.filter(country=country).order_by('submitted_at', 'id')
        for index, ticket in enumerate(tickets, start=1):
            ticket.ticket_number = f'{prefix}{index:04d}'
            ticket.save(update_fields=['ticket_number'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0032_alter_taskattachment_purpose'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerticket',
            name='ticket_number',
            field=models.CharField(
                blank=True, default='', max_length=30,
                help_text='auto-generated per country, e.g. AE-T0001 — the same prefix a task number uses, marked with a T so the two are never confused',
                verbose_name='ticket number',
            ),
        ),
        migrations.RunPython(backfill_ticket_numbers, noop_reverse),
        migrations.AlterField(
            model_name='customerticket',
            name='ticket_number',
            field=models.CharField(
                max_length=30, unique=True,
                help_text='auto-generated per country, e.g. AE-T0001 — the same prefix a task number uses, marked with a T so the two are never confused',
                verbose_name='ticket number',
            ),
        ),
    ]
