from rest_framework import serializers

from reports.models import PartUsed, WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskAttachment, TaskEvent


class TaskListSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source='site.name')
    customer_name = serializers.CharField(source='site.customer.name')
    status_display = serializers.CharField(source='get_status_display')
    priority_display = serializers.CharField(source='get_priority_display')

    class Meta:
        model = Task
        fields = [
            'id', 'task_number', 'site_name', 'customer_name', 'status', 'status_display',
            'priority', 'priority_display', 'scheduled_for', 'promised_at', 'reported_at',
        ]


class TaskEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source='get_event_type_display')
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = TaskEvent
        fields = ['id', 'event_type', 'event_type_display', 'occurred_at', 'actor_name', 'note']

    def get_actor_name(self, obj):
        technician = getattr(obj.actor, 'technician', None)
        return technician.full_name if technician else (obj.actor.get_username() if obj.actor else '')


class TaskAttachmentSerializer(serializers.ModelSerializer):
    media_type_display = serializers.CharField(source='get_media_type_display')
    purpose_display = serializers.CharField(source='get_purpose_display')

    class Meta:
        model = TaskAttachment
        fields = ['id', 'url', 'media_type', 'media_type_display', 'purpose', 'purpose_display', 'uploaded_at']


class TaskDetailSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source='site.name')
    site_address = serializers.CharField(source='site.address')
    customer_name = serializers.CharField(source='site.customer.name')
    currency_code = serializers.CharField(source='site.customer.country.currency_code')
    status_display = serializers.CharField(source='get_status_display')
    priority_display = serializers.CharField(source='get_priority_display')
    task_type_name = serializers.CharField(source='task_type.display_name', default='')
    brand_name = serializers.CharField(source='brand.name', default='')
    required_skill_name = serializers.CharField(source='required_skill.display_name', default='')
    events = TaskEventSerializer(many=True, read_only=True)
    attachments = TaskAttachmentSerializer(many=True, read_only=True)
    next_action = serializers.SerializerMethodField()
    is_lead = serializers.SerializerMethodField()
    can_file_report = serializers.SerializerMethodField()
    requires_signature = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'task_number', 'site_name', 'site_address', 'customer_name', 'currency_code',
            'status', 'status_display', 'priority', 'priority_display',
            'task_type_name', 'brand_name', 'required_skill_name', 'description',
            'reported_at', 'promised_at', 'scheduled_for', 'estimated_hours',
            'events', 'attachments', 'next_action', 'is_lead', 'can_file_report',
            'requires_signature',
        ]

    def get_next_action(self, obj):
        return self.context.get('next_action')

    def get_is_lead(self, obj):
        return self.context.get('is_lead', False)

    def get_can_file_report(self, obj):
        return self.context.get('can_file_report', False)

    def get_requires_signature(self, obj):
        return self.context.get('requires_signature', False)


class CustomerTicketSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source='country.display_name')
    status_display = serializers.CharField(source='get_status_display')

    class Meta:
        model = CustomerTicket
        fields = [
            'id', 'company_name', 'site_description', 'country_name', 'status', 'status_display',
            'contact_name', 'contact_phone', 'description', 'submitted_at',
        ]


class PartUsedSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartUsed
        fields = ['part_code', 'description', 'quantity', 'unit_cost', 'currency_code']


class WorkReportSerializer(serializers.ModelSerializer):
    parts_used = PartUsedSerializer(many=True, read_only=True)

    class Meta:
        model = WorkReport
        fields = [
            'findings', 'action_taken', 'resolved', 'labour_hours', 'customer_name',
            'signature_url', 'submitted_at', 'parts_used',
        ]


class AssignmentSerializer(serializers.ModelSerializer):
    technician_id = serializers.IntegerField(source='technician.id')
    technician_name = serializers.CharField(source='technician.full_name')

    class Meta:
        model = TaskAssignment
        fields = ['id', 'technician_id', 'technician_name', 'role', 'assigned_at']


class TeamTaskDetailSerializer(TaskDetailSerializer):
    """A supervisor's/manager's view of any task in scope — the
    technician detail plus who's on it, the filed report, and what the
    requester may do next (computed by the view, never guessed by the app).
    """

    lead = serializers.SerializerMethodField()
    helpers = serializers.SerializerMethodField()
    report = serializers.SerializerMethodField()
    responsible_supervisor_name = serializers.CharField(source='responsible_supervisor.full_name', default='')
    can_assign = serializers.SerializerMethodField()
    can_supervisor_approve = serializers.SerializerMethodField()
    can_manager_approve = serializers.SerializerMethodField()

    class Meta(TaskDetailSerializer.Meta):
        fields = TaskDetailSerializer.Meta.fields + [
            'lead', 'helpers', 'report', 'responsible_supervisor_name',
            'can_assign', 'can_supervisor_approve', 'can_manager_approve',
        ]

    def _active(self, obj):
        return [a for a in obj.assignments.all() if a.is_active]

    def get_lead(self, obj):
        lead = next((a for a in self._active(obj) if a.role == TaskAssignment.Role.LEAD), None)
        return AssignmentSerializer(lead).data if lead else None

    def get_helpers(self, obj):
        return AssignmentSerializer(
            [a for a in self._active(obj) if a.role == TaskAssignment.Role.HELPER], many=True,
        ).data

    def get_report(self, obj):
        report = getattr(obj, 'report', None)
        return WorkReportSerializer(report).data if report else None

    def get_can_assign(self, obj):
        return self.context.get('can_assign', False)

    def get_can_supervisor_approve(self, obj):
        return self.context.get('can_supervisor_approve', False)

    def get_can_manager_approve(self, obj):
        return self.context.get('can_manager_approve', False)
