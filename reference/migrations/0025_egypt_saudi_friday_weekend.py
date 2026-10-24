from django.db import migrations

# In Egypt and Saudi Arabia only Friday is off: the work week runs
# Saturday to Thursday (Country.first_workday then starts the week on
# Saturday). Both had been set to Friday–Saturday.
FRIDAY_ONLY = '4'
COUNTRIES = ['EG', 'SA']


def friday_only(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Country.objects.filter(iso_code__in=COUNTRIES).update(weekend_days=FRIDAY_ONLY)


def friday_saturday(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Country.objects.filter(iso_code__in=COUNTRIES).update(weekend_days='4,5')


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0024_formal_field_wording_restored'),
    ]

    operations = [
        migrations.RunPython(friday_only, friday_saturday),
    ]
