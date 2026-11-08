"""The "Spare part shipment" task type (tasks.shipments)."""
from django.db import migrations


def forwards(apps, schema_editor):
    TaskType = apps.get_model('reference', 'TaskType')
    TaskType.objects.get_or_create(code='part_shipment', defaults={
        'name': 'Spare Part Shipment', 'name_ar': 'شحن قطع غيار', 'category': 'part_shipment',
    })


def backwards(apps, schema_editor):
    apps.get_model('reference', 'TaskType').objects.filter(code='part_shipment', tasks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('reference', '0031_part_shipment_category')]
    operations = [migrations.RunPython(forwards, backwards)]
