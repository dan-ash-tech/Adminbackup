from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.db import models
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from .forms import CasePhoneNumberInlineFormSet
from .models import (
	CasePhoneNumber,
	CaseRecord,
	CaseReport,
	TargetMonitoringPeriod,
)


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class NonDeletableUserAdmin(UserAdmin):
	"""Users are deactivated, never deleted: LogEntry rows cascade with them."""

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
	verbose_name_plural = "Target record activity"
	list_display = (
		"action_time",
		"user",
		"object_repr",
		"get_action_flag_display",
		"change_message",
	)
	list_filter = ("action_flag", "content_type", "action_time")
	search_fields = ("user__username", "object_repr", "change_message")
	date_hierarchy = "action_time"
	ordering = ("-action_time",)
	readonly_fields = (
		"action_time",
		"user",
		"content_type",
		"object_id",
		"object_repr",
		"action_flag",
		"change_message",
	)

	def get_queryset(self, request):
		return super().get_queryset(request).filter(
			content_type__app_label="Adminbackup",
			content_type__model="caserecord",
		)

	def has_view_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


class CaseMonitoringFilter(admin.SimpleListFilter):
	title = "monitoring status"
	parameter_name = "monitoring_status"
	template = "admin/monitoring_status_filter.html"

	def lookups(self, request, model_admin):
		return (
			("current", "Current monitored"),
			("stopped", "Stopped from the system"),
		)

	def queryset(self, request, queryset):
		if self.value() == "current":
			return queryset.current_monitored()
		if self.value() == "stopped":
			return queryset.stopped()
		return queryset


class CasePhoneNumberInline(admin.TabularInline):
	model = CasePhoneNumber
	formset = CasePhoneNumberInlineFormSet
	fields = ("label", "number")
	can_delete = True
	extra = 1


class CaseReportInline(admin.TabularInline):
	model = CaseReport
	fields = ("file",)
	extra = 1


class TargetMonitoringPeriodInline(admin.TabularInline):
	model = TargetMonitoringPeriod
	fields = ("started_on", "terminated_on", "remarks")
	extra = 1


@admin.register(CaseRecord)
class CaseRecordAdmin(admin.ModelAdmin):
	formfield_overrides = {
		models.TextField: {"widget": forms.Textarea(attrs={"rows": 5})},
	}
	list_display = (
		"serial_number",
		"code",
		"assigned_users_display",
		"first_name",
		"last_name",
		"monitoring_status",
		"personal_identification_link",
		"phone_numbers_display",
		"authorizer",
		"case_type",
		"personal_identifications",
		"attached_document",
		"start_date",
		"first_termination_date",
		"reprovisioned_date",
		"second_termination_date",
		"remarks",
	)
	list_filter = (
		CaseMonitoringFilter,
		"case_type",
		"start_date",
	)
	list_editable = (
		"first_termination_date",
		"reprovisioned_date",
		"second_termination_date",
	)
	search_fields = (
		"serial_number",
		"code",
		"assigned_users__username",
		"assigned_user__username",
		"first_name",
		"last_name",
		"phone_numbers__number",
		"phone_numbers__label",
		"authorizer",
		"remarks",
	)
	inlines = (
		CasePhoneNumberInline,
		CaseReportInline,
		TargetMonitoringPeriodInline,
	)
	readonly_fields = ("serial_number", "personal_identification_preview")
	exclude = ("reprovisioned", "assigned_user")
	filter_horizontal = ("assigned_users",)

	def get_queryset(self, request):
		return super().get_queryset(request).visible_to(request.user).prefetch_related(
			"phone_numbers", "assigned_users"
		).select_related("assigned_user")

	def get_readonly_fields(self, request, obj=None):
		readonly_fields = list(super().get_readonly_fields(request, obj))
		if not request.user.is_superuser:
			readonly_fields.append("assigned_users")
		return tuple(readonly_fields)

	def save_model(self, request, obj, form, change):
		obj.assigned_user = None
		super().save_model(request, obj, form, change)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		TargetMonitoringPeriod.objects.get_or_create(
			target=form.instance,
			defaults={"started_on": form.instance.start_date},
		)

	def response_add(self, request, obj, post_url_continue=None):
		messages.success(request, "Target added successfully.")
		return redirect("dashboard")

	def response_change(self, request, obj):
		messages.success(request, "Target updated successfully.")
		return redirect("dashboard")

	@admin.display(description="Targets and reprovisioning")
	def phone_numbers_display(self, obj):
		return ", ".join(
			f"{phone.label}: {phone.number}" if phone.label else phone.number
			for phone in obj.phone_numbers.all()
		)

	@admin.display(description="Assigned users")
	def assigned_users_display(self, obj):
		return obj.assigned_users_display
  
	@admin.display(description="Monitoring status")
	def monitoring_status(self, obj):
		return obj.monitoring_status

	def _personal_identification_url(self, obj):
		filename = obj.personal_identifications.name.removeprefix(
			"personal_identifications/"
		)
		return reverse(
			"private-identification-media",
			kwargs={"filename": filename},
		)

	@admin.display(description="Personal identification")
	def personal_identification_link(self, obj):
		if not obj.personal_identifications:
			return "Not uploaded"
		return format_html(
			'<a href="{}" target="_blank" rel="noopener">View image</a>',
			self._personal_identification_url(obj),
		)

	@admin.display(description="Identification preview")
	def personal_identification_preview(self, obj):
		if not obj or not obj.personal_identifications:
			return "No image uploaded"
		url = self._personal_identification_url(obj)
		return format_html(
			'<a href="{}" target="_blank" rel="noopener">'
			'<img src="{}" alt="Personal identification" '
			'style="max-width: 280px; max-height: 200px;">'
			"</a>",
			url,
			url,
		)
