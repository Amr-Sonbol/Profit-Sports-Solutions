"""Part shipments: a spare part sent to the customer instead of a visit — a
task whose type is a part shipment (TaskType.Category.PART_SHIPMENT), made
from a machine's "Ship part" decision or by hand. Its own short way, with
none of a visit's steps (on the way, arrived, report, signature, review):

    Preparing      new        the warehouse manager picks the parts, adds the
                              PAK number and chooses the technician who ships it
    Ready to ship  assigned   that technician hands it to the courier
    Shipped        closed     courier, and tracking number when there is one
                              (local couriers often have none); the customer
                              is emailed, the request is settled

The parts come off the country's stock once ready (tasks.stock.part_stock).
A part that needs fitting gets a visit afterwards: "Book a fitting visit".
"""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from people.models import Technician
from reference.models import TaskType

from .models import Task, TaskAssignment, TaskEvent, TaskNotification, TaskProduct

PREPARING, READY, SHIPPED, CANCELLED = 'preparing', 'ready', 'shipped', 'cancelled'
STAGE_LABELS = {
    PREPARING: _('Preparing'), READY: _('Ready to ship'), SHIPPED: _('Shipped'), CANCELLED: _('Cancelled'),
}


def is_part_shipment(task):
    return task.task_type_id is not None and task.task_type.category == TaskType.Category.PART_SHIPMENT


def part_shipment_type():
    return TaskType.objects.filter(category=TaskType.Category.PART_SHIPMENT, is_active=True).first()


def stage(task):
    if task.status == Task.Status.CANCELLED:
        return CANCELLED
    if task.status in (Task.Status.CLOSED, Task.Status.COMPLETED):
        return SHIPPED
    return PREPARING if task.status == Task.Status.NEW else READY


def can_prepare(technician, task):
    """The country's warehouse manager, or the manager tier."""
    return technician.is_manager_tier or (
        technician.role == Technician.Role.WAREHOUSE_MANAGER and technician.country_id == task.site.customer.country_id
    )


def shipper(task):
    lead = task.assignments.filter(role=TaskAssignment.Role.LEAD, is_active=True).select_related('technician').first()
    return lead.technician if lead else None


def can_ship(technician, task):
    """The technician it was given to, or whoever can prepare it."""
    return can_prepare(technician, task) or shipper(task) == technician


def technicians_for(task):
    """Who can be given the parcel: active staff in the task's country who do field work."""
    return Technician.objects.filter(
        is_active=True, country=task.site.customer.country, user__isnull=False,
        role__in=[Technician.Role.TECHNICIAN, Technician.Role.SUPERVISOR, Technician.Role.WAREHOUSE_MANAGER],
    ).order_by('full_name')


def mark_ready(task, user, parts, pak_reference, technician):
    """Preparing -> ready to ship: the parts picked (they leave stock), the
    PAK number, and the technician who ships it."""
    from people.push import push_assigned

    now = timezone.now()
    with transaction.atomic():
        task.products.filter(kind=TaskProduct.Kind.PART).delete()  # prepared again: what's picked now counts
        for code, quantity in parts:
            TaskProduct.objects.create(
                task=task, kind=TaskProduct.Kind.PART, product_code=code, quantity=quantity,
                status=TaskProduct.Status.DONE,
            )
        task.pak_reference_number = pak_reference
        task.status = Task.Status.ASSIGNED
        task.save(update_fields=['pak_reference_number', 'status'])
        task.assignments.filter(role=TaskAssignment.Role.LEAD, is_active=True).update(is_active=False, ended_at=now)
        TaskAssignment.objects.create(task=task, technician=technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=now, is_active=True)
        TaskEvent.objects.create(task=task, event_type=TaskEvent.EventType.PARTS_READY, occurred_at=now, actor=user)
    push_assigned(technician, task, is_lead=True)


def mark_shipped(task, user, company, tracking_number):
    """Ready -> shipped: the courier, and the tracking number when there is
    one. Closes the task (which settles the customer's request)."""
    now = timezone.now()
    with transaction.atomic():
        task.shipping_company = company
        task.shipping_tracking_number = tracking_number
        task.status = Task.Status.CLOSED
        task.save(update_fields=['shipping_company', 'shipping_tracking_number', 'status'])
        TaskEvent.objects.create(task=task, event_type=TaskEvent.EventType.SHIPPED, occurred_at=now, actor=user)


def book_fitting_visit(task, user):
    """A visit to fit what was shipped: same site and machines' customer,
    linked as the next step. Returns the new task, to be scheduled."""
    from .views import _save_new_task

    visit = Task(
        site=task.site, task_type=TaskType.objects.filter(code='spare_parts_replacement').first(), brand=task.brand,
        priority=task.priority, source=Task.Source.INTERNAL, billing_type=task.billing_type,
        is_warranty=task.is_warranty, responsible_supervisor=task.responsible_supervisor,
        reported_at=timezone.now(), created_by=user, status=Task.Status.NEW, previous_task=task,
        description=str(_('Fit the parts shipped in %(number)s.') % {'number': task.task_number}),
    )
    with transaction.atomic():
        _save_new_task(visit)
        TaskEvent.objects.create(task=visit, event_type=TaskEvent.EventType.CREATED, occurred_at=timezone.now(), actor=user)
        TaskNotification.objects.create(task=visit, created_at=timezone.now())
    return visit
