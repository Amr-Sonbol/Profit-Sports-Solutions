from django.db import migrations

# Panatta and Skillcore each sell both a cardio line and everything else —
# split into two skill rows so a technician's rating on one doesn't hide the
# other. Every other brand stays a single, "other"-category skill.
CARDIO_BRANDS = ['Panatta', 'Skillcore']


def add_cardio_lines(apps, schema_editor):
    Brand = apps.get_model('reference', 'Brand')
    Skill = apps.get_model('reference', 'Skill')

    for brand_name in CARDIO_BRANDS:
        brand, _created = Brand.objects.get_or_create(name=brand_name)
        # The brand's original line — same as every other seeded brand.
        Skill.objects.get_or_create(brand=brand, name=brand.name, defaults={'category': 'other'})
        Skill.objects.get_or_create(brand=brand, name='Cardio', defaults={'category': 'cardio'})


def remove_cardio_lines(apps, schema_editor):
    apps.get_model('reference', 'Skill').objects.filter(
        brand__name__in=CARDIO_BRANDS, name='Cardio', category='cardio',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0004_skill_category'),
    ]

    operations = [
        migrations.RunPython(add_cardio_lines, remove_cardio_lines),
    ]
