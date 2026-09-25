from rest_framework import serializers

from tasks.models import CustomerTicket, Task, TaskAttachment, TaskEvent


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

    class Meta:
        model = Task
        fields = [
            'id', 'task_number', 'site_name', 'site_address', 'customer_name',
            'status', 'status_display', 'priority', 'priority_display',
            'task_type_name', 'brand_name', 'required_skill_name', 'description',
            'reported_at', 'promised_at', 'scheduled_for', 'estimated_hours',
            'events', 'attachments', 'next_action', 'is_lead', 'can_file_report',
        ]

    def get_next_action(self, obj):
        return self.context.get('next_action')

    def get_is_lead(self, obj):
        return self.context.get('is_lead', False)

    def get_can_file_report(self, obj):
        return self.context.get('can_file_report', False)


class CustomerTicketSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source='country.name')
    status_display = serializers.CharField(source='get_status_display')

    class Meta:
        model = CustomerTicket
        fields = [
            'id', 'company_name', 'site_description', 'country_name', 'status', 'status_display',
            'contact_name', 'contact_phone', 'description', 'submitted_at',
        ]
