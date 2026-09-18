from django.contrib import admin

from .models import CustomerFeedback, PartUsed, WorkReport


class PartUsedInline(admin.TabularInline):
    model = PartUsed
    extra = 0


@admin.register(WorkReport)
class WorkReportAdmin(admin.ModelAdmin):
    list_display = ['task', 'resolved', 'customer_name', 'submitted_at']
    search_fields = ['task__task_number', 'customer_name']
    list_filter = ['resolved']
    inlines = [PartUsedInline]


@admin.register(PartUsed)
class PartUsedAdmin(admin.ModelAdmin):
    list_display = ['report', 'part_code', 'quantity', 'unit_cost', 'currency_code']
    search_fields = ['part_code', 'report__task__task_number']


@admin.register(CustomerFeedback)
class CustomerFeedbackAdmin(admin.ModelAdmin):
    list_display = ['task', 'rating', 'requested_at', 'requested_by', 'submitted_at']
    search_fields = ['task__task_number']
    list_filter = ['rating']
    readonly_fields = ['token']
