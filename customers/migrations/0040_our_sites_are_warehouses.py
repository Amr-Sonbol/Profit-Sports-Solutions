"""Our own sites so far were all warehouses (a showroom is marked as one from here on)."""
from django.db import migrations


def warehouses(apps, schema_editor):
    apps.get_model('customers', 'Site').objects.filter(customer__is_internal=True, kind='').update(kind='warehouse')


class Migration(migrations.Migration):
    dependencies = [('customers', '0039_site_kind_asset_on_loan_until')]
    operations = [migrations.RunPython(warehouses, migrations.RunPython.noop)]
