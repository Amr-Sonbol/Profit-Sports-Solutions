from django.contrib import admin

from .models import CustomerTicket, Task, TaskAsset, TaskAssignment, TaskAttachment, TaskEvent


class TaskAssignmentInline(admin.TabularInline):
    model = TaskAssignment
    extra = 0


class TaskAttachmentInline(admin.TabularInline):
    model = TaskAttachment
    extra = 0


class TaskAssetInline(admin.TabularInline):
    model = TaskAsset
    extra = 0


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        'task_number', 'site', 'status', 'priority', 'billing_type',
        'reported_at', 'promised_at', 'scheduled_for', 'estimated_hours',
    ]
    search_fields = ['task_number', 'site__name', 'description']
    list_filter = ['status', 'priority', 'billing_type', 'source', 'is_warranty']
    inlines = [TaskAssignmentInline, TaskAttachmentInline, TaskAssetInline]


@admin.register(TaskAssignment)
class TaskAssignmentAdmin(admin.ModelAdmin):
    list_display = ['task', 'technician', 'role', 'assigned_at', 'is_active']
    search_fields = ['task__task_number', 'technician__full_name']
    list_filter = ['role', 'is_active']


@admin.register(TaskEvent)
class TaskEventAdmin(admin.ModelAdmin):
    list_display = ['task', 'event_type', 'occurred_at', 'actor']
    search_fields = ['task__task_number']
    list_filter = ['event_type']


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ['task', 'purpose', 'media_type', 'source', 'uploaded_at']
    search_fields = ['task__task_number']
    list_filter = ['purpose', 'media_type', 'source']


@admin.register(TaskAsset)
class TaskAssetAdmin(admin.ModelAdmin):
    list_display = ['task', 'asset', 'outcome']
    search_fields = ['task__task_number']
    list_filter = ['outcome']


@admin.register(CustomerTicket)
class CustomerTicketAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'site_description', 'country', 'status', 'submitted_at']
    search_fields = ['company_name', 'site_description', 'contact_name', 'contact_phone']
    list_filter = ['country', 'status']
