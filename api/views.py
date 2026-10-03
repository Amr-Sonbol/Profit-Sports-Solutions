from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from people.models import RolePermission
from people.permissions import get_active_country, require_manager, scoped_or_404
from reports.forms import PartUsedItemForm, WorkReportForm
from tasks.forms import (
    AddHelperForm, BlockTaskForm, PauseTaskForm, RemoveAssignmentForm, SetLeadForm, TaskAttachmentUploadForm,
)
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskEvent
from tasks.views import (
    ASSIGNMENT_LOCKED_STATUSES, BLOCKABLE_STATUSES, OPEN_STATUSES, REPORT_EDITABLE_STATUSES, TECHNICIAN_ACTIONS,
    _assignment_candidates, _candidates_with_skill_level, _next_technician_action, _require_task_owner, _set_lead,
    _requires_signature, _save_attachment, _task_is_paused, _technicians_with_next_scheduled_task,
    _with_lead_prefetch, approve_report_as_manager, approve_report_as_supervisor, can_supervisor_approve,
    report_saved_message, save_work_report, undo_last_tap, undoable_tap,
)

from .permissions import IsTechnician, has_role_permission, require_role_permission
from .serializers import (
    CustomerTicketSerializer, TaskDetailSerializer, TaskListSerializer, TeamTaskDetailSerializer, WorkReportSerializer,
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
        return Response({'token': token.key, **_me_payload(user)})


def _me_payload(user):
    technician = getattr(user, 'technician', None)
    if technician is None:
        return {'full_name': user.get_username(), 'role': None, 'country': None}
    return {
        'full_name': technician.full_name,
        'role': technician.role,
        'role_display': technician.get_role_display(),
        'country': technician.country.display_name,
        'language': technician.language,
    }


class MeView(APIView):
    def get(self, request):
        return Response(_me_payload(request.user))


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
    """Every task in the requester's active country — the supervisor/
    manager/admin view, same scope as the web task list.
    """

    permission_classes = [require_role_permission(RolePermission.Permission.VIEW_TASKS)]

    def get(self, request):
        active_country = get_active_country(request)
        tasks = _with_lead_prefetch(
            Task.objects.filter(site__customer__country=active_country).select_related('site__customer'),
        ).filter(status__in=OPEN_STATUSES)
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
            TaskEvent.objects.create(
                task=task, event_type=event_type, occurred_at=timezone.now(), actor=request.user,
            )
            if new_status:
                task.status = new_status
                task.save(update_fields=['status'])
            return Response({'status': task.status})

        is_paused = task.status == Task.Status.IN_PROGRESS and _task_is_paused(task)
        if action == 'pause' and task.status == Task.Status.IN_PROGRESS and not is_paused:
            form = PauseTaskForm(request.data)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.PAUSED, occurred_at=timezone.now(),
                actor=request.user, note=form.cleaned_data['note'],
            )
            return Response({'status': task.status})

        if action == 'resume' and is_paused:
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.RESUMED, occurred_at=timezone.now(), actor=request.user,
            )
            return Response({'status': task.status})

        if action == 'block' and task.status in BLOCKABLE_STATUSES and not is_paused:
            form = BlockTaskForm(request.data)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            task.status = Task.Status.BLOCKED
            task.save(update_fields=['status'])
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.BLOCKED, occurred_at=timezone.now(),
                actor=request.user, note=form.cleaned_data['note'],
            )
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


class MyTaskReportView(APIView):
    """The lead's work report — findings, parts used, labour hours and the
    customer's signature. GET returns {report: ... or null}; POST
    files or corrects it, with the same rules and status changes as the
    web form (tasks.views.my_report_form), via the same save_work_report.

    POST body (JSON): findings, action_taken, resolved (bool),
    labour_hours, customer_name, parts (list of {part_code, description,
    quantity, unit_cost, currency_code}), signature (PNG data URL,
    optional — leaving it out keeps the one already on file).
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
        return Response({'report': WorkReportSerializer(report).data if report else None})

    def post(self, request, pk):
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
            for field in ('findings', 'action_taken', 'labour_hours', 'customer_name')
        }
        data['resolved'] = str(bool(request.data.get('resolved')))
        data['signature_drawn'] = request.data.get('signature') or ''
        report_form = WorkReportForm(
            data, instance=getattr(task, 'report', None), require_signature=_requires_signature(task),
        )
        part_forms = [PartUsedItemForm(part) for part in request.data.get('parts') or []]

        errors = {}
        if not report_form.is_valid():
            errors.update(report_form.errors)
        part_errors = [form.errors for form in part_forms if not form.is_valid()]
        if part_errors:
            errors['parts'] = part_errors
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            report = save_work_report(
                request, task, request.user.technician, report_form, [form.cleaned_data for form in part_forms],
            )
        return Response(
            {
                'status': task.status,
                'message': str(report_saved_message(report.first_submission, task)),
                'report': WorkReportSerializer(report).data,
            },
            status=status.HTTP_201_CREATED if report.first_submission else status.HTTP_200_OK,
        )


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
            owns and task.status not in ASSIGNMENT_LOCKED_STATUSES
            and has_role_permission(request, RolePermission.Permission.ASSIGN_TASKS)
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
        if task.status in ASSIGNMENT_LOCKED_STATUSES:
            return Response(
                {'detail': 'Work has started — the team can no longer be changed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        active = [a for a in task.assignments.all() if a.is_active]
        active_lead = next((a for a in active if a.role == TaskAssignment.Role.LEAD), None)
        selectable = _assignment_candidates(task, exclude_ids={a.technician_id for a in active}).filter(
            is_available=True,
        )
        action = request.data.get('action')

        if action == 'set_lead':
            form = SetLeadForm(request.data, technicians=selectable, requires_reason=bool(active_lead))
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            _set_lead(
                task, active_lead, form.cleaned_data['technician'], form.cleaned_data.get('end_reason', ''),
                request.user,
            )
        elif action == 'add_helper':
            if not active_lead:
                return Response({'detail': 'Set a lead first.'}, status=status.HTTP_400_BAD_REQUEST)
            form = AddHelperForm(request.data, technicians=selectable)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            TaskAssignment.objects.create(
                task=task, technician=form.cleaned_data['technician'], role=TaskAssignment.Role.HELPER,
                assigned_at=timezone.now(), is_active=True,
            )
        elif action == 'remove_helper':
            helper = get_object_or_404(
                TaskAssignment, pk=request.data.get('assignment_id'), task=task,
                role=TaskAssignment.Role.HELPER, is_active=True,
            )
            form = RemoveAssignmentForm(request.data)
            if not form.is_valid():
                return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
            helper.is_active = False
            helper.ended_at = timezone.now()
            helper.end_reason = form.cleaned_data['end_reason']
            helper.save()
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


class TicketListView(APIView):
    """New customer tickets in the requester's active country — supervisor/
    manager/admin only, same permission as the web ticket list.
    """

    permission_classes = [require_role_permission(RolePermission.Permission.MANAGE_TICKETS)]

    def get(self, request):
        active_country = get_active_country(request)
        tickets = CustomerTicket.objects.filter(
            country=active_country, status=CustomerTicket.Status.NEW,
        ).select_related('country').order_by('-submitted_at')
        return Response(CustomerTicketSerializer(tickets, many=True).data)
