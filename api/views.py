from decimal import Decimal, InvalidOperation

from django.contrib.auth import SESSION_KEY, authenticate, login
from django.contrib.auth.models import update_last_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.utils import translation
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from customers.models import Asset, Customer
from people.models import PushDevice, RolePermission, Technician, TechnicianConduct, TechnicianSkill
from people.context_processors import nav_groups, nav_pages
from people.permissions import get_active_country, require_manager, scoped_or_404
from people.push import push_assigned, push_removed
from reference.models import Brand, ConductArea, Part, Skill
from reports.pdf import hours_text, report_language
from reports.phrases import Action, Finding, Recommendation, WorkLine
from reports.forms import PartUsedItemForm, WorkReportForm
from tasks.forms import (
    AddHelperForm, BlockTaskForm, ChangePasswordForm, ExistingAssetOutcomeForm, MarkUnavailableForm, MyProfileForm,
    NewAssetForm, PauseTaskForm, ReviewLevelForm, TaskCreateForm,
    RemoveAssignmentForm, SelfRateLevelForm, SelfRateSkillForm, SetLeadForm, TaskAttachmentUploadForm, TechnicianFirstLoginForm,
)
from tasks.location import record_tap
from tasks.models import CustomerTicket, Task, TaskAsset, TaskAssignment, TaskEvent
from tasks.views import (
    ASSIGNMENT_LOCKED_STATUSES, BLOCKABLE_STATUSES, assignment_changeable, OPEN_STATUSES, REPORT_EDITABLE_STATUSES, TECHNICIAN_ACTIONS,
    _assignment_candidates, _candidates_with_skill_level, _next_technician_action, _require_task_owner, _set_lead,
    _active_helpers, _post_task_message, _requires_signature, _save_attachment, _task_is_paused,
    _technicians_with_next_scheduled_task,
    _with_lead_prefetch, approve_report_as_manager, approve_report_as_supervisor, can_supervisor_approve,
    _calendar_context, _my_tasks, _overdue, _people_i_oversee, report_preview, _resolve_report_month, progress_context, report_machine_initial, report_saved_message, save_task_machines,
    save_work_report, search_tasks, search_tickets, team_progress_rows, today_plan,
    save_conduct_review, save_conduct_self_rating, save_skill_review, save_skill_self_rating,
    send_password_changed_email, set_availability, skills_summary, _reliability_stats, create_task, _weekend_warning,
    _overseen_rows, _shift_month, _technician_hours_rows, _visible_internal_tickets, team_map_data,
    undo_last_tap,
    undoable_tap, SEARCH_RESULT_LIMIT,
)

from .permissions import IsTechnician, has_role_permission, require_role_permission
from .serializers import (
    CustomerTicketSerializer, TaskDetailSerializer, TaskListSerializer, TeamTaskDetailSerializer, WorkReportSerializer,
    directions_url,
)


class LoginView(APIView):
    """Trades a username/password for an API token, same accounts as the
    web login — no separate mobile identity, one User either way.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username', '')
        password = request.data.get('password', '')
        # axes.middleware.AxesMiddleware flags a lockout on the underlying
        # HttpRequest it sees in process_response — DRF's Request wrapper
        # doesn't forward attribute writes to it, so authenticate() must
        # get the raw request or a lockout here would silently not count.
        user = authenticate(request._request, username=username, password=password)
        if user is None:
            return Response({'detail': 'Invalid username or password.'}, status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            return Response({'detail': 'This account is inactive.'}, status=status.HTTP_400_BAD_REQUEST)

        token, _created = Token.objects.get_or_create(user=user)
        # So Staff accounts' "Last sign-in" counts the app, not only the website.
        update_last_login(None, user)
        return Response({'token': token.key, **_me_payload(user, request)})


def _me_payload(user, request=None):
    technician = getattr(user, 'technician', None)
    if technician is None:
        return {'full_name': user.get_username(), 'role': None, 'country': None, 'permissions': []}
    return {
        'full_name': technician.full_name,
        'username': user.get_username(),
        'email': user.email,
        'phone': technician.phone,
        'photo_url': request.build_absolute_uri(technician.photo.url) if technician.photo and request else '',
        'role': technician.role,
        'role_display': technician.get_role_display(),
        'employment_type_display': technician.get_employment_type_display(),
        'country': technician.country.display_name,
        'language': technician.language,
        'language_choices': [{'value': value, 'label': str(label)} for value, label in technician.Language.choices],
        # Still on a temporary password: the app shows only its "choose
        # your password" screen until this is false.
        'must_change_password': technician.must_change_password,
        # What this role may do (RolePermission), so the app shows only
        # the tabs that will open — the API still checks every request.
        'permissions': sorted(
            RolePermission.objects.filter(role=technician.role, allowed=True).values_list('permission', flat=True),
        ),
    }


class MeView(APIView):
    def get(self, request):
        return Response(_me_payload(request.user, request))

    def post(self, request):
        """Edit my profile, as on the website's My profile (MyProfileForm):
        photo, language, phone and email — multipart; anything left out
        stays as it is. Role, country and employment stay office-side.
        """
        technician = getattr(request.user, 'technician', None)
        if technician is None:
            raise PermissionDenied
        data = {
            'language': technician.language, 'phone': technician.phone, 'email': request.user.email,
            **{field: request.data[field] for field in ('language', 'phone', 'email') if field in request.data},
        }
        form = MyProfileForm(data, request.FILES, instance=technician)
        if not form.is_valid():
            return Response({field: [str(e) for e in errors] for field, errors in form.errors.items()},
                            status=status.HTTP_400_BAD_REQUEST)
        form.save()
        return Response(_me_payload(request.user, request))


class MyPasswordView(APIView):
    """Set a password from the app: on a temporary one, just the new
    password (like the web's first sign-in); otherwise a change that needs
    the current one (like My profile). Their own change, so no phone is
    signed out; a change they didn't make shows up in the email.
    """

    permission_classes = [IsTechnician]
    allow_temporary_password = True

    def post(self, request):
        user = request.user
        technician = user.technician
        if technician.must_change_password:
            form = TechnicianFirstLoginForm(request.data, user=user)
        else:
            form = ChangePasswordForm(user, request.data)
        if not form.is_valid():
            return Response(
                {field: [str(error) for error in errors] for field, errors in form.errors.items()},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if technician.must_change_password:
            user.set_password(form.cleaned_data['new_password1'])
            user.save(update_fields=['password'])
            technician.must_change_password = False
            technician.save(update_fields=['must_change_password'])
        else:
            form.save()
            # Not on first setup: choosing your own over a temporary one isn't news.
            send_password_changed_email(user)
        # Their own change, so this phone and any other stay signed in;
        # the token is sent back so the app's flow doesn't depend on that.
        token, _created = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, **_me_payload(user, request)})


def _schedule_item(task):
    """One task on the app's Today screen: the list fields, plus the
    person's role, the address and the planned window."""
    return {
        **TaskListSerializer(task).data,
        'my_role': getattr(task, 'my_role', ''),
        'address': task.site.address,
        'scheduled_date': task.scheduled_date,
        'estimated_finish': task.estimated_finish,
    }


class MyTodayView(APIView):
    """The app's Today screen — the same plan as the website's (My
    schedule, tasks.views.today_plan): overdue, the next job with the
    lead's next tap and a map link, the rest of today and what's done.
    """

    permission_classes = [IsTechnician]

    def get(self, request):
        plan = today_plan(request.user.technician)
        next_job = plan['next_job']
        return Response({
            'today': plan['today'],
            'today_count': plan['today_count'],
            'overdue': [_schedule_item(task) for task in plan['overdue']],
            'next_job': {
                **_schedule_item(next_job),
                'next_action': next_job.next_action,
                'directions_url': next_job.directions_url,
            } if next_job else None,
            'later_today': [_schedule_item(task) for task in plan['later_today']],
            'done_today': [_schedule_item(task) for task in plan['done_today']],
        })


class MyScheduleView(APIView):
    """The app's Week / Month / Year — the website's My schedule calendar
    (tasks.views._calendar_context) of the person's own tasks: ?view=week
    (&start=YYYY-MM-DD), month (&month=YYYY-MM) or year (&year=YYYY). Weeks
    start on their country's first working day; days off are marked.
    Overdue and the tasks with no day come with every view.

    ?technician=<id> shows someone else's — for whoever can view
    technicians, within the people they oversee in their country (like
    the website's "View schedule"); anyone else is a 404.
    """

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = request.user.technician
        person_id = request.query_params.get('technician')
        if person_id:
            if not has_role_permission(request, RolePermission.Permission.VIEW_TECHNICIANS):
                return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
            technician = get_object_or_404(
                _people_i_oversee(technician).filter(country=technician.country), pk=person_id if person_id.isdigit() else 0,
            )
        mine, with_role = _my_tasks(technician)
        context = _calendar_context(request, mine, technician.country)
        for entry in context['days']:
            with_role(entry['tasks'])

        def day(entry):
            return {
                'date': entry['date'], 'is_today': entry['is_today'], 'is_weekend': entry['is_weekend'],
                'in_month': entry['in_month'], 'week_start': entry['week_start'],
                'tasks': [_schedule_item(task) for task in entry['tasks']],
            }

        view = context['view']
        payload = {
            'view': view,
            'today': context['today'],
            'person': {'id': technician.pk, 'name': technician.full_name},
            'overdue': [_schedule_item(task) for task in _overdue(mine, with_role, timezone.localtime().date())],
            'unscheduled': [_schedule_item(task) for task in with_role(context['unscheduled'])],
        }
        if view == 'year':
            payload.update({
                'year': context['year'],
                'months': [
                    {
                        'first': month['first'],
                        'count': month['count'],
                        'cells': [
                            None if cell is None else {
                                'date': cell['date'], 'count': len(cell['tasks']), 'is_today': cell['is_today'],
                                'is_weekend': cell['is_weekend'], 'week_start': cell['week_start'],
                            }
                            for cell in month['cells']
                        ],
                    }
                    for month in context['year_months']
                ],
            })
        elif view == 'month':
            payload.update({
                'month': context['month'], 'prev_month': context['prev_month'],
                'next_month': context['next_month'], 'this_month': context['this_month'],
                'days': [day(entry) for entry in context['days']],
            })
        else:
            payload.update({
                'start': context['start'], 'end': context['end'], 'prev_start': context['prev_start'],
                'next_start': context['next_start'], 'this_week_start': context['this_week_start'],
                'days': [day(entry) for entry in context['days']],
            })
        return Response(payload)


# The fields the app's New task screen fills in — the website form's,
# less adding a new site / brand / task type there (admin and desk work).
APP_TASK_FIELDS = [
    'site', 'task_type', 'brand', 'required_skill', 'min_level', 'description', 'priority', 'source',
    'is_warranty', 'billing_type', 'reported_at', 'scheduled_for', 'estimated_hours', 'responsible_supervisor',
]


def require_role_permission_or_403(request, permission):
    """The signed-in technician, if their role allows `permission`."""
    if not has_role_permission(request, permission):
        raise PermissionDenied
    return request.user.technician


def _form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


class TaskCreateView(APIView):
    """The app's New task — the website's (tasks.views.task_create), with
    the same form, rules and side effects (create_task): GET the choices
    for every field, in the viewer's country and language, with the
    defaults; POST the fields (times as YYYY-MM-DDTHH:MM, local). Needs
    create_tasks. Converting a ticket stays on the ticket's own page.
    """

    permission_classes = [IsTechnician]

    def _form(self, request, data=None):
        if not has_role_permission(request, RolePermission.Permission.CREATE_TASKS):
            raise PermissionDenied
        technician = request.user.technician
        return TaskCreateForm(data, country=get_active_country(request) or technician.country,
                              is_admin=technician.role == Technician.Role.ADMIN)

    def get(self, request):
        form = self._form(request)
        fields = {}
        for name in APP_TASK_FIELDS:
            field = form.fields[name]
            initial = form[name].initial
            fields[name] = {
                'label': str(field.label),
                'required': name in ('site', 'priority', 'source', 'billing_type', 'reported_at'),
                'initial': '' if initial is None else str(getattr(initial, 'pk', initial)),
                **({'choices': [
                    {'value': str(value), 'label': str(label)} for value, label in field.choices if str(value) != ''
                ]} if hasattr(field, 'choices') else {}),
            }
        return Response({'fields': fields})

    def post(self, request):
        data = {name: request.data.get(name, '') for name in APP_TASK_FIELDS}
        if str(data['is_warranty']).lower() not in ('true', '1', 'on'):
            data.pop('is_warranty')
        form = self._form(request, data)
        if not form.is_valid():
            return Response(_form_errors(form), status=status.HTTP_400_BAD_REQUEST)
        task = create_task(request, form, request.user.technician)
        warning = _weekend_warning(task)
        return Response(
            {'id': task.pk, 'task_number': task.task_number, 'warning': str(warning) if warning else ''},
            status=status.HTTP_201_CREATED,
        )


def _task_line(task):
    """A task in a customer's or a machine's history."""
    return {
        'id': task.pk, 'task_number': task.task_number, 'status_display': task.get_status_display(),
        'reported_at': task.reported_at, 'site_name': task.site.name,
        'task_type_name': task.task_type.display_name if task.task_type_id else '',
    }


class CustomersView(APIView):
    """The app's Customers — the website's list (manage_customers): every
    active customer in the viewer's country, searchable by name."""

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = require_role_permission_or_403(request, RolePermission.Permission.MANAGE_CUSTOMERS)
        customers = Customer.objects.filter(
            is_active=True, country=get_active_country(request) or technician.country,
        ).prefetch_related('sites').order_by('name')
        search = request.query_params.get('q', '').strip()
        if search:
            customers = customers.filter(Q(name__icontains=search) | Q(sites__name__icontains=search)).distinct()
        return Response([
            {
                'id': customer.pk, 'name': customer.name, 'segment': customer.get_segment_display(),
                'sites': [site.name for site in customer.sites.all()],
                'contact_name': customer.contact_name, 'contact_phone': customer.contact_phone,
            }
            for customer in customers[:200]
        ])


class CustomerView(APIView):
    """One customer in the app — the website's customer page: contacts,
    each site (address, contact, access notes, directions, its machines)
    and their recent tasks."""

    permission_classes = [IsTechnician]

    def get(self, request, pk):
        technician = require_role_permission_or_403(request, RolePermission.Permission.MANAGE_CUSTOMERS)
        customer = scoped_or_404(Customer.objects, pk, technician, get_active_country(request), 'country')
        sites = customer.sites.prefetch_related('assets__brand').order_by('name')
        tasks = Task.objects.filter(site__customer=customer).select_related('site', 'task_type').order_by(
            '-reported_at',
        )[:30]
        return Response({
            'id': customer.pk, 'name': customer.name, 'segment': customer.get_segment_display(),
            'contact_name': customer.contact_name, 'contact_phone': customer.contact_phone,
            'contact_email': customer.contact_email, 'billing_address': customer.billing_address,
            'has_portal': customer.user_id is not None,
            'sites': [
                {
                    'id': site.pk, 'name': site.name, 'address': site.address,
                    'contact_name': site.effective_contact_name, 'contact_phone': site.effective_contact_phone,
                    'access_notes': site.access_notes, 'directions_url': directions_url(site),
                    'machines': [
                        {'id': asset.pk, 'name': f"{asset.brand.name if asset.brand_id else ''} {asset.model_name}".strip(),
                         'serial': asset.serial_no, 'status': asset.get_status_display()}
                        for asset in site.assets.all()
                    ],
                }
                for site in sites
            ],
            'tasks': [_task_line(task) for task in tasks],
        })


class MachinesView(APIView):
    """The app's Machines — the website's list (view_machines): every
    machine on file in the viewer's country, searchable by serial, model,
    brand, site or customer."""

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = require_role_permission_or_403(request, RolePermission.Permission.VIEW_MACHINES)
        assets = Asset.objects.filter(
            site__customer__country=get_active_country(request) or technician.country,
        ).select_related('site__customer', 'brand').order_by('site__customer__name', 'site__name', 'model_name')
        search = request.query_params.get('q', '').strip()
        if search:
            assets = assets.filter(
                Q(serial_no__icontains=search) | Q(model_name__icontains=search)
                | Q(brand__name__icontains=search) | Q(site__name__icontains=search)
                | Q(site__customer__name__icontains=search)
            )
        return Response([_machine(asset) for asset in assets[:100]])


def _machine(asset):
    return {
        'id': asset.pk, 'name': f"{asset.brand.name if asset.brand_id else ''} {asset.model_name}".strip(),
        'serial': asset.serial_no, 'status': asset.get_status_display(),
        'customer_id': asset.site.customer_id, 'customer_name': asset.site.customer.name, 'site_name': asset.site.name,
    }


class MachineView(APIView):
    """One machine's history in the app — the website's machine page:
    every visit that recorded it, with what was found and done."""

    permission_classes = [IsTechnician]

    def get(self, request, pk):
        technician = require_role_permission_or_403(request, RolePermission.Permission.VIEW_MACHINES)
        asset = scoped_or_404(
            Asset.objects.select_related('site__customer__country', 'brand'),
            pk, technician, get_active_country(request), 'site__customer__country',
        )
        visits = asset.task_assets.select_related('task__site', 'task__task_type').order_by('-task__reported_at')
        return Response({
            **_machine(asset),
            'installed_on': asset.installed_on, 'warranty_end': asset.warranty_end,
            'history': [
                {
                    **_task_line(visit.task),
                    'outcome': visit.get_outcome_display() if visit.outcome else '',
                    'finding': Finding(visit.finding_code).label if visit.finding_code else visit.note,
                    'action': Action(visit.action_code).label if visit.action_code else visit.action,
                }
                for visit in visits
            ],
        })


class HoursView(APIView):
    """The app's Hours — the website's Hours page (view_technicians): for
    a month (?month=YYYY-MM), each person this viewer oversees, with the
    reports they filed, their hours (h:mm), the estimate and the warning
    counts, and each report behind them."""

    permission_classes = [IsTechnician]

    def get(self, request):
        viewer = require_role_permission_or_403(request, RolePermission.Permission.VIEW_TECHNICIANS)
        month = _resolve_report_month(request)
        rows = _overseen_rows(viewer, _technician_hours_rows(get_active_country(request) or viewer.country, month))
        return Response({
            'month': month,
            'prev_month': _shift_month(month, -1),
            'next_month': _shift_month(month, 1),
            'is_this_month': month == timezone.localtime().date().replace(day=1),
            'total_hours': hours_text(sum(row['hours'] for row in rows)) or '0:00',
            'total_reports': sum(len(row['reports']) for row in rows),
            'rows': [
                {
                    'id': row['technician'].pk, 'name': row['technician'].full_name,
                    'reports_count': len(row['reports']), 'hours': hours_text(row['hours']) or '0:00',
                    'estimated': hours_text(row['estimated']), 'helped_on': row['helped_on'],
                    'overrun_count': row['overrun_count'], 'above_tapped_count': row['above_tapped_count'],
                    'away_count': row['away_count'],
                    'reports': [
                        {
                            'task_id': report.task_id, 'task_number': report.task.task_number,
                            'submitted_at': report.submitted_at, 'hours': hours_text(report.labour_hours) or '0:00',
                            'customer_name': report.task.site.customer.name,
                        }
                        for report in row['reports']
                    ],
                }
                for row in rows
            ],
        })


class InternalTicketsView(APIView):
    """The app's Internal tickets — the website's list (anyone above a
    technician): stock requests, vendor follow-ups and other internal
    tickets, open ones by default (?status=open|closed|all), newest first.
    Each opens its website page in the app to reply, hand on or close."""

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = request.user.technician
        if technician.role == Technician.Role.TECHNICIAN:
            raise PermissionDenied
        tickets = _visible_internal_tickets(technician, get_active_country(request) or technician.country).select_related(
            'assigned_to',
        ).order_by('-submitted_at')
        status_filter = request.query_params.get('status', 'open')
        if status_filter == 'open':
            tickets = tickets.filter(status=CustomerTicket.Status.NEW)
        elif status_filter == 'closed':
            tickets = tickets.filter(status__in=[CustomerTicket.Status.CLOSED, CustomerTicket.Status.CONVERTED])
        return Response([
            {
                'id': ticket.pk, 'ticket_number': ticket.ticket_number, 'kind': ticket.get_kind_display(),
                'subject': ticket.subject, 'description': ticket.description,
                'status_display': ticket.get_status_display(), 'submitted_at': ticket.submitted_at,
                'assigned_to': ticket.assigned_to.full_name if ticket.assigned_to_id else '',
            }
            for ticket in tickets[:200]
        ])


class TeamMapView(APIView):
    """The app's Team map — the website's (view_technicians): where each
    technician last tapped today, as pins, and what needs a follow-up."""

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = require_role_permission_or_403(request, RolePermission.Permission.VIEW_TECHNICIANS)
        data = team_map_data(get_active_country(request) or technician.country)
        return Response({
            'pins': [{key: str(value) if key in ('detail', 'place') else value for key, value in pin.items()
                      if key != 'url'} for pin in data['pins']],
            'attention': [{'text': str(item['text']), 'level': item['level'], 'task_id': item['task_id']}
                          for item in data['attention']],
            'without_taps': [person.full_name for person in data['technicians'] if person.last_tap is None],
        })


class WebsiteMenuView(APIView):
    """The app's More tab: every website page this person may open, in the
    website's own menu groups and order (people.context_processors
    .nav_pages — the same gates as the pages themselves), in their
    language. Their own schedule, progress and skills are native screens
    in the app, so that group is left out.
    """

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = request.user.technician
        allowed = set(
            RolePermission.objects.filter(role=technician.role, allowed=True).values_list('permission', flat=True),
        )
        with translation.override(technician.language):
            groups = nav_groups(nav_pages(technician, allowed), '')
            return Response([
                {
                    'heading': str(group['heading'] or ''),
                    'pages': [{'title': str(page['label']), 'url': page['url'], 'icon': page['icon']}
                              for page in group['pages']],
                }
                for group in groups if group['pages'][0]['group'] != 'mine'
            ])


class WebsiteSessionView(APIView):
    """Opens a website page inside the app, already signed in: the app
    calls this once with its token in the Authorization header (never in
    the address), gets a normal website session in its web view, and is
    sent on to `next` — a path on this site only. From then on the web
    view is just the website, with its own permission checks.
    """

    permission_classes = [IsTechnician]

    def get(self, request):
        next_url = request.query_params.get('next') or '/'
        if not next_url.startswith('/') or not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()},
        ):
            next_url = '/'
        # The session, not request.user: DRF sets the token's user on the
        # underlying request too, so that alone can't tell a session apart.
        if request._request.session.get(SESSION_KEY) != str(request.user.pk):
            login(request._request, request.user, backend='django.contrib.auth.backends.ModelBackend')
        return HttpResponseRedirect(next_url)


class MySkillsView(APIView):
    """The app's My skills — the website's page (tasks.views.my_skills):
    GET the progress, what the levels mean and every skill / conduct area
    with its status; POST a self-rating (multipart: kind = skill|conduct,
    id, level, note, and for a skill the evidence photo or video). Only
    until a supervisor reviews it, as on the website.
    """

    permission_classes = [IsTechnician]

    @staticmethod
    def _summary(request, technician):
        data = skills_summary(technician)
        for group in ('basic', 'advanced', 'conduct'):
            for row in data[group]:
                if row['evidence_url']:
                    row['evidence_url'] = request.build_absolute_uri(row['evidence_url'])
        return data

    def get(self, request):
        return Response(self._summary(request, request.user.technician))

    def post(self, request):
        technician = request.user.technician
        today = timezone.localtime().date()
        kind = request.data.get('kind')
        if kind == 'skill':
            item = get_object_or_404(Skill, pk=request.data.get('id'), is_active=True)
            current = TechnicianSkill.objects.filter(technician=technician, skill=item).first()
            form = SelfRateSkillForm(request.data, request.FILES)
        elif kind == 'conduct':
            item = get_object_or_404(ConductArea, pk=request.data.get('id'), is_active=True)
            current = TechnicianConduct.objects.filter(technician=technician, conduct_area=item).first()
            form = SelfRateLevelForm(request.data)
        else:
            return Response({'detail': 'kind must be skill or conduct.'}, status=status.HTTP_400_BAD_REQUEST)
        if current is not None and current.source != TechnicianSkill.Source.SELF:
            return Response(
                {'detail': str(_('A supervisor has reviewed this, so only a supervisor can change it now.'))},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not form.is_valid():
            return Response({field: [str(e) for e in errors] for field, errors in form.errors.items()},
                            status=status.HTTP_400_BAD_REQUEST)
        if kind == 'skill':
            save_skill_self_rating(technician, item, form.cleaned_data, today)
        else:
            save_conduct_self_rating(technician, item, form.cleaned_data, today)
        return Response(self._summary(request, technician))


def _person(request, pk):
    """Someone the viewer oversees, in their country — like the website's
    technician pages; anyone else is a 404. Needs view_technicians."""
    if not has_role_permission(request, RolePermission.Permission.VIEW_TECHNICIANS):
        raise PermissionDenied
    viewer = request.user.technician
    return get_object_or_404(_people_i_oversee(viewer).filter(country=viewer.country), pk=pk)


class PersonView(APIView):
    """A person's page in the app (Team → a name): their details,
    availability, skills (with what the viewer may do — confirm skills,
    set availability) and work record. The website's technician skills,
    availability and progress pages in one.
    """

    permission_classes = [IsTechnician]

    def get(self, request, pk):
        person = _person(request, pk)
        skills = MySkillsView._summary(request, person)
        reliability = _reliability_stats(person)
        return Response({
            'id': person.pk,
            'name': person.full_name,
            'role': person.get_role_display(),
            'photo_url': request.build_absolute_uri(person.photo.url) if person.photo else '',
            'phone': person.phone,
            'email': person.user.email if person.user_id else '',
            'country': person.country.display_name,
            'is_available': person.is_available,
            'unavailable_reason': person.get_unavailable_reason_display() if not person.is_available else '',
            'unavailable_reasons': [
                {'value': value, 'label': str(label)} for value, label in Technician.UnavailableReason.choices
            ],
            'can_review_skills': has_role_permission(request, RolePermission.Permission.REVIEW_SKILLS),
            'can_set_availability': has_role_permission(request, RolePermission.Permission.ASSIGN_TASKS),
            'skills': skills,
            'record': {
                'completed': reliability['total_completed'],
                'helped': reliability['total_helped'],
                'on_time_percent': reliability['on_time_percent'],
                'acceptance_minutes': reliability['acceptance_latency_minutes'],
            },
        })


class PersonSkillReviewView(APIView):
    """A supervisor confirming or adjusting one of the person's levels
    (kind = skill|conduct, id, level, note) — review_skills, like the
    website's review screen. Returns the person's page again."""

    permission_classes = [IsTechnician]

    def post(self, request, pk):
        person = _person(request, pk)
        if not has_role_permission(request, RolePermission.Permission.REVIEW_SKILLS):
            raise PermissionDenied
        form = ReviewLevelForm(request.data)
        kind = request.data.get('kind')
        if kind not in ('skill', 'conduct'):
            return Response({'detail': 'kind must be skill or conduct.'}, status=status.HTTP_400_BAD_REQUEST)
        if not form.is_valid():
            return Response({field: [str(e) for e in errors] for field, errors in form.errors.items()},
                            status=status.HTTP_400_BAD_REQUEST)
        today = timezone.localtime().date()
        if kind == 'skill':
            skill = get_object_or_404(Skill, pk=request.data.get('id'), is_active=True)
            save_skill_review(person, skill, request.user.technician, form.cleaned_data, today)
        else:
            area = get_object_or_404(ConductArea, pk=request.data.get('id'), is_active=True)
            save_conduct_review(person, area, request.user.technician, form.cleaned_data, today)
        return PersonView().get(request, pk)


class PersonAvailabilityView(APIView):
    """Available, or unavailable with a reason (available = true/false,
    reason) — whoever assigns tasks, like the website's Availability page.
    Returns the person's page again."""

    permission_classes = [IsTechnician]

    def post(self, request, pk):
        person = _person(request, pk)
        if not has_role_permission(request, RolePermission.Permission.ASSIGN_TASKS):
            raise PermissionDenied
        if request.data.get('available') in (True, 'true', '1'):
            set_availability(person, True)
        else:
            form = MarkUnavailableForm(request.data)
            if not form.is_valid():
                return Response({field: [str(e) for e in errors] for field, errors in form.errors.items()},
                                status=status.HTTP_400_BAD_REQUEST)
            set_availability(person, False, form.cleaned_data['reason'])
        return PersonView().get(request, pk)


class MyProgressView(APIView):
    """The app's Progress screen — My progress's own figures
    (tasks.views.progress_context): the year's line, a month (?month=),
    rank and certification."""

    permission_classes = [IsTechnician]

    def get(self, request):
        context = progress_context(request, request.user.technician)
        chart, certification = context['trend_chart'], context['certification']
        return Response({
            'trend': {
                'months': [{'start': p['start'], 'completed': p['completed']} for p in chart['periods']],
                'top': chart['top'],
                'this_month': chart['this_month'],
                'change': chart['change'],
            },
            'month': {
                'first': context['month_first'],
                'is_this_month': context['is_this_month'],
                'hours': context['month_hours'],
                'reports': context['month_report_count'],
                'over_estimate': context['month_overrun_count'],
            },
            'rank': context['rank'],
            'leaderboard_size': context['leaderboard_size'],
            'certification': {
                'certified': certification['non_cardio_certified'] + certification['conduct_certified'],
                'required': certification['non_cardio_total'] + certification['conduct_total'],
                'is_certified': certification['is_certified'],
            },
        })


class TeamProgressView(APIView):
    """The app's team progress — the website's Team progress
    (tasks.views.team_progress_rows): the field team this person oversees
    in their country, by rank, for a month (?month=)."""

    permission_classes = [require_role_permission(RolePermission.Permission.VIEW_TECHNICIANS)]

    def get(self, request):
        viewer = request.user.technician
        this_month = timezone.localtime().date().replace(day=1)
        month_first = min(_resolve_report_month(request), this_month)
        rows = team_progress_rows(viewer, viewer.country, month_first)
        return Response({
            'month': month_first,
            'rows': [
                {
                    'id': row['technician'].pk,
                    'name': row['technician'].full_name,
                    'role': row['technician'].get_role_display(),
                    'rank': row['rank'],
                    'completed': row['completed'],
                    'change': row['change'],
                    'hours': row['hours'],
                    'certified': row['certified'],
                    'required': row['required'],
                    'is_certified': row['is_certified'],
                    'months': [p['completed'] for p in row['trend']['periods']],
                }
                for row in rows
            ],
        })


class MyTaskListView(APIView):
    """Every task this technician is actively assigned to, lead or helper
    — same pool as the my_week board, without the week-boundary filter
    since the app has more room to just show a scrollable list.
    """

    permission_classes = [IsTechnician]

    def get(self, request):
        technician = request.user.technician
        assignments = TaskAssignment.objects.filter(
            technician=technician, is_active=True, task__status__in=OPEN_STATUSES,
        ).select_related('task__site__customer')
        tasks = [a.task for a in assignments]
        tasks.sort(key=lambda t: (t.scheduled_for is None, t.scheduled_for))
        return Response(TaskListSerializer(tasks, many=True).data)


class TaskListView(APIView):
    """Every open task in the requester's active country — the supervisor/
    manager/admin view, same scope as the web task list. With ?q=, a
    search across every status instead (tasks.views.search_tasks), newest
    first, at most SEARCH_RESULT_LIMIT.
    """

    permission_classes = [require_role_permission(RolePermission.Permission.VIEW_TASKS)]

    def get(self, request):
        active_country = get_active_country(request)
        tasks = _with_lead_prefetch(
            Task.objects.filter(site__customer__country=active_country).select_related('site__customer'),
        )
        text = request.query_params.get('q', '').strip()
        if text:
            tasks = search_tasks(tasks, text)
        else:
            tasks = tasks.filter(status__in=OPEN_STATUSES)
        return Response(TaskListSerializer(tasks, many=True).data)


def _get_my_assignment(request, pk):
    return get_object_or_404(
        TaskAssignment.objects.select_related(
            'task__site__customer__country', 'task__task_type', 'task__brand', 'task__required_skill',
        ),
        task__pk=pk, technician=request.user.technician, is_active=True,
    )


class MyTaskDetailView(APIView):
    """A technician's own view of one assigned task — mirrors
    tasks.views.my_task_detail, just as JSON instead of a rendered page.
    """

    permission_classes = [IsTechnician]

    def get(self, request, pk):
        assignment = _get_my_assignment(request, pk)
        task = assignment.task
        is_lead = assignment.role == TaskAssignment.Role.LEAD
        next_action = _next_technician_action(task) if is_lead else None
        is_paused = is_lead and task.status == Task.Status.IN_PROGRESS and _task_is_paused(task)
        context = {
            'next_action': next_action,
            'is_lead': is_lead,
            'can_file_report': is_lead and task.status in REPORT_EDITABLE_STATUSES and not is_paused,
            'requires_signature': _requires_signature(task),
            'undoable_tap': undoable_tap(task, request.user) if is_lead else None,
        }
        return Response(TaskDetailSerializer(task, context=context).data)


class MyTaskActionView(APIView):
    """One of the lead's next-step buttons (accept / en_route / arrive /
    start), undo (their own last tap, within 10 minutes), or
    block/pause/resume — the same state machine as my_task_detail's POST
    handling, reused rather than re-implemented.
    """

    permission_classes = [IsTechnician]

    def post(self, request, pk):
        assignment = _get_my_assignment(request, pk)
        task = assignment.task
        is_lead = assignment.role == TaskAssignment.Role.LEAD
        if not is_lead:
            return Response({'detail': 'Only the lead can act on this task.'}, status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action')
        next_action = _next_technician_action(task)

        if action == 'undo':
            if not undo_last_tap(task, request.user):
                return Response(
                    {'detail': 'That can no longer be undone — ask a manager to correct the time.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({'status': task.status})

        if action in TECHNICIAN_ACTIONS and action == next_action:
            event_type, new_status = TECHNICIAN_ACTIONS[action]
            record_tap(task, event_type, request.user, request.data)
            if new_status:
                task.status = new_status
                task.save(update_fields=['status'])
            return Response({'status': task.status})

        is_paused = task.status == Task.Status.IN_PROGRESS and _task_is_paused(task)
        if action == 'pause' and task.status == Task.Status.IN_PROGRESS and not is_paused:
            form = PauseTaskForm(request.data)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            record_tap(task, TaskEvent.EventType.PAUSED, request.user, request.data, note=form.cleaned_data['note'])
            return Response({'status': task.status})

        if action == 'resume' and is_paused:
            record_tap(task, TaskEvent.EventType.RESUMED, request.user, request.data)
            return Response({'status': task.status})

        if action == 'block' and task.status in BLOCKABLE_STATUSES and not is_paused:
            form = BlockTaskForm(request.data)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            task.status = Task.Status.BLOCKED
            task.save(update_fields=['status'])
            record_tap(task, TaskEvent.EventType.BLOCKED, request.user, request.data, note=form.cleaned_data['note'])
            return Response({'status': task.status})

        return Response({'detail': 'That action is not available right now.'}, status=status.HTTP_400_BAD_REQUEST)


class MyTaskAttachmentView(APIView):
    """Add a photo or video to an assigned task — same upload path
    (extension/size limits) as the web app's my_task_detail form.
    """

    permission_classes = [IsTechnician]

    def post(self, request, pk):
        assignment = _get_my_assignment(request, pk)
        form = TaskAttachmentUploadForm(request.data, request.FILES)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        _save_attachment(request, assignment.task, form.cleaned_data['file'], form.cleaned_data['purpose'])
        return Response(status=status.HTTP_201_CREATED)


def _machine_label(asset):
    return f"{asset.brand.name if asset.brand_id else ''} {asset.model_name}".strip()


def _report_machines(task):
    """The report screen's machines: its rows (what's recorded, or the
    customer's ticket machines before the first filing), the site's
    machines to add from, the brands for registering a new one, and the
    outcomes to choose from.
    """
    existing, new = report_machine_initial(task)
    site_assets = {asset.pk: asset for asset in Asset.objects.filter(site=task.site).select_related('brand')}
    rows = [
        {
            'asset_id': row['asset'], 'name': _machine_label(site_assets[row['asset']]),
            'serial_no': site_assets[row['asset']].serial_no,
            'outcome': row.get('outcome', ''), 'note': row.get('note', ''),
            'finding_code': row.get('finding_code', ''), 'action_code': row.get('action_code', ''), 'action': row.get('action', ''),
        }
        for row in existing if row['asset'] in site_assets
    ] + [
        {
            'ticket_machine_id': row['ticket_machine'], 'brand_id': None, 'model_name': row['model_name'],
            'serial_no': row['serial_no'], 'outcome': '', 'note': '',
            'finding_code': '', 'action_code': '', 'action': '',
        }
        for row in new
    ]
    return {
        'machines': rows,
        'site_machines': [
            {'id': asset.pk, 'name': _machine_label(asset), 'serial_no': asset.serial_no}
            for asset in site_assets.values()
        ],
        'brands': [{'id': brand.pk, 'name': brand.name} for brand in Brand.objects.filter(is_active=True)],
        'outcomes': [{'value': value, 'label': label} for value, label in TaskAsset.Outcome.choices],
        # The report's standard wording (reports.phrases), to pick from.
        'findings': [{'value': value, 'label': label} for value, label in Finding.choices],
        'actions': [{'value': value, 'label': label} for value, label in Action.choices],
        'work_line_choices': [{'value': value, 'label': label} for value, label in WorkLine.choices],
        'recommendation_choices': [{'value': value, 'label': label} for value, label in Recommendation.choices],
        # What the customer reads it in: anything typed should be in this language.
        'report_language': report_language(task.site.customer),
    }


def _machine_form(task, row):
    """The web form's validation for one machine row from the app."""
    if row.get('asset_id'):
        return ExistingAssetOutcomeForm({
            'asset': row.get('asset_id'), 'outcome': row.get('outcome', ''), 'note': row.get('note', ''),
            'finding_code': row.get('finding_code') or '', 'action_code': row.get('action_code') or '',
            'action': row.get('action', ''),
        }, site=task.site)
    return NewAssetForm({
        'brand': row.get('brand_id') or '', 'model_name': row.get('model_name', ''),
        'serial_no': row.get('serial_no', ''), 'outcome': row.get('outcome', ''), 'note': row.get('note', ''),
        'finding_code': row.get('finding_code') or '', 'action_code': row.get('action_code') or '',
        'action': row.get('action', ''),
        'ticket_machine': row.get('ticket_machine_id') or '',
    })


class MyTaskReportView(APIView):
    """The lead's work report — findings, parts used, labour hours and the
    customer's signature. GET returns {report: ... or null}; POST
    files or corrects it, with the same rules and status changes as the
    web form (tasks.views.my_report_form), via the same save_work_report.

    POST body (JSON): findings, action_taken, resolved (bool),
    labour_hours, customer_name, parts (list of {part_code, description,
    quantity}), signature (PNG data URL,
    optional — leaving it out keeps the one already on file),
    signature_waived_reason (customer not available to sign — files
    without a signature), helper_hours ({assignment id: hours} for a
    helper whose hours differ from the report's), machines (optional —
    the machines this visit covered, each {asset_id, outcome, note} for
    one at the site, or {brand_id, model_name, serial_no, outcome, note,
    ticket_machine_id} to register a new one; leaving it out keeps what's
    recorded, as an older app sends no machines).

    A lead taken off the task before their report reached the server (it
    waited on the phone with no signal) isn't refused: the report goes to
    the team as a task message instead, and its hours count toward them
    as handover hours — so the work isn't lost.
    """

    permission_classes = [IsTechnician]

    def _get_lead_task(self, request, pk):
        assignment = _get_my_assignment(request, pk)
        if assignment.role != TaskAssignment.Role.LEAD:
            return None
        return assignment.task

    def get(self, request, pk):
        task = self._get_lead_task(request, pk)
        if task is None:
            return Response({'detail': 'Only the lead can file the report.'}, status=status.HTTP_403_FORBIDDEN)
        report = getattr(task, 'report', None)
        return Response({
            'report': WorkReportSerializer(report).data if report else None,
            # "Send a copy to" starts as wherever the last copy went, else the site's contact.
            'send_copy_to': (report.copy_sent_to if report else '') or task.site.effective_contact_email or '',
            # The visit's hours, from the taps — shown, never typed (Task.time_on_site);
            # final once the report has been sent.
            'time_on_site': hours_text(task.time_on_site()) or None,  # "1:32"
            'time_is_final': bool(report and report.submitted_at),
            # Each helper's hours box: empty = same as the report's.
            'helpers': [
                {'id': a.pk, 'technician_name': a.technician.full_name, 'labour_hours': a.labour_hours}
                for a in _active_helpers(task)
            ],
            **_report_machines(task),
        })

    def post(self, request, pk):
        former_lead = TaskAssignment.objects.filter(
            task__pk=pk, technician=request.user.technician, role=TaskAssignment.Role.LEAD, is_active=False,
        ).select_related('task__site__customer__country').order_by('-ended_at').first()
        on_task = TaskAssignment.objects.filter(
            task__pk=pk, technician=request.user.technician, is_active=True,
        ).exists()
        if former_lead and not on_task:
            return self._forward_from_former_lead(request, former_lead)

        task = self._get_lead_task(request, pk)
        if task is None:
            return Response({'detail': 'Only the lead can file the report.'}, status=status.HTTP_403_FORBIDDEN)
        if task.status == Task.Status.CLOSED:
            return Response(
                {'detail': 'This task is closed — only a manager or admin can correct its report now.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if task.status not in REPORT_EDITABLE_STATUSES:
            return Response(
                {'detail': 'Start work on this task before filing a report.'}, status=status.HTTP_400_BAD_REQUEST,
            )
        if task.status == Task.Status.IN_PROGRESS and _task_is_paused(task):
            return Response(
                {'detail': 'Mark yourself started again before filing the report.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = {
            field: request.data.get(field, '')
            for field in ('findings', 'action_taken', 'customer_name', 'send_copy_to', 'recommendation')
        }
        # The standard work lines and recommendations, as lists of codes.
        data['work_lines'] = list(request.data.get('work_lines') or [])
        data['recommendations'] = list(request.data.get('recommendations') or [])
        data['resolved'] = str(bool(request.data.get('resolved')))
        # ?preview=1: the "Review report" step — the report as it would be
        # sent, nothing saved; the customer signs after the review.
        preview = request.query_params.get('preview') == '1'
        data['signature_drawn'] = '' if preview else request.data.get('signature') or ''
        waived_reason = request.data.get('signature_waived_reason') or ''
        if waived_reason:
            data['signature_waived'] = 'on'
            data['signature_waived_reason'] = waived_reason
        for assignment_id, hours in (request.data.get('helper_hours') or {}).items():
            data[f'helper_hours_{assignment_id}'] = '' if hours is None else str(hours)
        # A report saved offline in the app carries when it was filed: the
        # visit's time stops then, not when the phone was back online.
        filed_at = parse_datetime(str(request.data.get('filed_at') or ''))
        if filed_at is not None:
            filed_at = min(filed_at if timezone.is_aware(filed_at) else timezone.make_aware(filed_at), timezone.now())
        report_form = WorkReportForm(
            data, instance=getattr(task, 'report', None),
            require_signature=_requires_signature(task) and not preview, helpers=_active_helpers(task),
            time_on_site=task.time_on_site(until=filed_at),
        )
        report_form.filed_at = filed_at
        part_forms = [PartUsedItemForm(part) for part in request.data.get('parts') or []]
        machine_rows = request.data.get('machines')
        machine_forms = None
        if isinstance(machine_rows, list):
            machine_forms = [_machine_form(task, row) for row in machine_rows if isinstance(row, dict)]

        errors = {}
        if not report_form.is_valid():
            errors.update(report_form.errors)
        part_errors = [form.errors for form in part_forms if not form.is_valid()]
        if part_errors:
            errors['parts'] = part_errors
        if machine_forms is not None:
            machine_errors = [form.errors for form in machine_forms if not form.is_valid()]
            if machine_errors:
                errors['machines'] = machine_errors
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        if preview:
            html, problems = report_preview(
                request, task, request.user.technician, report_form, [form.cleaned_data for form in part_forms],
                None if machine_forms is None else (
                    [form.cleaned_data for form in machine_forms if isinstance(form, ExistingAssetOutcomeForm)],
                    [form.cleaned_data for form in machine_forms if isinstance(form, NewAssetForm)],
                ),
            )
            return Response({'html': html, 'problems': [str(problem) for problem in problems]})

        with transaction.atomic():
            report = save_work_report(
                request, task, request.user.technician, report_form, [form.cleaned_data for form in part_forms],
            )
            if machine_forms is not None:
                save_task_machines(
                    task,
                    [form.cleaned_data for form in machine_forms if isinstance(form, ExistingAssetOutcomeForm)],
                    [form.cleaned_data for form in machine_forms if isinstance(form, NewAssetForm)],
                )
        return Response(
            {
                'status': task.status,
                'message': str(report_saved_message(report.first_submission, task)),
                'report': WorkReportSerializer(report).data,
            },
            status=status.HTTP_201_CREATED if report.first_submission else status.HTTP_200_OK,
        )

    def _forward_from_former_lead(self, request, assignment):
        task = assignment.task
        try:
            hours = Decimal(str(request.data.get('labour_hours') or '')).quantize(Decimal('0.01'))
        except InvalidOperation:
            hours = None
        if hours is not None and Decimal('0') <= hours < Decimal('1000'):
            assignment.labour_hours = hours
            assignment.save(update_fields=['labour_hours'])

        lines = [
            _('Report from %(name)s, submitted after they were removed from this task:') % {
                'name': request.user.technician.full_name,
            },
            _('Findings: %(text)s') % {'text': request.data.get('findings') or '—'},
        ]
        if request.data.get('action_taken'):
            lines.append(_('Action taken: %(text)s') % {'text': request.data['action_taken']})
        lines.append(_('Resolved: %(answer)s') % {'answer': _('Yes') if request.data.get('resolved') else _('No')})
        if hours is not None:
            lines.append(_('Hours worked: %(hours)s') % {'hours': hours})
        parts = [
            f"{part.get('part_code', '')} × {part.get('quantity', '')}"
            for part in request.data.get('parts') or [] if part.get('part_code')
        ]
        if parts:
            lines.append(_('Parts used: %(parts)s') % {'parts': ', '.join(parts)})
        _post_task_message(request, task, '\n'.join(lines))

        return Response({
            'status': task.status,
            'message': _('You are no longer assigned to this task, so your report has been sent to the team as a '
                         'message.'),
            'report': None,
        })


def _get_team_task(request, pk):
    """Same scope as the web task detail: the active country, or any
    country for the manager tier.
    """
    return scoped_or_404(
        Task.objects.select_related(
            'site__customer__country', 'task_type', 'brand', 'required_skill', 'responsible_supervisor', 'report',
        ).prefetch_related('assignments__technician', 'report__parts_used'),
        pk, request.user.technician, get_active_country(request), 'site__customer__country',
    )


def _owns_task(request, task):
    try:
        _require_task_owner(request, task)
    except PermissionDenied:
        return False
    return True


def _team_task_response(request, task):
    technician = request.user.technician
    owns = _owns_task(request, task)
    context = {
        'requires_signature': bool(task.task_type and task.task_type.requires_signature),
        'can_assign': (
            owns and has_role_permission(request, RolePermission.Permission.ASSIGN_TASKS)
            and any(
                assignment_changeable(task, a, technician)
                for a in [None, *(a for a in task.assignments.all() if a.is_active)]
            )
        ),
        'can_supervisor_approve': (
            task.status == Task.Status.PENDING_SUPERVISOR_REVIEW and can_supervisor_approve(technician, task)
        ),
        'can_manager_approve': task.status == Task.Status.COMPLETED and technician.is_manager_tier,
    }
    return Response(TeamTaskDetailSerializer(task, context=context).data)


class TeamTaskDetailView(APIView):
    """Any task in the requester's scope, with its lead/helpers, report,
    and which of assign/approve the requester can do — the mobile side of
    the web task detail for supervisors, managers and admins.
    """

    permission_classes = [require_role_permission(RolePermission.Permission.VIEW_TASKS)]

    def get(self, request, pk):
        return _team_task_response(request, _get_team_task(request, pk))


class TeamTaskCandidatesView(APIView):
    """Technicians who could take this task — same pool as the web assign
    screen, with skill level and their next booked job so a clash shows.
    Only available ones can be picked (`is_available`).
    """

    permission_classes = [require_role_permission(RolePermission.Permission.ASSIGN_TASKS)]

    def get(self, request, pk):
        task = _get_team_task(request, pk)
        _require_task_owner(request, task)
        assigned_ids = {a.technician_id for a in task.assignments.all() if a.is_active}
        candidates = _technicians_with_next_scheduled_task(
            _candidates_with_skill_level(_assignment_candidates(task, exclude_ids=assigned_ids), task),
        )
        return Response([
            {
                'id': technician.pk,
                'full_name': technician.full_name,
                'is_available': technician.is_available,
                # Their home country when they're here on a trip, else null.
                'visiting_from': technician.country.display_name
                if technician.country_id != task.site.customer.country_id else None,
                'skill_level': technician.skill_level,
                'next_task_number': technician.next_scheduled_task.task_number
                if technician.next_scheduled_task else None,
                'next_task_at': technician.next_scheduled_task.scheduled_for
                if technician.next_scheduled_task else None,
            }
            for technician in candidates
        ])


class TeamTaskAssignView(APIView):
    """set_lead / add_helper / remove_helper — the web assign screen's
    actions, with the same forms, lock and ownership rules.

    Body: {"action": "set_lead", "technician": id, "end_reason": "..."}
    (end_reason only when replacing a lead), {"action": "add_helper",
    "technician": id}, or {"action": "remove_helper", "assignment_id": id,
    "end_reason": "..."}.
    """

    permission_classes = [require_role_permission(RolePermission.Permission.ASSIGN_TASKS)]

    def post(self, request, pk):
        task = _get_team_task(request, pk)
        _require_task_owner(request, task)
        technician = request.user.technician
        active = [a for a in task.assignments.all() if a.is_active]
        active_lead = next((a for a in active if a.role == TaskAssignment.Role.LEAD), None)
        locked_message = Response(
            {'detail': 'Work has started — the team can no longer be changed.'}, status=status.HTTP_400_BAD_REQUEST,
        )
        selectable = _assignment_candidates(task, exclude_ids={a.technician_id for a in active}).filter(
            is_available=True,
        )
        action = request.data.get('action')

        if action == 'set_lead':
            if not assignment_changeable(task, active_lead, technician):
                return locked_message
            form = SetLeadForm(
                request.data, technicians=selectable, requires_reason=bool(active_lead),
                ask_hours=(
                    bool(active_lead) and task.status == Task.Status.IN_PROGRESS and technician.is_manager_tier
                ),
            )
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            _set_lead(
                task, active_lead, form.cleaned_data['technician'], form.cleaned_data.get('end_reason', ''),
                request.user, form.cleaned_data.get('hours_worked'),
            )
        elif action == 'add_helper':
            if not assignment_changeable(task, technician=technician):
                return locked_message
            if not active_lead:
                return Response({'detail': 'Set a lead first.'}, status=status.HTTP_400_BAD_REQUEST)
            form = AddHelperForm(request.data, technicians=selectable)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            TaskAssignment.objects.create(
                task=task, technician=form.cleaned_data['technician'], role=TaskAssignment.Role.HELPER,
                assigned_at=timezone.now(), is_active=True,
            )
            push_assigned(form.cleaned_data['technician'], task, is_lead=False)
        elif action == 'remove_helper':
            helper = get_object_or_404(
                TaskAssignment, pk=request.data.get('assignment_id'), task=task,
                role=TaskAssignment.Role.HELPER, is_active=True,
            )
            if not assignment_changeable(task, helper, technician):
                return locked_message
            form = RemoveAssignmentForm(request.data)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            helper.is_active = False
            helper.ended_at = timezone.now()
            helper.end_reason = form.cleaned_data['end_reason']
            helper.save()
            push_removed(helper.technician, task)
        else:
            return Response({'detail': 'Unknown action.'}, status=status.HTTP_400_BAD_REQUEST)

        return _team_task_response(request, _get_team_task(request, pk))


class TeamTaskApproveView(APIView):
    """Approves the filed report one step: the responsible supervisor (or
    a manager) moves it from supervisor review to awaiting the manager; a
    manager/admin approves and closes. Same helpers as the web buttons.
    """

    permission_classes = [require_role_permission(RolePermission.Permission.VIEW_TASKS)]

    def post(self, request, pk):
        task = _get_team_task(request, pk)
        technician = request.user.technician
        if task.status == Task.Status.PENDING_SUPERVISOR_REVIEW:
            if not can_supervisor_approve(technician, task):
                raise PermissionDenied
            approve_report_as_supervisor(task, request.user)
        elif task.status == Task.Status.COMPLETED:
            require_manager(request)
            approve_report_as_manager(task, request.user)
        else:
            return Response(
                {'detail': 'This task has no report awaiting approval.'}, status=status.HTTP_400_BAD_REQUEST,
            )
        return _team_task_response(request, task)


class PushDeviceView(APIView):
    """The app registering this phone for push notifications after sign-in
    (POST {"token": "ExponentPushToken[...]"}), and dropping it on sign-out
    (DELETE with the same body). A phone signing in as someone else moves
    to them.
    """

    permission_classes = [IsTechnician]
    # The app registers for pushes right after sign-in, before the
    # "choose your password" screen is done.
    allow_temporary_password = True

    def post(self, request):
        token = str(request.data.get('token', '')).strip()
        if not token.startswith(('ExponentPushToken[', 'ExpoPushToken[')):
            return Response({'detail': 'Not an Expo push token.'}, status=status.HTTP_400_BAD_REQUEST)
        PushDevice.objects.update_or_create(
            token=token, defaults={'technician': request.user.technician, 'created_at': timezone.now()},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request):
        PushDevice.objects.filter(
            token=str(request.data.get('token', '')), technician=request.user.technician,
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PartListView(APIView):
    """The active parts catalogue, for the report screen's picker."""

    permission_classes = [IsTechnician]

    def get(self, request):
        return Response(list(Part.objects.filter(is_active=True).values('code', 'description')))


class TicketListView(APIView):
    """New customer tickets in the requester's active country — supervisor/
    manager/admin only, same permission as the web ticket list. With ?q=,
    a search across every status instead (tasks.views.search_tickets).
    """

    permission_classes = [require_role_permission(RolePermission.Permission.MANAGE_TICKETS)]

    def get(self, request):
        active_country = get_active_country(request)
        tickets = CustomerTicket.objects.filter(
            country=active_country, kind=CustomerTicket.Kind.CUSTOMER,
        ).select_related('country')
        text = request.query_params.get('q', '').strip()
        if text:
            # A search looks at every ticket, not just new ones.
            tickets = search_tickets(tickets, text).order_by('-submitted_at')[:SEARCH_RESULT_LIMIT]
        else:
            tickets = tickets.filter(status=CustomerTicket.Status.NEW).order_by('-submitted_at')
        return Response(CustomerTicketSerializer(tickets, many=True).data)
