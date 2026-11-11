from django.db import migrations

# Visit reports are in Arabic for every customer in Saudi Arabia and
# Egypt (reports.pdf.report_language), whatever language each customer
# set for themselves.
COUNTRIES = ['SA', 'EG']


def arabic(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Country.objects.filter(iso_code__in=COUNTRIES).update(report_language='ar')


def own_language(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Country.objects.filter(iso_code__in=COUNTRIES).update(report_language='')


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0026_country_report_language'),
    ]

    operations = [
        migrations.RunPython(arabic, own_language),
    ]
