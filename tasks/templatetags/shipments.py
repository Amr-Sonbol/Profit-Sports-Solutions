from django import template

from tasks import shipments

register = template.Library()


@register.filter
def tracking_url(task):
    """The courier's tracking page for a part shipment's parcel (tasks.shipments.tracking_url); '' when none."""
    return shipments.tracking_url(task) if task else ''
