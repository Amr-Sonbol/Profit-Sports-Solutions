"""The spare parts a repair needs, known before the visit (TaskPartNeeded):
each from our stock (held for the task at once) or ordered (waiting until it
arrives — received for this task, or marked arrived). One place for "is this
task waiting for parts?", for the task page, the customer's portal and the
scheduling warning.
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from reference.models import Part, TaskType

from .models import Task, TaskEvent, TaskPartNeeded, TaskProduct
from .stock import stock_key


def _received_for(task):
    """{code: quantity} received at a warehouse for this repair (a receiving task's part lines, for_task)."""
    received = defaultdict(int)
    for code, quantity in TaskProduct.objects.filter(
        kind=TaskProduct.Kind.PART, status=TaskProduct.Status.DONE, for_task=task,
        task__task_type__category=TaskType.Category.RECEIVING,
    ).exclude(task__status=Task.Status.CANCELLED).values_list('product_code', 'quantity'):
        received[stock_key(code)] += quantity
    return received


def part_rows(task):
    """Each part needed, with its state: 'stock' (held from our stock),
    'waiting' (ordered, not in yet) or 'arrived'."""
    lines = list(task.parts_needed.all())
    received = _received_for(task) if any(line.source == TaskPartNeeded.Source.ORDERED for line in lines) else {}
    rows = []
    for line in lines:
        if line.source == TaskPartNeeded.Source.STOCK:
            state = 'stock'
        elif line.arrived_at or received.get(stock_key(line.product_code), 0) >= line.quantity:
            state = 'arrived'
        else:
            state = 'waiting'
        rows.append({'line': line, 'state': state})
    return rows


def waiting_for_parts(task):
    """(waiting, expected): whether an ordered part hasn't arrived yet, and the
    latest expected date among those (None when not known)."""
    waiting = [row['line'] for row in part_rows(task) if row['state'] == 'waiting']
    if not waiting:
        return False, None
    dates = [line.expected_on for line in waiting]
    return True, (max(dates) if all(dates) else None)


def add_part(task, user, cleaned):
    """A part needed (PartNeededForm): its description from the catalogue when left blank."""
    code = cleaned['product_code'].strip()
    description = (cleaned.get('description') or '').strip()
    if not description:
        part = Part.objects.filter(code__iexact=code).first()
        description = part.description if part else ''
    ordered = cleaned['source'] == TaskPartNeeded.Source.ORDERED
    line = TaskPartNeeded.objects.create(
        task=task, product_code=code, description=description, quantity=cleaned['quantity'],
        source=cleaned['source'], expected_on=cleaned.get('expected_on') if ordered else None, created_by=user,
    )
    changed(task)
    return line


def all_in(task):
    """Every part it needs is here: from our stock, or ordered and arrived."""
    rows = part_rows(task)
    return bool(rows) and all(row['state'] != 'waiting' for row in rows)


def can_prepare(technician):
    """Who gets a visit's parts ready: the warehouse manager, or the manager tier."""
    from people.models import Technician
    return technician.is_manager_tier or technician.role == Technician.Role.WAREHOUSE_MANAGER


def changed(task):
    """A part added or changed: whatever the warehouse prepared before doesn't cover it."""
    if task.parts_prepared_at:
        task.parts_prepared_at = None
        task.parts_prepared_by = None
        task.save(update_fields=['parts_prepared_at', 'parts_prepared_by'])


def check_all_in(task, user):
    """The last ordered part it needed is in: "All parts are in", once —
    again only if a part changed after it was told. The event tells the
    people on the task and the country's warehouse manager (tasks.updates)."""
    ordered = task.parts_needed.filter(source=TaskPartNeeded.Source.ORDERED)
    if not ordered.exists() or not all_in(task):
        return None
    latest_change = max(line.updated_at for line in task.parts_needed.all())
    if task.events.filter(event_type=TaskEvent.EventType.PARTS_ARRIVED, occurred_at__gte=latest_change).exists():
        return None
    return TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.PARTS_ARRIVED, occurred_at=timezone.now(), actor=user,
    )


def check_received(receiving_task, user):
    """A receiving task's parts were checked off: each repair they were ordered for may be all in now."""
    for task in Task.objects.filter(parts_held__task=receiving_task).distinct():
        check_all_in(task, user)


def edit_part(line, cleaned):
    """A manager corrects a part (PartNeededForm): it counts as changed."""
    line.product_code = cleaned['product_code'].strip()
    line.description = (cleaned.get('description') or '').strip() or line.description
    line.quantity = cleaned['quantity']
    line.source = cleaned['source']
    ordered = line.source == TaskPartNeeded.Source.ORDERED
    line.expected_on = cleaned.get('expected_on') if ordered else None
    if not ordered:
        line.arrived_at = None
    line.save()
    changed(line.task)


def mark_prepared(task, user):
    task.parts_prepared_at = timezone.now()
    task.parts_prepared_by = user
    task.save(update_fields=['parts_prepared_at', 'parts_prepared_by'])


def is_shipment(task):
    """A part shipment has its own "Preparing" step (tasks.shipments): no "Mark prepared" here."""
    return task.task_type_id is not None and task.task_type.category == TaskType.Category.PART_SHIPMENT


def to_prepare(country):
    """The repairs whose parts are all in and not prepared yet, in this
    country — the soonest visit first, those with no date last. Not part
    shipments: they're prepared on their own step."""
    tasks = Task.objects.filter(
        site__customer__country=country, parts_needed__isnull=False, parts_prepared_at__isnull=True,
    ).exclude(status__in=[Task.Status.CLOSED, Task.Status.CANCELLED]).exclude(
        task_type__category=TaskType.Category.PART_SHIPMENT,
    ).distinct().select_related('site__customer')
    ready = [task for task in tasks if all_in(task)]
    far = timezone.now() + timedelta(days=36500)
    return sorted(ready, key=lambda task: (task.scheduled_for or far, task.pk))


def report_parts(task):
    """The report's "Parts used" before it's first filed: the parts it needed,
    for the technician to correct to what was fitted."""
    return [
        {'part_code': line.product_code, 'description': line.description, 'quantity': line.quantity}
        for line in task.parts_needed.all()
    ]


def mark_arrived(line, user):
    """An ordered part that came in another way (delivered direct, say)."""
    line.arrived_at = timezone.now()
    line.save(update_fields=['arrived_at'])
    check_all_in(line.task, user)
