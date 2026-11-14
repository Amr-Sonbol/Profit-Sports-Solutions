
from django.utils import timezone
from rest_framework import serializers

from reports.models import PartUsed, WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskAttachment, TaskEvent
from spots.media import signed_media_url


class TaskListSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source='site.name')
    customer_name = serializers.CharField(source='site.customer.name')
    status_display = serializers.CharField(source='get_status_display')
    priority_display = serializers.CharField(source='get_priority_display')
    # Who it's assigned to — or, once done, who did it: the lead.
    lead_name = serializers.SerializerMethodField()
    # Team messages this person hasn't read yet (context "unread": {task id: count}).
    unread_messages = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'task_number', 'site_name', 'customer_name', 'status', 'status_display',
            'priority', 'priority_display', 'scheduled_for', 'promised_at', 'reported_at', 'lead_name',
            'unread_messages',
        ]

    def get_unread_messages(self, obj):
        return self.context.get('unread', {}).get(obj.pk, 0)

    def get_lead_name(self, obj):
        leads = getattr(obj, 'lead_assignments', None)  # prefetched for the team list (_with_lead_prefetch)
        if leads is None:
            leads = [a for a in obj.assignments.all() if a.is_active and a.role == TaskAssignment.Role.LEAD]
        return leads[0].technician.full_name if leads else ''


class TaskEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source='get_event_type_display')
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = TaskEvent
        fields = ['id', 'event_type', 'event_type_display', 'occurred_at', 'corrected_at', 'actor_name', 'note']

    def get_actor_name(self, obj):
        technician = getattr(obj.actor, 'technician', None) if obj.actor_id else None
        return technician.full_name if technician else (obj.actor.get_username() if obj.actor else '')


class TaskAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    media_type_display = serializers.CharField(source='get_media_type_display')
    purpose_display = serializers.CharField(source='get_purpose_display')
    # The app loads files without the website's sign-in: a signed link (spots.media).
    url = serializers.SerializerMethodField()
    # The machine it shows, if taken from a machine's card ('' for the visit as a whole).
    asset_name = serializers.SerializerMethodField()

    class Meta:
        model = TaskAttachment
        fields = [
            'id', 'url', 'media_type', 'media_type_display', 'purpose', 'purpose_display', 'uploaded_at', 'asset', 'asset_name',
            # A customer's photo the technician confirmed ("From the customer · confirmed by …").
            'source', 'uploaded_by_name',
        ]

    def get_url(self, obj):
        request = self.context.get('request')
        return signed_media_url(request, obj.url) if request else obj.url

    def get_uploaded_by_name(self, obj):
        technician = getattr(obj.uploaded_by, 'technician', None)
        return technician.full_name if technician else obj.uploaded_by.get_username()

    def get_asset_name(self, obj):
        if not obj.asset_id:
            return ''
        asset = obj.asset
        return f"{asset.brand.name if asset.brand_id else ''} {asset.model_name}".strip() + (f' · {asset.serial_no}' if asset.serial_no else '')


def directions_url(site):
    """Google Maps to the site (Site.directions_url)."""
    return site.directions_url


class TaskDetailSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source='site.name')
    site_address = serializers.CharField(source='site.address')
    customer_name = serializers.CharField(source='site.customer.name')
    currency_code = serializers.CharField(source='site.customer.country.currency_code')
    status_display = serializers.CharField(source='get_status_display')
    priority_display = serializers.CharField(source='get_priority_display')
    task_type_name = serializers.CharField(source='task_type.display_name', default='')
    brand_name = serializers.CharField(source='brand.name', default='')
    events = serializers.SerializerMethodField()
    about_display = serializers.CharField(source='get_about_display', read_only=True)
    # Who created the task, and when (Task.creator); who last changed it (spots.audit.trail).
    created_by = serializers.SerializerMethodField()
    last_changed = serializers.SerializerMethodField()
    attachments = TaskAttachmentSerializer(many=True, read_only=True)
    next_action = serializers.SerializerMethodField()
    shipment = serializers.SerializerMethodField()
    from_request = serializers.SerializerMethodField()
    is_lead = serializers.SerializerMethodField()
    can_file_report = serializers.SerializerMethodField()
    requires_signature = serializers.SerializerMethodField()
    undoable_step = serializers.SerializerMethodField()
    # Who to call on site, how to get there, and the machines the customer
    # reported for this task (from their portal ticket).
    site_contact_name = serializers.CharField(source='site.effective_contact_name', default='')
    site_contact_phone = serializers.CharField(source='site.effective_contact_phone', default='')
    directions_url = serializers.SerializerMethodField()
    machines = serializers.SerializerMethodField()
    # The task's machines as photo cards (tasks.views.task_machine_cards): every screen shows these.
    machine_cards = serializers.SerializerMethodField()
    # The spare parts for the visit (tasks.parts_needed), and whether the warehouse prepared them.
    parts_needed = serializers.SerializerMethodField()
    parts_prepared = serializers.SerializerMethodField()
    delivery_note_url = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'task_number', 'site_name', 'site_address', 'customer_name', 'currency_code',
            'site_contact_name', 'site_contact_phone', 'directions_url', 'machines', 'machine_cards', 'scheduled_date',
            'parts_needed', 'parts_prepared', 'delivery_note_url',
            'status', 'status_display', 'priority', 'priority_display',
            'task_type_name', 'brand_name', 'about', 'about_display', 'done_by', 'subcontractor_name',
            'description',
            'reported_at', 'promised_at', 'scheduled_for', 'estimated_hours',
            'events', 'attachments', 'next_action', 'shipment', 'from_request', 'is_lead', 'can_file_report',
            'created_by', 'last_changed',
            'requires_signature', 'undoable_step',
        ]

    def get_last_changed(self, obj):
        from spots.audit import trail, who
        changed = trail(obj)['changed']
        return {'name': who(changed), 'role': '', 'at': timezone.localtime(changed.at).isoformat()} if changed else None

    def get_created_by(self, obj):
        creator = obj.creator()
        if creator['at']:
            creator['at'] = timezone.localtime(creator['at']).isoformat()
        return creator

    def get_events(self, obj):
        """The history, everyone's name fetched with it. A negligence flag is a
        manager-tier record: never shown to the technician flagged or to a
        supervisor (as on the website's task page) — hidden unless the screen
        says this viewer is manager tier (context "show_negligence")."""
        events = obj.events.select_related('actor__technician').order_by('occurred_at')
        if not self.context.get('show_negligence'):
            events = events.exclude(event_type=TaskEvent.EventType.NEGLIGENCE)
        return TaskEventSerializer(events, many=True).data

    def get_next_action(self, obj):
        return self.context.get('next_action')

    def get_from_request(self, obj):
        # Everything from the customer's request (tasks.views.request_for_app); None for any other task.
        return self.context.get('from_request')

    def get_shipment(self, obj):
        # A part shipment's own steps (tasks.views.shipment_for_app); None for any other task.
        return self.context.get('shipment')

    def get_directions_url(self, obj):
        return directions_url(obj.site)

    def get_machines(self, obj):
        return [
            {
                'name': machine.machine_name, 'serial': machine.serial,
                'problem': machine.get_problem_display(), 'description': machine.description,
            }
            for machine in obj.ticket_machines.all()
        ]

    def get_parts_needed(self, obj):
        from tasks.parts_needed import part_rows

        return [
            {'id': row['line'].pk, 'code': row['line'].product_code, 'description': row['line'].description,
             'quantity': row['line'].quantity, 'state': row['state'],
             'expected_on': row['line'].expected_on.isoformat() if row['line'].expected_on else None}
            for row in part_rows(obj)
        ]

    def get_parts_prepared(self, obj):
        return obj.parts_prepared_at is not None

    def get_delivery_note_url(self, obj):
        request = self.context.get('request')
        if not obj.delivery_note:
            return ''
        return signed_media_url(request, obj.delivery_note.url) if request else obj.delivery_note.url

    def get_machine_cards(self, obj):
        from tasks.views import task_machine_cards

        return [
            {
                'asset_id': card['asset'].pk if card['asset'] else None, 'registered': card['registered'],
                # The customer's report for it (from_request.machines with this id).
                'ticket_machine_id': card['ticket_machine'].pk if card['ticket_machine'] else None,
                'name': card['name'], 'serial_no': card['serial_no'],
                'photos': TaskAttachmentSerializer(card['photos'], many=True, context=self.context).data,
                # The customer's photos it can still use ("Use this"), each in its slot.
                'customer_files': [
                    {'name': item['file'].name, 'kind': item['kind'], 'slot': item['slot'],
                     'added_by_staff': item['added_by_staff'],
                     'url': signed_media_url(self.context['request'], item['file'].url) if 'request' in self.context
                     else item['file'].url}
                    for item in card['customer_files']
                ],
            }
            for card in task_machine_cards(obj)
        ]

    def get_is_lead(self, obj):
        return self.context.get('is_lead', False)

    def get_can_file_report(self, obj):
        return self.context.get('can_file_report', False)

    def get_requires_signature(self, obj):
        return self.context.get('requires_signature', False)

    def get_undoable_step(self, obj):
        """The display name of the lead's own last tap if they can still
        undo it (tasks.views.undoable_tap), else null.
        """
        event = self.context.get('undoable_tap')
        return event.get_event_type_display() if event else None


class CustomerTicketSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source='country.display_name')
    status_display = serializers.CharField(source='get_status_display')

    class Meta:
        model = CustomerTicket
        fields = [
            'id', 'ticket_number', 'company_name', 'site_description', 'country_name', 'status', 'status_display',
            'contact_name', 'contact_phone', 'description', 'submitted_at',
        ]


class PartUsedSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartUsed
        fields = ['part_code', 'description', 'quantity']


class WorkReportSerializer(serializers.ModelSerializer):
    parts_used = PartUsedSerializer(many=True, read_only=True)
    signature_url = serializers.SerializerMethodField()

    def get_signature_url(self, obj):
        request = self.context.get('request')
        return signed_media_url(request, obj.signature_url) if request else obj.signature_url

    class Meta:
        model = WorkReport
        fields = [
            'findings', 'action_taken', 'resolved', 'labour_hours', 'customer_name',
            'signature_url', 'signature_waived_reason', 'submitted_at', 'parts_used', 'copy_sent_to', 'copy_sent_at',
            'work_lines', 'recommendations', 'recommendation',
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
    # Who reviewed and who approved the report, and whose turn it is (Task.approvals).
    approvals = serializers.SerializerMethodField()
    responsible_supervisor_name = serializers.CharField(source='responsible_supervisor.full_name', default='')
    can_assign = serializers.SerializerMethodField()
    can_supervisor_approve = serializers.SerializerMethodField()
    can_manager_approve = serializers.SerializerMethodField()

    class Meta(TaskDetailSerializer.Meta):
        fields = TaskDetailSerializer.Meta.fields + [
            'lead', 'helpers', 'report', 'approvals', 'responsible_supervisor_name',
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

    def get_approvals(self, obj):
        found = obj.approvals()
        for key in ('reviewed', 'approved'):
            if found[key]:
                found[key] = {**found[key], 'at': timezone.localtime(found[key]['at']).isoformat()}
        return found

    def get_report(self, obj):
        report = getattr(obj, 'report', None)
        return WorkReportSerializer(report).data if report else None

    def get_can_assign(self, obj):
        return self.context.get('can_assign', False)

    def get_can_supervisor_approve(self, obj):
        return self.context.get('can_supervisor_approve', False)

    def get_can_manager_approve(self, obj):
        return self.context.get('can_manager_approve', False)
