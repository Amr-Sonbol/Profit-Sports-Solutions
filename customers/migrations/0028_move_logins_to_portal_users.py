from django.db import migrations


def move_logins(apps, schema_editor):
    """Each customer's single login becomes its first portal user, with its
    invitation state; its name is the customer's contact, if it had none."""
    Customer = apps.get_model('customers', 'Customer')
    PortalUser = apps.get_model('customers', 'PortalUser')
    for customer in Customer.objects.filter(user__isnull=False).select_related('user'):
        PortalUser.objects.create(
            customer=customer, user=customer.user,
            must_change_password=customer.must_change_password, invited_at=customer.invited_at,
        )
        if not customer.user.first_name and customer.contact_name:
            customer.user.first_name = customer.contact_name[:150]
            customer.user.save(update_fields=['first_name'])


def back(apps, schema_editor):
    PortalUser = apps.get_model('customers', 'PortalUser')
    for person in PortalUser.objects.order_by('-pk').select_related('customer'):
        customer = person.customer
        customer.user_id = person.user_id
        customer.must_change_password = person.must_change_password
        customer.invited_at = person.invited_at
        customer.save(update_fields=['user', 'must_change_password', 'invited_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0027_portaluser'),
    ]

    operations = [
        migrations.RunPython(move_logins, back),
    ]
