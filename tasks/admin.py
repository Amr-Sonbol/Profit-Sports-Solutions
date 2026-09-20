from django.contrib import admin
from django.db import models
from django.urls import reverse
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


def _has_file(field_name):
    """A task where this field is genuinely set — not '' and not NULL.
    Some rows predate these columns and hold NULL, and NULL never equals
    '' in SQL, so a plain exclude(field='') silently lets those through.
    """
    return ~models.Q(**{field_name: ''}) & models.Q(**{f'{field_name}__isnull': False})


def _file_actions(file_field):
    """View (opens in a new tab) and Download (forces save-as) — the
    same file, two ways to open it.
    """
    if not file_field:
        return '—'
    return format_html(
        '<a href="{0}" target="_blank" rel="noopener">View</a> | <a href="{0}" download>Download</a>',
        file_field.url,
    )


def _task_link(obj):
    """The task itself, one click away — every document belongs to a
    task, and browsing documents is exactly when you want to jump to it.
    """
    return format_html('<a href="{}">{}</a>', reverse('tasks:task_detail', args=[obj.pk]), obj.task_number)


class TaskQuotation(Task):
    """A proxy for Task — same table, same rows — so quotations get
    their own browsable tab instead of being one easy-to-miss field on
    every task's change form. Never a separate model to keep in sync.
    """

    class Meta:
        proxy = True
        verbose_name = 'quotation'
        verbose_name_plural = 'quotations'


@admin.register(TaskQuotation)
class TaskQuotationAdmin(admin.ModelAdmin):
    list_display = ['task_link', 'site', 'quotation_uploaded_at', 'quotation_actions']
    search_fields = ['task_number', 'site__name']
    list_filter = ['site__customer__country']
    date_hierarchy = 'quotation_uploaded_at'
    readonly_fields = ['task_number', 'site', 'quotation_uploaded_at']
    fields = ['task_number', 'site', 'quotation', 'quotation_uploaded_at']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(_has_file('quotation'))

    def has_add_permission(self, request):
        return False

    def task_link(self, obj):
        return _task_link(obj)
    task_link.short_description = 'Task'
    task_link.admin_order_field = 'task_number'

    def quotation_actions(self, obj):
        return _file_actions(obj.quotation)
    quotation_actions.short_description = 'Quotation'


class TaskFactoryOffer(Task):
    class Meta:
        proxy = True
        verbose_name = 'factory offer'
        verbose_name_plural = 'factory offers'


@admin.register(TaskFactoryOffer)
class TaskFactoryOfferAdmin(admin.ModelAdmin):
    list_display = ['task_link', 'site', 'factory_offer_uploaded_at', 'factory_offer_actions']
    search_fields = ['task_number', 'site__name']
    list_filter = ['site__customer__country']
    date_hierarchy = 'factory_offer_uploaded_at'
    readonly_fields = ['task_number', 'site', 'factory_offer_uploaded_at']
    fields = ['task_number', 'site', 'factory_offer', 'factory_offer_uploaded_at']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(_has_file('factory_offer'))

    def has_add_permission(self, request):
        return False

    def task_link(self, obj):
        return _task_link(obj)
    task_link.short_description = 'Task'
    task_link.admin_order_field = 'task_number'

    def factory_offer_actions(self, obj):
        return _file_actions(obj.factory_offer)
    factory_offer_actions.short_description = 'Factory offer'


class TaskInvoice(Task):
    class Meta:
        proxy = True
        verbose_name = 'invoice'
        verbose_name_plural = 'invoices'


@admin.register(TaskInvoice)
class TaskInvoiceAdmin(admin.ModelAdmin):
    list_display = ['task_link', 'site', 'invoice_uploaded_at', 'invoice_actions']
    search_fields = ['task_number', 'site__name']
    list_filter = ['site__customer__country']
    date_hierarchy = 'invoice_uploaded_at'
    readonly_fields = ['task_number', 'site', 'invoice_uploaded_at']
    fields = ['task_number', 'site', 'invoice', 'invoice_uploaded_at']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(_has_file('invoice'))

    def has_add_permission(self, request):
        return False

    def task_link(self, obj):
        return _task_link(obj)
    task_link.short_description = 'Task'
    task_link.admin_order_field = 'task_number'

    def invoice_actions(self, obj):
        return _file_actions(obj.invoice)
    invoice_actions.short_description = 'Invoice'


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
