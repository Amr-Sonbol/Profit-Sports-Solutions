"""Warehouse stock (docs: Warehouse stock, under task_product) — counted from
the tasks themselves, never stored, so it can't drift from them:

    machines, per warehouse:  received − loaded + counts
    spare parts, per country: received − used on reports − shipped + counts

plus hand counts (StockAdjustment). Codes are compared ignoring case and
spaces. Colours aren't separate stock, and there are no shelves.
"""
from collections import defaultdict

from django.db.models import Q

from reports.models import PartUsed
from reference.models import Part, TaskType

from .models import StockAdjustment, Task, TaskPartNeeded, TaskProduct

# A task that's still going holds the parts ordered for it; one that's
# closed or cancelled lets what it didn't use go free.
FINISHED_STATUSES = (Task.Status.CLOSED, Task.Status.CANCELLED)


def stock_key(code):
    """BENCH-01, bench-01 and "BENCH- 01" are the same code."""
    return ''.join(code.split()).upper()


def _add(totals, rows, sign=1):
    for code, quantity in rows:
        totals[stock_key(code)] += sign * quantity


def machine_stock(site):
    """{code: quantity} of machines at one warehouse. Out is every machine
    on a loading task there — planned is enough, that's the reservation —
    and any carried straight from a receiving there to an installation,
    which the app allowed before loading was planned from stock."""
    lines = TaskProduct.objects.filter(kind=TaskProduct.Kind.MACHINE).exclude(task__status=Task.Status.CANCELLED)
    totals = defaultdict(int)
    _add(totals, lines.filter(
        task__site=site, task__task_type__category=TaskType.Category.RECEIVING, status=TaskProduct.Status.DONE,
    ).values_list('product_code', 'quantity'))
    _add(totals, lines.filter(
        Q(task__site=site, task__task_type__category=TaskType.Category.LOADING)
        | Q(task__task_type__category=TaskType.Category.INSTALLATION, source_line__task__site=site,
            source_line__task__task_type__category=TaskType.Category.RECEIVING),
    ).values_list('product_code', 'quantity'), sign=-1)
    _add(totals, StockAdjustment.objects.filter(
        site=site, kind=TaskProduct.Kind.MACHINE,
    ).values_list('product_code', 'quantity'))
    return {code: quantity for code, quantity in sorted(totals.items()) if quantity}


def part_stock(country):
    """{code: quantity} of spare parts in one country's warehouse."""
    totals = defaultdict(int)
    _add(totals, TaskProduct.objects.filter(
        kind=TaskProduct.Kind.PART, status=TaskProduct.Status.DONE,
        task__task_type__category=TaskType.Category.RECEIVING, task__site__customer__country=country,
    ).exclude(task__status=Task.Status.CANCELLED).values_list('product_code', 'quantity'))
    _add(totals, PartUsed.objects.filter(
        report__task__site__customer__country=country,
    ).values_list('part_code', 'quantity'), sign=-1)
    # Sent to customers (tasks.shipments): out once picked and ready to ship.
    _add(totals, TaskProduct.objects.filter(
        kind=TaskProduct.Kind.PART, status=TaskProduct.Status.DONE,
        task__task_type__category=TaskType.Category.PART_SHIPMENT, task__site__customer__country=country,
    ).exclude(task__status=Task.Status.CANCELLED).values_list('product_code', 'quantity'), sign=-1)
    _add(totals, StockAdjustment.objects.filter(
        site__customer__country=country, kind=TaskProduct.Kind.PART,
    ).values_list('product_code', 'quantity'))
    return {code: quantity for code, quantity in sorted(totals.items()) if quantity}


def parts_held(country):
    """{code: [(task, quantity)]}: parts received for a repair that's still
    going, and parts taken from stock for one (TaskPartNeeded, from our stock),
    less what its report already used."""
    received = defaultdict(int)
    lines = TaskProduct.objects.filter(
        kind=TaskProduct.Kind.PART, status=TaskProduct.Status.DONE, for_task__isnull=False,
        task__task_type__category=TaskType.Category.RECEIVING, task__site__customer__country=country,
    ).exclude(task__status=Task.Status.CANCELLED).exclude(for_task__status__in=FINISHED_STATUSES)
    tasks = {}
    for line in lines.select_related('for_task'):
        tasks[line.for_task_id] = line.for_task
        received[(stock_key(line.product_code), line.for_task_id)] += line.quantity
    for line in TaskPartNeeded.objects.filter(
        source=TaskPartNeeded.Source.STOCK, task__site__customer__country=country,
    ).exclude(task__status__in=FINISHED_STATUSES).select_related('task'):
        tasks[line.task_id] = line.task
        received[(stock_key(line.product_code), line.task_id)] += line.quantity
    used = defaultdict(int)
    for task_id, code, quantity in PartUsed.objects.filter(report__task__in=tasks).values_list(
        'report__task', 'part_code', 'quantity',
    ):
        used[(stock_key(code), task_id)] += quantity
    held = defaultdict(list)
    for (code, task_id), quantity in sorted(received.items()):
        left = quantity - used[(code, task_id)]
        if left > 0:
            held[code].append((tasks[task_id], left))
    return dict(held)


def part_rows(country):
    """The Stock screen's parts: each code with its catalogue entry (photo,
    description), how many there are, how many are held and for which
    tasks, and how many are free."""
    stock, held = part_stock(country), parts_held(country)
    catalogue = {stock_key(part.code): part for part in Part.objects.all()}
    rows = []
    for code in sorted(set(stock) | set(held)):
        quantity = stock.get(code, 0)
        held_for = held.get(code, [])
        rows.append({
            'code': code, 'part': catalogue.get(code), 'quantity': quantity, 'held': held_for,
            'free': quantity - sum(left for _task, left in held_for),
        })
    return rows


def record_count(site, kind, code, counted, note, user):
    """A hand count: `counted` is what's on the shelf; stores the difference
    from what the app works out, if any. Returns the StockAdjustment, or
    None when the count matched."""
    key = stock_key(code)
    current = (machine_stock(site) if kind == TaskProduct.Kind.MACHINE else part_stock(site.customer.country)).get(key, 0)
    if counted == current:
        return None
    return StockAdjustment.objects.create(
        site=site, kind=kind, product_code=key, quantity=counted - current, note=note, created_by=user,
    )
