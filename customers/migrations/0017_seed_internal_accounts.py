from django.db import migrations

NAME = 'Profit Sports Solutions'


def add_accounts(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Customer = apps.get_model('customers', 'Customer')
    for country in Country.objects.all():
        if not Customer.objects.filter(country=country, is_internal=True).exists():
            Customer.objects.create(country=country, name=NAME, segment='other', is_internal=True)


def remove_accounts(apps, schema_editor):
    apps.get_model('customers', 'Customer').objects.filter(is_internal=True, sites__isnull=True).delete()


class Migration(migrations.Migration):
    """The company's own account in each country, for its warehouses to be
    added as sites (docs: customer.is_internal)."""

    dependencies = [('customers', '0016_customer_is_internal')]
    operations = [migrations.RunPython(add_accounts, remove_accounts)]
