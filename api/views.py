from django.contrib.auth import authenticate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from people.models import RolePermission
from people.permissions import get_active_country
from tasks.forms import BlockTaskForm, PauseTaskForm, TaskAttachmentUploadForm
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskEvent
from tasks.views import (
    BLOCKABLE_STATUSES, OPEN_STATUSES, REPORT_EDITABLE_STATUSES, TECHNICIAN_ACTIONS, _next_technician_action,
    _save_attachment, _task_is_paused, _with_lead_prefetch,
)

from .permissions import IsTechnician, require_role_permission
from .serializers import CustomerTicketSerializer, TaskDetailSerializer, TaskListSerializer


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
        'country': technician.country.name,
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
            'task__site__customer', 'task__task_type', 'task__brand', 'task__required_skill',
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
        }
        return Response(TaskDetailSerializer(task, context=context).data)


class MyTaskActionView(APIView):
    """One of the lead's next-step buttons (accept / en_route / arrive /
    start), or block/pause/resume — the same state machine as
    my_task_detail's POST handling, reused rather than re-implemented.
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
