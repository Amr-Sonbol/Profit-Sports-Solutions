"""Writing a customer's portal bell (CustomerNotification) where each thing
happens: a report filed, our team's reply, a visit booked or moved, a
request resolved. Only
for a customer with a portal login — never our own account (the
warehouses) — and one item per thing: booking the same visit again
replaces the unseen item rather than stacking another.
"""
from django.utils import timezone

from .models import CustomerNotification


def _notify(customer, kind, task=None, ticket=None):
    if customer is None or customer.is_internal or not customer.user_id:
        return
    CustomerNotification.objects.filter(
        customer=customer, kind=kind, task=task, ticket=ticket, seen_at__isnull=True,
    ).delete()
    CustomerNotification.objects.create(customer=customer, kind=kind, task=task, ticket=ticket, created_at=timezone.now())


def report_ready(task):
    """A visit's report is in: download it, and rate the visit."""
    _notify(task.site.customer, CustomerNotification.Kind.REPORT_READY, task=task)


def visit_booked(task):
    """A visit now has a day (or a new one)."""
    if task.scheduled_for or task.scheduled_date:
        _notify(task.site.customer, CustomerNotification.Kind.VISIT_BOOKED, task=task)


def staff_replied(ticket):
    """Our team answered one of their requests."""
    _notify(ticket.customer, CustomerNotification.Kind.REPLY, ticket=ticket)


def part_shipped(ticket):
    """A spare part for one of their requests is on its way (tasks.shipments)."""
    _notify(ticket.customer, CustomerNotification.Kind.PART_SHIPPED, ticket=ticket)


def request_closed(ticket):
    """We resolved one of their requests, visit or not: our answer is on it."""
    _notify(ticket.customer, CustomerNotification.Kind.REQUEST_CLOSED, ticket=ticket)


def seen(customer, task=None, ticket=None):
    """The customer opened what these are about: clear them from the bell."""
    items = CustomerNotification.objects.filter(customer=customer, seen_at__isnull=True)
    items = items.filter(task=task) if task is not None else items.filter(ticket=ticket)
    items.update(seen_at=timezone.now())
