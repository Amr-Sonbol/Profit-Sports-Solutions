"""Before one serial per brand is kept by the database (0036): any machines that
already share a brand and serial (test data, or typed twice) keep it, the second
and later ones marked "(duplicate <id>)" — so nothing is lost and they can be
found and put right."""
from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Upper


def mark_duplicates(apps, schema_editor):
    Asset = apps.get_model('customers', 'Asset')
    groups = (Asset.objects.exclude(serial_no='').annotate(serial=Upper('serial_no'))
              .values('brand_id', 'serial').annotate(n=Count('id')).filter(n__gt=1))
    for group in groups:
        machines = Asset.objects.filter(brand_id=group['brand_id'], serial_no__iexact=group['serial']).order_by('pk')
        for machine in list(machines)[1:]:
            machine.serial_no = f'{machine.serial_no} (duplicate {machine.pk})'[:100]
            machine.save(update_fields=['serial_no'])


class Migration(migrations.Migration):
    dependencies = [('customers', '0034_search_folded_and_phone_digits')]
    operations = [migrations.RunPython(mark_duplicates, migrations.RunPython.noop)]
