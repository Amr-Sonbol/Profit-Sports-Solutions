"""The equipment chain — receiving, loading, installation, follow-ups: planning
machines, the on-site checklist, stock beyond with a reason, sending machines
on, the site checked ready, machines joining the customer's list and what went
wrong followed up (docs/database_design_v2.md: task_product). Shared by the
website's pages and the app's API.
"""
from collections import defaultdict
from datetime import timedelta
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.forms import formset_factory
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from customers.models import Asset, Site
from people.models import (
    Technician,
)
from reference.models import TaskType
from .forms import (
    TaskProductCheckForm, TaskProductForm,
)
from .stock import machine_stock, stock_key
from .models import (
    CustomerTicket, Task, TaskAttachment, TaskEvent, TaskNotification,
    TaskProduct,
)


def save_planned_lines(task, rows):
    """The machine lines as planned on the Edit screen and in the app
    (TaskProductForm rows). A machine on the task is never removed: a row
    for an existing line (its line_id — or, from an app that doesn't send
    ids, the same code and serial) updates that line in place, keeping
    what was recorded on it on site (status, photo); a line with no row is
    simply kept; only new rows add lines. On a loading or an installation,
    "BENCH-01 × 4" becomes four lines — one per machine, each with its own
    serial and status to check off on site. Receiving keeps code ×
    quantity (counted against the container's list), and only there can a
    line be a spare part."""
    step = equipment_step(task)
    unused = {line.pk: line for line in task.products.all()}

    def existing_for(cleaned):
        if cleaned.get('line_id') in unused:
            return unused.pop(cleaned['line_id'])
        for pk, line in unused.items():
            if (line.product_code, line.serial_number) == (cleaned.get('product_code'), cleaned.get('serial_number', '')):
                return unused.pop(pk)
        return None

    for cleaned in rows:
        line = existing_for(cleaned) if cleaned.get('product_code') or cleaned.get('line_id') else None
        if line is not None:
            # Corrected in place: its code, serial, colours — and on a receiving, its count.
            if cleaned.get('product_code'):
                line.product_code = cleaned['product_code']
                line.serial_number = '' if step == TaskType.Category.RECEIVING else cleaned.get('serial_number', '')
                for color in TaskProductForm.COLORS:
                    setattr(line, color, cleaned[color])
                line.brand = cleaned.get('brand') or line.brand or task.brand
                if step == TaskType.Category.RECEIVING:
                    line.quantity = cleaned['quantity']
                    line.kind = cleaned.get('kind') or line.kind
                    line.for_task = cleaned.get('for_task')
                line.save()
            continue
        if not cleaned.get('product_code'):
            continue
        fields = {
            'product_code': cleaned['product_code'], 'serial_number': cleaned.get('serial_number', ''),
            **{color: cleaned[color] for color in TaskProductForm.COLORS},
            'brand': cleaned.get('brand') or task.brand,
        }
        if step == TaskType.Category.RECEIVING:
            TaskProduct.objects.create(
                task=task, quantity=cleaned['quantity'], **{**fields, 'serial_number': ''},
                kind=cleaned.get('kind') or TaskProduct.Kind.MACHINE, for_task=cleaned.get('for_task'),
            )
        elif step:
            for _unit in range(cleaned['quantity']):
                TaskProduct.objects.create(task=task, quantity=1, **fields)
        else:
            TaskProduct.objects.create(task=task, quantity=cleaned['quantity'], **fields)


# Lines that weren't installed and still need to be: the follow-up's.
# Still to install at the customer's: missing, left for later, never checked — and damaged or wrong
# ones, which wait for their replacement (docs/scenarios.md D, C2).
STILL_TO_INSTALL = (
    TaskProduct.Status.MISSING, TaskProduct.Status.LATER, TaskProduct.Status.PENDING,
    TaskProduct.Status.DAMAGED, TaskProduct.Status.WRONG_ITEM,
)


# What can follow each step of the equipment chain. A receiving only adds
# to the warehouse's stock; a loading is planned from that stock (its Edit
# screen), so machines from two containers can go on one truck.
NEXT_STEPS = {
    TaskType.Category.LOADING: (TaskType.Category.INSTALLATION,),
}


def warehouse_stock_for_planning(task):
    """{code: quantity} a loading can take from its warehouse — the stock
    with this loading's own planned machines put back."""
    stock = defaultdict(int, machine_stock(task.site))
    # Put back what machine_stock took out for this loading (all but the lines left for later).
    for code, quantity in task.products.filter(kind=TaskProduct.Kind.MACHINE).exclude(
        status__in=[TaskProduct.Status.LATER, TaskProduct.Status.RETURNED],
    ).values_list('product_code', 'quantity'):
        stock[stock_key(code)] += quantity
    return {code: quantity for code, quantity in sorted(stock.items()) if quantity > 0}


class BeyondStock(Exception):
    """A loading's plan goes beyond its warehouse's stock and no reason was given."""


def check_beyond_stock(task, reason, user):
    """After a loading's plan is saved (inside its transaction): beyond the
    warehouse's stock it stands only with the manager's reason, kept in the
    task's history; without one, BeyondStock undoes the save. Returns the
    warning to show ('' within stock)."""
    short = loading_shortfall(task)
    if not short:
        return ''
    if not (reason or '').strip():
        raise BeyondStock(short)
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.BEYOND_STOCK, occurred_at=timezone.now(), actor=user,
        note=f'{reason.strip()} — {short}',
    )
    return short


def loading_shortfall(task):
    """A warning when a loading plans more of a code than its warehouse
    holds — not a block: the warehouse floor is the truth, and a count
    corrects the figure."""
    short = {code: -quantity for code, quantity in machine_stock(task.site).items() if quantity < 0}
    if not short:
        return ''
    return _('More planned than %(warehouse)s holds: %(codes)s. Check the warehouse, or record a count on the Stock '
             'screen.') % {
        'warehouse': task.site.name, 'codes': ', '.join(f'{code} ({quantity} short)' for code, quantity in short.items()),
    }


def lines_to_carry(task):
    """A loading's machines that are loaded and not carried to an
    installation yet."""
    if equipment_step(task) not in NEXT_STEPS:
        return []
    from django.db.models import Exists, OuterRef
    # Carried on to a next step that's still going: not again. To one that was cancelled: free again.
    carried = TaskProduct.objects.filter(source_line=OuterRef('pk')).exclude(task__status=Task.Status.CANCELLED)
    return list(task.products.filter(
        status=TaskProduct.Status.DONE, kind=TaskProduct.Kind.MACHINE,
    ).exclude(Exists(carried)).order_by('pk'))


def next_step_sites(viewer, step):
    """Where a next step can be: a warehouse (our own sites) for loading, a
    customer's site for an installation — in every country for the manager
    tier (the chain crosses borders; only they send machines on)."""
    sites = Site.objects.filter(customer__is_active=True).select_related('customer__country')
    sites = sites.filter(customer__is_internal=(step == TaskType.Category.LOADING))
    if not viewer.reaches_all_countries:
        sites = sites.filter(customer__country=viewer.country)
    return sites.order_by('customer__country__name', 'customer__name', 'name')


def next_step_customers(viewer):
    """The customers a new branch can be added to while sending machines on: active,
    not our own, in the countries this person reaches."""
    from customers.models import Customer
    customers = Customer.objects.filter(is_active=True, is_internal=False).select_related('country')
    if not viewer.reaches_all_countries:
        customers = customers.filter(country=viewer.country)
    return customers.order_by('country__name', 'name')


def new_branch_for_next_step(request, viewer, data):
    """(the new branch, an error, or (None, '') when none was asked for): "+ New branch"
    while sending machines on to an installation — for whoever creates branches
    (people.permissions.can_create_customers). The customer is told."""
    from people.permissions import can_create_customers

    name = (data.get('new_branch_name') or '').strip()
    if not name:
        return None, ''
    if not can_create_customers(viewer):
        raise PermissionDenied
    customer = next_step_customers(viewer).filter(pk=data.get('new_branch_customer') or 0).first()
    if customer is None:
        return None, _('Choose the customer for the new branch.')
    address = (data.get('new_branch_address') or '').strip()
    if not address:
        return None, _('Please enter the new branch\'s address.')
    if customer.sites.filter(name__iexact=name).exists():
        return None, _('%(customer)s already has a branch called %(name)s: choose it from the list.') % {
            'customer': customer.name, 'name': name,
        }
    site = Site.objects.create(
        customer=customer, name=name[:150], address=address,
        contact_name=(data.get('new_branch_contact') or '').strip()[:150],
        contact_phone=(data.get('new_branch_phone') or '').strip()[:30],
    )
    tell_customer_new_branch(request, site)
    return site, ''


def tell_customer_new_branch(request, site):
    """A branch our staff set up (not one the customer asked for): a short email, in
    English and Arabic — they can follow it in the portal (owner, 13 Nov 2026)."""
    from tasks import customer_emails as emails
    customer = site.customer
    emails.send(
        country=customer.country, recipient=customer.contact_email, contact=customer.contact_name or customer.name,
        title=emails.NEW_BRANCH_TITLE, paragraphs=[emails.NEW_BRANCH_BODY, emails.NEW_BRANCH_NEXT],
        values={'site': site.name}, request=request,
        link=_portal_link(request), button=emails.PORTAL_BUTTON,
    )


def _portal_link(request):
    from django.urls import reverse

    from spots.hosts import portal_url
    return portal_url(request, reverse('customers:portal_home'))


def next_step_supervisors(viewer):
    """Who can own a next step: anyone who can own a task (an active
    supervisor or manager, never a technician — TaskForm's pool), in the
    countries this viewer reaches."""
    people = Technician.objects.filter(is_active=True).exclude(role=Technician.Role.TECHNICIAN)
    if not viewer.reaches_all_countries:
        people = people.filter(country=viewer.country)
    return people.select_related('country').order_by('country__name', 'full_name')


def next_step_supervisor(viewer, task, site, value):
    """(the next step's responsible supervisor, an error message or ''): the
    one chosen — someone in the site's country — or, left as it is, this
    task's own when it stays in the same country. Going to another country
    with no choice is an error: every task has an owner from the start
    (docs/access_rules.md), and this task's supervisor doesn't work there."""
    country = site.customer.country
    if value:
        supervisor = next_step_supervisors(viewer).filter(pk=value, country=country).first()
        if supervisor is None:
            return None, _('Choose a supervisor in %(country)s.') % {'country': country.display_name}
        return supervisor, ''
    if country.pk == task.site.customer.country_id and task.responsible_supervisor_id:
        return task.responsible_supervisor, ''
    return None, _('Choose who supervises it in %(country)s.') % {'country': country.display_name}


def create_next_step(request, task, task_type, site, lines, supervisor):
    """The chain's next step (loading, or an installation) at `site` — any
    country — for exactly these machines: each carried over with its serial
    and colours (source_line), to check off again there; owned by
    `supervisor` (next_step_supervisor). Returns it."""
    next_task = Task(
        site=site, task_type=task_type, brand=task.brand, priority=task.priority, source=Task.Source.INTERNAL,
        billing_type=task.billing_type, is_warranty=task.is_warranty, reported_at=timezone.now(),
        created_by=request.user, status=Task.Status.NEW, previous_task=task, responsible_supervisor=supervisor,
        description=_('Next step after %(number)s: %(count)s machines.') % {
            'number': task.task_number, 'count': sum(line.quantity for line in lines),
        },
    )
    # One installation per branch: machines sent to a branch whose installation is still
    # open join it (its customer's request follows the one task), not a second one.
    if task_type.category == TaskType.Category.INSTALLATION:
        open_one = Task.objects.filter(site=site, task_type__category=TaskType.Category.INSTALLATION).exclude(
            status__in=[Task.Status.COMPLETED, Task.Status.CLOSED, Task.Status.CANCELLED,
                        Task.Status.PENDING_SUPERVISOR_REVIEW],
        ).order_by('pk').first()
        if open_one is not None:
            with transaction.atomic():
                for line in lines:
                    for _unit in range(line.quantity):
                        TaskProduct.objects.create(
                            task=open_one, product_code=line.product_code, serial_number=line.serial_number,
                            quantity=1, frame=line.frame, arm=line.arm, padding=line.padding, trim=line.trim,
                            source_line=line, brand=line.brand,
                        )
                TaskEvent.objects.create(
                    task=open_one, event_type=TaskEvent.EventType.CREATED, occurred_at=timezone.now(), actor=request.user,
                    note=_('%(count)s more machines from %(number)s.') % {
                        'count': sum(line.quantity for line in lines), 'number': task.task_number},
                )
            return open_one
    with transaction.atomic():
        _save_new_task(next_task)
        # A received line is code × quantity: one line per machine from here on.
        for line in lines:
            for _unit in range(line.quantity):
                TaskProduct.objects.create(
                    task=next_task, product_code=line.product_code, serial_number=line.serial_number, quantity=1,
                    frame=line.frame, arm=line.arm, padding=line.padding, trim=line.trim, source_line=line, brand=line.brand,
                )
        TaskEvent.objects.create(task=next_task, event_type=TaskEvent.EventType.CREATED, occurred_at=timezone.now(), actor=request.user)
        TaskNotification.objects.create(task=next_task, created_at=timezone.now())
    _send_new_task_email_to_staff(request, next_task)
    return next_task


def still_to_install(task):
    """An installation's machines not installed yet (missing, later, never
    checked, damaged or wrong — waiting for a replacement) once its report is in:
    what a follow-up is for. Each only once: a line already carried to a
    follow-up that's still going isn't again (a second follow-up takes the rest)."""
    from django.db.models import Exists, OuterRef
    if not (is_installation(task) and has_checklist(task)) or getattr(task, 'report', None) is None:
        return []
    carried = TaskProduct.objects.filter(source_line=OuterRef('pk')).exclude(task__status=Task.Status.CANCELLED)
    return list(task.products.filter(status__in=STILL_TO_INSTALL).exclude(Exists(carried)))


def create_follow_up(task, user, request=None):
    """A follow-up installation for exactly the machines still to install:
    same site, type, brand and supervisor, linked as the next step, each
    machine's line carried over (source_line) to check off again. Returns
    the new task."""
    lines = still_to_install(task)
    follow_up = Task(
        site=task.site, task_type=task.task_type, brand=task.brand, priority=task.priority,
        source=Task.Source.INTERNAL, billing_type=task.billing_type, is_warranty=task.is_warranty,
        responsible_supervisor=task.responsible_supervisor, reported_at=timezone.now(), created_by=user,
        status=Task.Status.NEW, previous_task=task,
        description=_('Follow-up of %(number)s: the machines still to install.') % {'number': task.task_number},
    )
    with transaction.atomic():
        _save_new_task(follow_up)
        for line in lines:
            TaskProduct.objects.create(
                task=follow_up, product_code=line.product_code, serial_number=line.serial_number, quantity=1,
                frame=line.frame, arm=line.arm, padding=line.padding, trim=line.trim, source_line=line, brand=line.brand,
            )
        TaskEvent.objects.create(task=follow_up, event_type=TaskEvent.EventType.CREATED, occurred_at=timezone.now(), actor=user)
        TaskNotification.objects.create(task=follow_up, created_at=timezone.now())
    if request is not None:
        _send_new_task_email_to_staff(request, follow_up)  # like a next step: the staff are told
    return follow_up


DISPATCH_PHOTO_MISSING = _('Add the dispatch photo: the loaded truck, or the signed delivery note.')


def dispatch_photo_missing(task):
    """A loading leaves with proof of dispatch — a photo of the loaded truck or
    of the signed delivery note — before its report is filed."""
    return equipment_step(task) == TaskType.Category.LOADING and not task.attachments.filter(
        purpose=TaskAttachment.Purpose.DELIVERY_NOTE,
    ).exists()


def has_checklist(task):
    """An equipment task (receiving, loading, installation) with machine
    lines to check off."""
    return bool(equipment_step(task)) and task.products.exists()


def checklist_initial(task):
    """The checklist's rows, from the task's lines."""
    return [
        {
            'id': line.pk, 'product_code': line.product_code, 'kind': line.kind, 'quantity': line.quantity,
            'status': '' if line.status == TaskProduct.Status.PENDING else line.status,
            'serial_number': line.serial_number, 'frame': line.frame, 'arm': line.arm, 'padding': line.padding,
            'trim': line.trim, 'replacement': line.replacement, 'note': line.note,
            'photo_url': line.photo.url if line.photo else '', 'added_on_site': line.added_on_site,
            'serial_unreachable': line.serial_unreachable, 'tested_working': line.tested_working,
            # Installation: its serial wasn't taken at loading (packed) — read it from the sticker now.
            'serial_not_taken_at_loading': bool(
                line.source_line_id and line.source_line.serial_unreachable and not line.serial_number
            ),
        }
        for line in task.products.select_related('source_line').order_by('pk')
    ]


# The on-site checklist: the planned machines plus room to add one found on site.
ChecklistFormSet = formset_factory(TaskProductCheckForm, extra=1)


def make_checklist_formset(task, *args, **kwargs):
    """The checklist formset for a task's step (its labels follow the step)."""
    return ChecklistFormSet(
        *args, initial=checklist_initial(task), prefix='checklist', form_kwargs={'step': equipment_step(task)}, **kwargs,
    )


def checklist_formset_rows(formset):
    """A valid checklist formset's rows to save — its planned lines, and any
    machine added on site (an untouched extra row is left out); None
    without a checklist."""
    if formset is None:
        return None
    return [
        form.cleaned_data for form in formset
        if form.cleaned_data.get('product_code') and (form.cleaned_data.get('id') or form.has_changed())
    ]


def save_checklist(task, rows):
    _save_checklist(task, rows)
    if task.task_type_id and task.task_type.category == TaskType.Category.RECEIVING:
        from tasks.parts_needed import check_received
        user = task.created_by
        transaction.on_commit(lambda: check_received(task, user))


def _save_checklist(task, rows):
    """What really happened to each machine (TaskProductCheckForm rows,
    with any new photo as `photo`): planned lines updated by `id`, machines
    added on site created (and any added earlier but now left out removed)
    — planned lines are never deleted here, only marked. On receiving, a
    count short of the list splits the line: those that arrived, and the
    rest as its own line, missing or damaged."""
    lines = {line.pk: line for line in task.products.all()}
    kept = set()
    for cleaned in rows:
        line = lines.get(cleaned.get('id'))
        if line is None:
            line = TaskProduct(
                task=task, added_on_site=True, quantity=cleaned.get('quantity') or 1,
                kind=cleaned.get('kind') or TaskProduct.Kind.MACHINE, brand=task.brand,
            )
        line.product_code = cleaned['product_code']
        line.status = cleaned['status']
        line.serial_number = cleaned['serial_number']
        if 'serial_unreachable' in cleaned:  # loading
            line.serial_unreachable = cleaned['serial_unreachable']
        if 'tested_working' in cleaned:  # installation
            line.tested_working = cleaned['tested_working']
        for color in TaskProductCheckForm.COLORS:
            setattr(line, color, cleaned.get(color, getattr(line, color)))
        line.replacement = cleaned.get('replacement', '')
        line.note = cleaned.get('note', '')
        if cleaned.get('photo'):
            line.photo = cleaned['photo']
        rest = None
        received = cleaned.get('received')
        if received is not None and received < line.quantity:
            rest = TaskProduct(
                task=task, kind=line.kind, for_task_id=line.for_task_id, product_code=line.product_code, brand=line.brand,
                quantity=line.quantity - received,
                status=cleaned.get('rest_status') or TaskProduct.Status.MISSING, note=line.note,
                **{color: getattr(line, color) for color in TaskProductCheckForm.COLORS},
            )
            if received == 0:
                line.status = rest.status
                rest = None
            else:
                line.quantity = received
        elif received is not None:
            line.quantity = received
        line.save()
        kept.add(line.pk)
        if rest is not None:
            rest.photo = line.photo.name or None
            rest.save()
    task.products.filter(added_on_site=True).exclude(pk__in=kept).delete()


SITE_NOT_READY = _("This installation is scheduled, but its site isn't confirmed ready yet (space, power, access, "
                   "the date): check it on the task's page.")


def site_is_ready(task):
    """An installation's site confirmed ready (SiteReadiness); True for any other task."""
    if not is_installation(task):
        return True
    readiness = getattr(task, 'site_readiness', None)
    return bool(readiness and readiness.is_ready)


def save_site_readiness(task, user, data):
    """The installation's site checks as ticked (`data`: each of SiteReadiness.CHECKS
    true or false, and a note), by `user` — the website's task page and the app."""
    from tasks.models import SiteReadiness

    def ticked(value):
        return str(value).lower() in ('true', 'on', '1')
    return SiteReadiness.objects.update_or_create(task=task, defaults={
        **{check: ticked(data.get(check, False)) for check in SiteReadiness.CHECKS},
        'note': str(data.get('note') or '').strip()[:2000], 'checked_by': user, 'checked_at': timezone.now(),
    })[0]


def site_readiness_for_app(task, technician):
    """An installation's site checks as the app shows them; None for any other task."""
    if not is_installation(task):
        return None
    from tasks.models import SiteReadiness
    readiness = getattr(task, 'site_readiness', None)
    return {
        **{check: bool(readiness and getattr(readiness, check)) for check in SiteReadiness.CHECKS},
        'note': readiness.note if readiness else '', 'is_ready': bool(readiness and readiness.is_ready),
        'checked_by': readiness.checked_by.get_full_name() or readiness.checked_by.get_username() if readiness else '',
        'checked_at': readiness.checked_at if readiness else None,
        'can_check': can_change_task(technician, task),
    }


def is_installation(task):
    """True for an installation task (its type's category)."""
    return equipment_step(task) == TaskType.Category.INSTALLATION


# The equipment chain's steps, in order (TaskType.Category).
EQUIPMENT_STEPS = (TaskType.Category.RECEIVING, TaskType.Category.LOADING, TaskType.Category.INSTALLATION)


def equipment_step(task):
    """'receiving', 'loading' or 'installation' for a step of the equipment
    chain, else ''."""
    category = task.task_type.category if task.task_type_id else ''
    return category if category in EQUIPMENT_STEPS else ''


def register_installed_machines(task):
    """An installation's machines join the customer's machine list at its
    site — each product line with a serial, under its brand (the line's, else
    the task's), so the customer can pick it in the portal and every visit
    builds its history. One serial per brand: one already on file is that
    machine — at this site nothing to add; at another, it isn't added a second
    time, and is returned as (line, the machine on file) for the manager to
    check (open_equipment_follow_up). Called each time the installation's report is
    saved — a correction corrects the machines it registered.
    """
    if not is_installation(task):
        return []
    today = timezone.localdate()
    elsewhere = []
    for product in task.products.filter(status=TaskProduct.Status.DONE).exclude(serial_number='').select_related('brand'):
        brand = product.brand or task.brand
        if brand is None:
            continue  # the review says so: no brand, nothing to add
        on_file = (Asset.objects.filter(brand=brand, serial_no__iexact=product.serial_number)
                   .select_related('site').first())
        mine = Asset.objects.filter(installed_from=product).first()
        if mine is not None:
            # Registered from this line before: a corrected report corrects it (unless the
            # corrected serial is another machine's — then that's followed up, below).
            if on_file is None or on_file.pk == mine.pk:
                changed = (mine.serial_no, mine.brand_id, mine.code) != (product.serial_number, brand.pk, product.product_code)
                if changed:
                    mine.serial_no, mine.brand, mine.code = product.serial_number, brand, product.product_code
                    mine.model_name = mine.model_name or product.product_code
                    mine.save()
                continue
        if on_file is None:
            Asset.objects.create(
                site=task.site, brand=brand, model_name=product.product_code, code=product.product_code,
                serial_no=product.serial_number, installed_on=today, installed_from=product,
            )
        elif on_file.site_id != task.site_id:
            elsewhere.append((product, on_file))
        elif on_file.installed_from_id is None:
            on_file.installed_from = product
            on_file.save(update_fields=['installed_from'])
    return elsewhere


# What, on an equipment step, needs following up with the factory or the carrier.
FOLLOW_UP_STATUSES = (TaskProduct.Status.MISSING, TaskProduct.Status.DAMAGED, TaskProduct.Status.WRONG_ITEM)


# How long a claim to the factory (or the carrier) has before it's overdue.
CLAIM_DAYS = 14


def open_equipment_follow_up(task, user, elsewhere=()):
    """A receiving's, loading's or installation's problems, followed up: the
    machines missing, damaged or wrong (a claim to the factory or the
    carrier), and a serial already on file at another site (`elsewhere`,
    register_installed_machines) — one internal task, to the country's
    Manager (else the task's own supervisor), in their language; their bell
    and phone are told. None when nothing went wrong."""
    problems = list(task.products.filter(status__in=FOLLOW_UP_STATUSES).order_by('pk'))
    if not problems and not elsewhere:
        return None
    country = task.site.customer.country
    manager = Technician.objects.filter(
        role=Technician.Role.MANAGER, country=country, is_active=True, user__isnull=False,
    ).order_by('pk').first() or task.responsible_supervisor
    if manager is None:
        return None
    with translation.override(manager.language or 'en'):
        lines = []
        for line in problems:
            what = f'{line.product_code}' + (f' × {line.quantity}' if line.quantity > 1 else '')
            what += f' ({line.serial_number})' if line.serial_number else ''
            lines.append(f'- {what}: {line.get_status_display()}' + (f' — {line.note}' if line.note else ''))
        for line, on_file in elsewhere:
            lines.append('- ' + _('%(code)s (%(serial)s): this serial is already on file at %(site)s — check the '
                                  'sticker; if the machine moved, move it from the machine\'s page.') % {
                'code': line.product_code, 'serial': line.serial_number, 'site': on_file.site.name,
            })
        step = TaskType.Category(equipment_step(task)).label
        ticket = CustomerTicket(
            kind=CustomerTicket.Kind.INTERNAL, country=country, assigned_to=manager,
            # Followed through: on this task, due in two weeks; missing, damaged or wrong goods are a
            # claim (to the factory or the carrier), closed only with its result.
            linked_task=task, due_on=timezone.localdate() + timedelta(days=CLAIM_DAYS), is_claim=bool(problems),
            waiting_for=CustomerTicket.WaitingFor.FACTORY if problems else '',
            subject=_('To follow up: %(step)s %(number)s') % {'step': step, 'number': task.task_number},
            description='\n'.join([
                _('Found when the report was filed for %(number)s at %(site)s:') % {
                    'number': task.task_number, 'site': task.site.name,
                },
                *lines,
            ]),
        )
    return open_internal_ticket(getattr(user, 'technician', None), user, ticket)


def open_quality_follow_up(task, user, why):
    """A visit that didn't settle it — a report "not resolved", or a customer's rating
    of 2 or less (docs/scenarios.md D, F): an internal task to the task's owner, on the
    task, due in a week. Once per task and reason."""
    owner = task.responsible_supervisor
    if owner is None or not owner.is_active:
        return None
    subject = why % {'number': task.task_number}
    if CustomerTicket.objects.filter(linked_task=task, subject=subject).exists():
        return None
    ticket = CustomerTicket(
        kind=CustomerTicket.Kind.INTERNAL, country=task.site.customer.country, assigned_to=owner,
        subject=subject, linked_task=task, due_on=timezone.localdate() + timedelta(days=7),
        description=str(_('%(site)s — what follows: another visit, parts, a call to the customer.')) % {
            'site': task.site.name},
    )
    return open_internal_ticket(getattr(user, 'technician', None), user, ticket)


# Defined with the pages in tasks.views; imported last, so either module can load first.
from .notices import _send_new_task_email_to_staff  # noqa: E402
from .views import _save_new_task, can_change_task, open_internal_ticket  # noqa: E402
