from django.db import migrations


def seed(apps, schema_editor):
    """Every part code already used on a report, with the most recent
    description it was given — so the catalogue doesn't start empty.
    """
    Part = apps.get_model('reference', 'Part')
    PartUsed = apps.get_model('reports', 'PartUsed')
    descriptions = {}
    for code, description in PartUsed.objects.order_by('report__submitted_at').values_list(
        'part_code', 'description',
    ):
        code = code.strip().upper()
        if code:
            descriptions[code] = description or descriptions.get(code, '')
    for code, description in descriptions.items():
        Part.objects.get_or_create(code=code, defaults={'description': description[:200]})


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0020_part'),
        ('reports', '0003_remove_workreport_approved_at_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
