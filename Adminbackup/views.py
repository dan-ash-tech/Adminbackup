from io import BytesIO
from mimetypes import guess_type
from datetime import timedelta
from urllib.parse import urlencode

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from axes.models import AccessAttempt

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import (
	FileResponse,
	Http404,
	HttpResponse,
	HttpResponseBadRequest,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
	AdminPasswordResetForm,
	CasePhoneNumberEditFormSet,
	CasePhoneNumberManagementFormSet,
	CasePhoneNumberFormSet,
	CaseRecordEditorForm,
	CaseRecordIntakeForm,
	CaseReportFormSet,
	CaseReportManagementFormSet,
	TargetMonitoringPeriodFormSet,
	ManagedUserForm,
	ViewerUserCreationForm,
)
from .models import (
	CaseRecord,
	CaseReport,
	TargetMonitoringPeriod,
	can_view_all_records,
)


DEFAULT_DASHBOARD_TEXT = {
	"title": "Target Monitoring System",
	"message": "",
}

TARGETS_PER_PAGE = 25
DASHBOARD_EXPORT_COLUMNS = (
	("serial_number", "S/N", 10),
	("code", "Call-sign", 18),
	("first_name", "First name", 18),
	("last_name", "Last name", 18),
	("phone_number", "Phone number", 30),
	("authorizer", "Authorizer", 20),
	("case_type", "Case type", 30),
	("user", "User", 20),
	("status", "Status", 24),
	("start_date", "Start date", 14),
	("image", "Image", 28),
	("reports", "Reports", 42),
	("remarks", "Remarks", 40),
	("monitoring_history", "Monitoring history", 42),
)

# Edit each account's title and message here. Add new usernames as accounts are created.
USER_DASHBOARD_TEXT = {
	"passy": {
		"title": "Target Monitoring",
		"message": (
			"View target records by monitoring status. "
			
		),
	},
	"user1": {
		"title": "Target Monitoring",
		"message": (
			
			"FDRL Agents And their ASSOCIATES."
		),
	},
	"user2": {
		"title": "Target Monitoring",
		"message": (
			"criminal cases only. "
		),
	},
	"user3": {
		"title": "Target Monitoring",
		"message": (
		
			"SPECIAL TARGETS AND OTHER CRIME CASES AND RELATED CASES."
		),
	},
 "admin2": {
		"title": "SPECIAL TARGETS AND OTHER CRIME CASES AND RELATED CASES",
		"message": (
		
			 ""
		),
	},
    "user4": {
			"title": "Target Monitoring",
			"message": (
			
				"RUDI,CNRD,FLN & OTHER RELATED ENEMIES."
			),
		},
}


def staff_can_add_cases(user):
	return user.is_staff and user.has_perm("Adminbackup.add_caserecord")


TARGET_EDITOR_PERMISSIONS = (
	"add_caserecord",
	"change_caserecord",
	"delete_caserecord",
	"view_caserecord",
	"add_casephonenumber",
	"change_casephonenumber",
	"delete_casephonenumber",
)
VIEW_ALL_RECORDS_PERMISSIONS = ("view_caserecord",)


def _apply_user_role(user, role):
	with transaction.atomic():
		user.is_staff = role in (
			"view_all_records",
			"target_editor",
		)
		user.save(update_fields=["is_staff"])
		if role == "target_editor":
			permissions = Permission.objects.filter(
				content_type__app_label="Adminbackup",
				codename__in=TARGET_EDITOR_PERMISSIONS,
			)
			if permissions.count() != len(TARGET_EDITOR_PERMISSIONS):
				raise Permission.DoesNotExist(
					"Target-editor permissions are not fully configured."
				)
			user.user_permissions.set(permissions)
		elif role == "view_all_records":
			permissions = Permission.objects.filter(
				content_type__app_label="Adminbackup",
				codename__in=VIEW_ALL_RECORDS_PERMISSIONS,
			)
			if permissions.count() != len(VIEW_ALL_RECORDS_PERMISSIONS):
				raise Permission.DoesNotExist(
					"View-all-records permission is not configured."
				)
			user.user_permissions.set(permissions)
		else:
			user.user_permissions.clear()


def _dashboard_case_queryset(user):
	return CaseRecord.objects.visible_to(user)


def _can_edit_targets(user):
	return (
		user.is_staff
		and user.has_perm("Adminbackup.change_caserecord")
		and user.has_perm("Adminbackup.add_casephonenumber")
		and user.has_perm("Adminbackup.change_casephonenumber")
		and user.has_perm("Adminbackup.delete_casephonenumber")
	)


def _can_edit_phone_numbers_only(user):
	return (
		user.is_staff
		and user.has_perm("Adminbackup.change_casephonenumber")
		and not user.has_perm("Adminbackup.change_caserecord")
	)


def _can_delete_targets(user):
	return user.is_staff and user.has_perm("Adminbackup.delete_caserecord")


def _log_target_action(user, case_record, action_flag, message):
	LogEntry.objects.log_actions(
		user_id=user.pk,
		queryset=[case_record],
		action_flag=action_flag,
		change_message=message,
		single_object=True,
	)


def _log_user_action(actor, target_user, action_flag, message):
	LogEntry.objects.log_actions(
		user_id=actor.pk,
		queryset=[target_user],
		action_flag=action_flag,
		change_message=message,
		single_object=True,
	)


ACTIVITY_ACTIONS = (
	(str(ADDITION), "Added"),
	(str(CHANGE), "Changed"),
	(str(DELETION), "Deleted"),
)


@login_required
@require_GET
def activity_logs(request):
	if not request.user.is_superuser:
		raise PermissionDenied

	all_logs = LogEntry.objects.select_related("user", "content_type")
	logs = all_logs
	search_query = request.GET.get("q", "").strip()
	selected_user = request.GET.get("user", "").strip()
	selected_action = request.GET.get("action", "").strip()
	date_from_value = request.GET.get("date_from", "").strip()
	date_to_value = request.GET.get("date_to", "").strip()

	if search_query:
		logs = logs.filter(
			Q(user__username__icontains=search_query)
			| Q(object_repr__icontains=search_query)
			| Q(change_message__icontains=search_query)
			| Q(content_type__model__icontains=search_query)
		)
	if selected_user.isdigit():
		logs = logs.filter(user_id=int(selected_user))
	else:
		selected_user = ""
	if selected_action in {str(flag) for flag, _label in ACTIVITY_ACTIONS}:
		logs = logs.filter(action_flag=int(selected_action))
	else:
		selected_action = ""

	date_from = parse_date(date_from_value)
	date_to = parse_date(date_to_value)
	if date_from:
		logs = logs.filter(action_time__date__gte=date_from)
	else:
		date_from_value = ""
	if date_to:
		logs = logs.filter(action_time__date__lte=date_to)
	else:
		date_to_value = ""

	logs = logs.order_by("-action_time", "-pk")
	today = timezone.localdate()
	recent_cutoff = timezone.now() - timedelta(days=7)
	users = get_user_model().objects.filter(
		pk__in=LogEntry.objects.values("user_id")
	).order_by("username")
	page_obj = Paginator(logs, 25).get_page(request.GET.get("page"))

	return render(
		request,
		"case_portal/activity_logs.html",
		{
			"activity_logs": page_obj,
			"page_obj": page_obj,
			"activity_users": users,
			"activity_actions": ACTIVITY_ACTIONS,
			"search_query": search_query,
			"selected_user": selected_user,
			"selected_action": selected_action,
			"date_from": date_from_value,
			"date_to": date_to_value,
			"activity_total": all_logs.count(),
			"activity_today": all_logs.filter(
				action_time__date=today
			).count(),
			"activity_recent_changes": all_logs.filter(
				action_flag=CHANGE,
				action_time__gte=recent_cutoff,
			).count(),
		},
	)


@login_required
@require_GET
def dashboard(request):
	if request.user.is_superuser:
		current_target_count = CaseRecord.objects.current_monitored().count()
		stopped_target_count = CaseRecord.objects.stopped().count()
	else:
		current_target_count = None
		stopped_target_count = None

	cases = _dashboard_case_queryset(request.user)
	status = request.GET.get("status", "current")
	if status == "stopped":
		cases = cases.stopped().order_by(
			Coalesce("second_termination_date", "first_termination_date"),
			"pk",
		)
		status = "stopped"
	else:
		cases = cases.current_monitored().order_by("start_date", "pk")
		status = "current"

	search_query = request.GET.get("q", "").strip()
	if search_query:
		matches = (
			Q(serial_number__icontains=search_query)
			| Q(code__icontains=search_query)
			| Q(first_name__icontains=search_query)
			| Q(last_name__icontains=search_query)
			| Q(phone_numbers__number__icontains=search_query)
			| Q(phone_numbers__label__icontains=search_query)
			| Q(authorizer__icontains=search_query)
			| Q(case_type__icontains=search_query)
			| Q(remarks__icontains=search_query)
			| Q(assigned_users__username__icontains=search_query)
			| Q(assigned_user__username__icontains=search_query)
			| Q(personal_identifications__icontains=search_query)
			| Q(attached_document__icontains=search_query)
			| Q(attached_reports__file__icontains=search_query)
		)
		search_date = parse_date(search_query)
		if search_date:
			matches |= (
				Q(start_date=search_date)
				| Q(first_termination_date=search_date)
				| Q(reprovisioned_date=search_date)
				| Q(second_termination_date=search_date)
				| Q(monitoring_periods__started_on=search_date)
				| Q(monitoring_periods__terminated_on=search_date)
			)
		cases = cases.filter(matches).distinct()

	visible_optional_columns = {
		"phone_numbers": cases.filter(phone_numbers__isnull=False).exists(),
		"authorizer": cases.exclude(authorizer="").exists(),
		"case_type": cases.exclude(case_type="").exists(),
		"user": cases.filter(
			Q(assigned_users__isnull=False) | Q(assigned_user__isnull=False)
		).exists(),
		# Monitoring history replaces the legacy first/reprovisioned/second
		# termination columns in the dashboard table.
		"first_termination": False,
		"reprovisioned": False,
		"second_termination": False,
		"monitoring_history": cases.filter(
			monitoring_periods__isnull=False
		).exists(),
		"remarks": status == "stopped" and cases.exclude(remarks="").exists(),
		"image": cases.exclude(personal_identifications="").exists(),
		"report": (
			cases.exclude(attached_document="").exists()
			or cases.filter(attached_reports__isnull=False).exists()
		),
	}

	cases = cases.prefetch_related(
		"assigned_users",
		"phone_numbers",
		"attached_reports",
		"monitoring_periods",
	)
	page_obj = Paginator(cases, TARGETS_PER_PAGE).get_page(
		request.GET.get("page")
	)
	dashboard_text = USER_DASHBOARD_TEXT.get(
		request.user.get_username().casefold(),
		DEFAULT_DASHBOARD_TEXT,
	)
	return render(
		request,
		"case_portal/dashboard.html",
		{
			"cases": page_obj.object_list,
			"page_obj": page_obj,
			"status": status,
			"search_query": search_query,
			"visible_optional_columns": visible_optional_columns,
			"table_column_count": 6 + sum(visible_optional_columns.values()) + 1,
			"dashboard_title": dashboard_text["title"],
			"dashboard_message": dashboard_text["message"],
			"export_columns": [
				(key, label) for key, label, _width in DASHBOARD_EXPORT_COLUMNS
			],
			"show_target_totals": request.user.is_superuser,
			"current_target_count": current_target_count,
			"stopped_target_count": stopped_target_count,
			"can_edit_targets": _can_edit_targets(request.user),
			"can_edit_phone_numbers": _can_edit_phone_numbers_only(request.user),
			"can_delete_targets": _can_delete_targets(request.user),
		},
	)


@login_required
@require_GET
def view_target(request, case_id):
	case_record = get_object_or_404(
		_dashboard_case_queryset(request.user)
		.prefetch_related(
			"assigned_users",
			"phone_numbers",
			"attached_reports",
			"monitoring_periods",
		),
		pk=case_id,
	)
	status = request.GET.get("status", "current")
	if status not in {"current", "stopped"}:
		status = "current"
	search_query = request.GET.get("q", "").strip()
	return render(
		request,
		"case_portal/view_target.html",
		{
			"case_record": case_record,
			"can_edit_targets": _can_edit_targets(request.user),
			"can_edit_phone_numbers": _can_edit_phone_numbers_only(request.user),
			"can_delete_targets": _can_delete_targets(request.user),
			"status": status,
			"search_query": search_query,
		},
	)


@login_required
@require_GET
def export_dashboard_targets(request):
	export_status = request.GET.get("export_status", "all")
	if export_status not in {"all", "current", "stopped"}:
		return HttpResponseBadRequest("Choose all, current, or stopped targets.")

	requested_columns = request.GET.getlist("columns")
	valid_column_keys = {key for key, _label, _width in DASHBOARD_EXPORT_COLUMNS}
	if request.GET.get("columns_selected"):
		if any(key not in valid_column_keys for key in requested_columns):
			return HttpResponseBadRequest("Choose valid export columns.")
		selected_keys = set(requested_columns)
		if not selected_keys:
			return HttpResponseBadRequest("Choose at least one export column.")
	else:
		selected_keys = valid_column_keys
	selected_columns = [
		column for column in DASHBOARD_EXPORT_COLUMNS if column[0] in selected_keys
	]

	cases = _dashboard_case_queryset(request.user)
	if export_status == "current":
		cases = cases.current_monitored()
	elif export_status == "stopped":
		cases = cases.stopped()

	cases = (
		cases
		.select_related("assigned_user")
		.prefetch_related(
			"assigned_users",
			"phone_numbers",
			"attached_reports",
			"monitoring_periods",
		)
		.order_by("pk")
	)

	workbook = Workbook(write_only=True)
	worksheet = workbook.create_sheet("Targets")
	headers = [label for _key, label, _width in selected_columns]
	header_cells = []
	for value in headers:
		cell = WriteOnlyCell(worksheet, value=value)
		cell.font = Font(bold=True)
		header_cells.append(cell)
	worksheet.append(header_cells)
	row_count = 1

	for case in cases:
		phone_numbers = list(case.phone_numbers.all()) or [None]
		reports = [
			report.file.name.rsplit("/", 1)[-1]
			for report in case.attached_reports.all()
		]
		if case.attached_document:
			reports.insert(0, case.attached_document.name.rsplit("/", 1)[-1])

		for phone_index, phone in enumerate(phone_numbers):
			phone_number = ""
			if phone:
				phone_number = (
					f"{phone.label}: {phone.number}" if phone.label else phone.number
				)
			if phone_index == 0:
				all_values = {
					"serial_number": case.serial_number,
					"code": case.code,
					"first_name": case.first_name,
					"last_name": case.last_name,
					"phone_number": phone_number,
					"authorizer": case.authorizer,
					"case_type": case.case_type,
					"user": case.assigned_users_display,
					"status": case.monitoring_status,
					"start_date": case.start_date,
					"image": case.personal_identifications.name.rsplit("/", 1)[-1]
					if case.personal_identifications else "",
					"reports": "; ".join(reports),
					"remarks": case.remarks,
					"monitoring_history": case.monitoring_periods_display,
				}
				values = [all_values[key] for key, _label, _width in selected_columns]
			else:
				values = [
					phone_number if key == "phone_number" else None
					for key, _label, _width in selected_columns
				]

			cells = []
			for value in values:
				cell = WriteOnlyCell(worksheet, value=value)
				if isinstance(value, str):
					cell.data_type = "s"
				cells.append(cell)
			worksheet.append(cells)
			row_count += 1

	worksheet.freeze_panes = "A2"
	last_column = get_column_letter(len(selected_columns))
	worksheet.auto_filter.ref = f"A1:{last_column}{row_count}"
	for index, (_key, _label, width) in enumerate(selected_columns, start=1):
		worksheet.column_dimensions[get_column_letter(index)].width = width

	output = BytesIO()
	workbook.save(output)
	response = HttpResponse(
		output.getvalue(),
		content_type=(
			"application/vnd.openxmlformats-officedocument."
			"spreadsheetml.sheet"
		),
	)
	response["Content-Disposition"] = (
		f'attachment; filename="target-records-{export_status}.xlsx"'
	)
	return response


@login_required
@require_http_methods(["GET", "POST"])
def create_users(request):
	if not request.user.is_superuser:
		raise PermissionDenied

	if request.method == "POST":
		user_form = ViewerUserCreationForm(request.POST)
		if user_form.is_valid():
			with transaction.atomic():
				user = user_form.save()
				_apply_user_role(user, user_form.cleaned_data["role"])
			_log_user_action(
				request.user,
				user,
				ADDITION,
				"User account created from the management dashboard.",
			)
			messages.success(request, "User account created.")
			return redirect("manage-users")
	else:
		user_form = ViewerUserCreationForm()

	return render(
		request,
		"case_portal/create_users.html",
		{"user_form": user_form},
	)


def _managed_viewer_or_404(user_id):
	return get_object_or_404(
		get_user_model(),
		pk=user_id,
		is_superuser=False,
	)


@login_required
@require_GET
def manage_users(request):
	if not request.user.is_superuser:
		raise PermissionDenied

	locked_usernames = set(
		AccessAttempt.objects.filter(
			failures_since_start__gte=settings.AXES_FAILURE_LIMIT
		).values_list("username", flat=True)
	)

	managed_users = list(
		get_user_model().objects.order_by("-is_superuser", "username")
	)
	for managed_user in managed_users:
		if managed_user.is_superuser:
			managed_user.account_role = "Administrator"
		elif can_view_all_records(managed_user):
			managed_user.account_role = "View all targets assigned to all users"
		elif managed_user.is_staff:
			managed_user.account_role = "Target editor"
		else:
			managed_user.account_role = "Read-only viewer"
		managed_user.is_locked = managed_user.username in locked_usernames

	return render(
		request,
		"case_portal/manage_users.html",
		{"managed_users": managed_users},
	)


@login_required
@require_http_methods(["GET", "POST"])
def edit_user(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	if request.method == "POST":
		form = ManagedUserForm(request.POST, instance=user)
		if form.is_valid():
			with transaction.atomic():
				form.save()
				_apply_user_role(user, form.cleaned_data["role"])
			_log_user_action(
				request.user,
				user,
				CHANGE,
				"User account and role updated from the management dashboard.",
			)
			messages.success(request, f"User {user.username} updated.")
			return redirect("manage-users")
	else:
		form = ManagedUserForm(instance=user)

	return render(
		request,
		"case_portal/edit_user.html",
		{"form": form, "managed_user": user},
	)


@login_required
@require_http_methods(["GET", "POST"])
def reset_user_password(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	if request.method == "POST":
		form = AdminPasswordResetForm(user, request.POST)
		if form.is_valid():
			form.save()
			_log_user_action(
				request.user,
				user,
				CHANGE,
				"User password reset from the management dashboard.",
			)
			messages.success(request, f"Password reset for {user.username}.")
			return redirect("manage-users")
	else:
		form = AdminPasswordResetForm(user)

	return render(
		request,
		"case_portal/reset_user_password.html",
		{"form": form, "managed_user": user},
	)


@login_required
@require_http_methods(["GET", "POST"])
def delete_user(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	if request.method == "POST":
		# Deactivate instead of deleting: LogEntry rows cascade-delete with
		# their user, so a real delete would erase that user's audit history.
		# Inactive users are signed out on their next request.
		if user.is_active:
			user.is_active = False
			user.save(update_fields=["is_active"])
			_log_user_action(
				request.user,
				user,
				CHANGE,
				"User account deactivated from the management dashboard.",
			)
			messages.success(request, f"User {user.username} deactivated.")
		else:
			messages.info(request, f"User {user.username} is already inactive.")
		return redirect("manage-users")

	return render(
		request,
		"case_portal/delete_user.html",
		{"managed_user": user},
	)


@login_required
@require_POST
def activate_user(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	AccessAttempt.objects.filter(username=user.username).delete()
	_log_user_action(
		request.user,
		user,
		CHANGE,
		"User account unlocked from the management dashboard.",
	)
	messages.success(request, f"User {user.username} account unlocked.")
	return redirect("manage-users")


@login_required
@require_POST
def delete_case_phone_number(request, case_id, phone_number_id):
	if not (
		request.user.is_staff
		and request.user.has_perm("Adminbackup.delete_casephonenumber")
	):
		raise PermissionDenied

	case_record = get_object_or_404(
		_dashboard_case_queryset(request.user),
		pk=case_id,
	)
	phone_number = get_object_or_404(
		case_record.phone_numbers,
		pk=phone_number_id,
	)
	phone_description = str(phone_number)
	phone_number.delete()
	_log_target_action(
		request.user,
		case_record,
		CHANGE,
		f"Removed phone number: {phone_description}.",
	)

	status = request.POST.get("status", "current")
	if status not in ("current", "stopped"):
		status = "current"
	redirect_params = {"status": status}
	search_query = request.POST.get("q", "").strip()
	if search_query:
		redirect_params["q"] = search_query

	return redirect(f"{reverse('dashboard')}?{urlencode(redirect_params)}")


@login_required
@require_http_methods(["GET", "POST"])
def edit_target_phone_numbers(request, case_id):
	if not (
		request.user.is_staff
		and request.user.has_perm("Adminbackup.change_casephonenumber")
	):
		raise PermissionDenied

	case_record = get_object_or_404(
		_dashboard_case_queryset(request.user),
		pk=case_id,
	)
	if request.method == "POST":
		formset = CasePhoneNumberEditFormSet(
			request.POST,
			instance=case_record,
			prefix="phone_numbers",
		)
		if formset.is_valid():
			if formset.has_changed():
				before_numbers = list(
					case_record.phone_numbers.order_by("pk").values_list(
						"label", "number"
					)
				)
				with transaction.atomic():
					formset.save()
				after_numbers = list(
					case_record.phone_numbers.order_by("pk").values_list(
						"label", "number"
					)
				)
				_log_target_action(
					request.user,
					case_record,
					CHANGE,
					f"Updated phone numbers from {before_numbers} to {after_numbers}.",
				)
				messages.success(request, "Phone numbers updated.")
			else:
				messages.info(request, "No phone number changes were made.")
			return redirect("dashboard")
	else:
		formset = CasePhoneNumberEditFormSet(
			instance=case_record,
			prefix="phone_numbers",
		)

	return render(
		request,
		"case_portal/edit_phone_numbers.html",
		{"case_record": case_record, "formset": formset},
	)


@login_required
@require_POST
def delete_target(request, case_id):
	if not _can_delete_targets(request.user):
		raise PermissionDenied

	case_record = get_object_or_404(
		_dashboard_case_queryset(request.user),
		pk=case_id,
	)
	_log_target_action(
		request.user,
		case_record,
		DELETION,
		"Target record deleted from the case portal.",
	)
	case_record.delete()

	status = request.POST.get("status", "current")
	if status not in ("current", "stopped"):
		status = "current"
	redirect_params = {"status": status}
	search_query = request.POST.get("q", "").strip()
	if search_query:
		redirect_params["q"] = search_query

	messages.success(request, "Target deleted. Remaining targets were renumbered.")
	return redirect(f"{reverse('dashboard')}?{urlencode(redirect_params)}")


@login_required
def submit_case(request):
	if not staff_can_add_cases(request.user):
		raise PermissionDenied

	if request.method == "POST":
		case_form = CaseRecordIntakeForm(
			request.POST,
			request.FILES,
			can_assign=request.user.is_superuser,
		)
		phone_formset = CasePhoneNumberFormSet(
			request.POST,
			prefix="phone_numbers",
		)
		report_formset = CaseReportFormSet(
			request.POST,
			request.FILES,
			prefix="reports",
		)
		if (
			case_form.is_valid()
			and phone_formset.is_valid()
			and report_formset.is_valid()
		):
			with transaction.atomic():
				case_record = case_form.save()
				TargetMonitoringPeriod.objects.create(
					target=case_record,
					started_on=case_record.start_date,
				)
				phone_formset.instance = case_record
				phone_formset.save()
				report_formset.instance = case_record
				report_formset.save()
			_log_target_action(
				request.user,
				case_record,
				ADDITION,
				"Target record created in the case portal, including submitted phone numbers and reports.",
			)
			messages.success(request, "Target record saved.")
			return redirect("dashboard")
	else:
		case_form = CaseRecordIntakeForm(can_assign=request.user.is_superuser)
		phone_formset = CasePhoneNumberFormSet(prefix="phone_numbers")
		report_formset = CaseReportFormSet(prefix="reports")

	return render(
		request,
		"case_portal/case_form.html",
		{
			"case_form": case_form,
			"phone_formset": phone_formset,
			"report_formset": report_formset,
		},
	)


@login_required
@require_http_methods(["GET", "POST"])
def edit_target(request, case_id):
	"""Edit a target from the case portal without opening Django admin."""
	if not _can_edit_targets(request.user):
		raise PermissionDenied

	case_record = get_object_or_404(
		_dashboard_case_queryset(request.user),
		pk=case_id,
	)
	if request.method == "POST":
		case_form = CaseRecordEditorForm(
			request.POST,
			request.FILES,
			instance=case_record,
			can_assign=request.user.is_superuser,
		)
		phone_formset = CasePhoneNumberManagementFormSet(
			request.POST,
			instance=case_record,
			prefix="phone_numbers",
		)
		report_formset = CaseReportManagementFormSet(
			request.POST,
			request.FILES,
			instance=case_record,
			prefix="reports",
		)
		period_formset = TargetMonitoringPeriodFormSet(
			request.POST,
			instance=case_record,
			prefix="monitoring_periods",
		)
		if (
			case_form.is_valid()
			and phone_formset.is_valid()
			and report_formset.is_valid()
			and period_formset.is_valid()
		):
			with transaction.atomic():
				case_form.save()
				phone_formset.save()
				report_formset.save()
				period_formset.save()
			_log_target_action(
				request.user,
				case_record,
				CHANGE,
				"Target record updated in the case portal, including phone numbers and reports.",
			)
			messages.success(request, "Target record updated.")
			return redirect("dashboard")
	else:
		case_form = CaseRecordEditorForm(
			instance=case_record,
			can_assign=request.user.is_superuser,
		)
		phone_formset = CasePhoneNumberManagementFormSet(
			instance=case_record,
			prefix="phone_numbers",
		)
		report_formset = CaseReportManagementFormSet(
			instance=case_record,
			prefix="reports",
		)
		period_formset = TargetMonitoringPeriodFormSet(
			instance=case_record,
			prefix="monitoring_periods",
		)

	return render(
		request,
		"case_portal/edit_target.html",
		{
			"case_record": case_record,
			"case_form": case_form,
			"phone_formset": phone_formset,
			"report_formset": report_formset,
			"period_formset": period_formset,
		},
	)


@login_required
def case_submission_complete(request):
	if not staff_can_add_cases(request.user):
		raise PermissionDenied
	return render(request, "case_portal/submission_complete.html")


@login_required
@require_GET
def private_identification_media(request, filename):
	relative_path = f"personal_identifications/{filename}"
	record = CaseRecord.objects.filter(
		personal_identifications=relative_path
	).first()
	if record is None or not _can_read_case(request.user, record):
		raise Http404

	return FileResponse(
		record.personal_identifications.open("rb"),
		content_type=guess_type(record.personal_identifications.name)[0]
		or "application/octet-stream",
	)


@login_required
@require_GET
def private_target_file(request, case_id, field_name):
	if field_name not in ("personal_identifications", "attached_document"):
		raise Http404

	record = get_object_or_404(_dashboard_case_queryset(request.user), pk=case_id)
	target_file = getattr(record, field_name)
	if not target_file:
		raise Http404

	is_report = field_name == "attached_document"
	return FileResponse(
		target_file.open("rb"),
		as_attachment=is_report,
		filename=target_file.name.rsplit("/", 1)[-1],
		content_type=guess_type(target_file.name)[0]
		or "application/octet-stream",
	)


@login_required
@require_GET
def private_attached_report(request, case_id, report_id):
	record = get_object_or_404(_dashboard_case_queryset(request.user), pk=case_id)
	report = get_object_or_404(CaseReport, pk=report_id, case_record=record)
	return FileResponse(
		report.file.open("rb"),
		as_attachment=True,
		filename=report.file.name.rsplit("/", 1)[-1],
		content_type=guess_type(report.file.name)[0]
		or "application/octet-stream",
	)


def _can_read_case(user, record):
	return _dashboard_case_queryset(user).filter(pk=record.pk).exists()
