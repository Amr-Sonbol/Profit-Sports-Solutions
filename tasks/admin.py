from django.contrib import admin
from django.utils.html import format_html

from .models import (
    CustomerTicket, CustomerTicketAttachment, Task, TaskAsset, TaskAssignment, TaskAttachment, TaskEvent,
)


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
        'reported_at', 'promised_at', 'scheduled_for', 'estimated_hours', 'schedule_notified_at',
    ]
    search_fields = ['task_number', 'site__name', 'description']
    list_filter = ['status', 'priority', 'billing_type', 'source', 'is_warranty']
    inlines = [TaskAssignmentInline, TaskAttachmentInline, TaskAssetInline]


class TaskDocument(Task):
    """A proxy for Task — same table, same rows — so the paperwork trail
    (quotation/factory offer/invoice) gets its own browsable tab in the
    admin instead of being three easy-to-miss fields on every task's
    change form. Never a separate model to keep in sync.
    """

    class Meta:
        proxy = True
        verbose_name = 'task document'
        verbose_name_plural = 'task documents'


@admin.register(TaskDocument)
class TaskDocumentAdmin(admin.ModelAdmin):
    list_display = ['task_number', 'site', 'quotation_link', 'factory_offer_link', 'invoice_link']
    search_fields = ['task_number', 'site__name']
    list_filter = ['site__customer__country']
    readonly_fields = ['task_number', 'site']
    fields = ['task_number', 'site', 'quotation', 'factory_offer', 'invoice']

    def get_queryset(self, request):
        # Only tasks with at least one document on file — a first upload
        # still happens from the task's own edit screen (in-app or the
        # main Task admin page), not created here.
        return super().get_queryset(request).exclude(quotation='', factory_offer='', invoice='')

    def has_add_permission(self, request):
        return False

    def _file_link(self, file_field):
        if not file_field:
            return '—'
        return format_html('<a href="{}" target="_blank" rel="noopener">View</a>', file_field.url)

    def quotation_link(self, obj):
        return self._file_link(obj.quotation)
    quotation_link.short_description = 'Quotation'

    def factory_offer_link(self, obj):
        return self._file_link(obj.factory_offer)
    factory_offer_link.short_description = 'Factory offer'

    def invoice_link(self, obj):
        return self._file_link(obj.invoice)
    invoice_link.short_description = 'Invoice'


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


class CustomerTicketAttachmentInline(admin.TabularInline):
    model = CustomerTicketAttachment
    extra = 0


@admin.register(CustomerTicket)
class CustomerTicketAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'site_description', 'country', 'status', 'assigned_to', 'submitted_at']
    search_fields = ['company_name', 'site_description', 'contact_name', 'contact_phone']
    list_filter = ['country', 'status']
    inlines = [CustomerTicketAttachmentInline]
