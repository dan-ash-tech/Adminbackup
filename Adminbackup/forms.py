from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet

from .models import (
	CasePhoneNumber,
	CaseRecord,
	CaseReport,
	TargetMonitoringPeriod,
)


USER_ROLE_CHOICES = (
	("viewer", "Read-only viewer"),
	("view_all_records", "View all targets assigned to all users"),
	("target_editor", "Target editor"),
)


class ViewerUserCreationForm(UserCreationForm):
	role = forms.ChoiceField(
		choices=USER_ROLE_CHOICES,
		label="Permissions",
		help_text=(
			"Read-only viewers see assigned targets. View-all users can view "
			"targets assigned to all users. Target editors can manage target records."
		),
	)

	class Meta(UserCreationForm.Meta):
		model = get_user_model()
		fields = ("username",)


class ManagedUserForm(forms.ModelForm):
	role = forms.ChoiceField(
		choices=USER_ROLE_CHOICES,
		label="Permissions",
		help_text=(
			"Read-only viewers see assigned targets. View-all users can view "
			"targets assigned to all users. Target editors can manage target records."
		),
	)

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.instance and self.instance.pk:
			if not self.instance.is_staff:
				role = "viewer"
			elif self.instance.has_perm("Adminbackup.change_caserecord"):
				role = "target_editor"
			else:
				role = "view_all_records"
			self.fields["role"].initial = role

	class Meta:
		model = get_user_model()
		fields = ("username", "first_name", "last_name", "email", "is_active")
		widgets = {
			"username": forms.TextInput(attrs={"autocomplete": "username"}),
			"email": forms.EmailInput(attrs={"autocomplete": "email"}),
		}


class AdminPasswordResetForm(SetPasswordForm):
	pass


class CaseRecordIntakeForm(forms.ModelForm):
	assigned_user = forms.ModelChoiceField(
		queryset=get_user_model().objects.none(),
		required=False,
		widget=forms.HiddenInput(),
	)

	def __init__(self, *args, can_assign=False, **kwargs):
		super().__init__(*args, **kwargs)
		if not can_assign:
			self.fields.pop("assigned_users")
			self.fields.pop("assigned_user")
		else:
			assignable_users = get_user_model().objects.filter(
				is_active=True,
				is_superuser=False,
			)
			self.fields["assigned_users"].queryset = assignable_users
			self.fields["assigned_user"].queryset = assignable_users
			self.fields["assigned_users"].widget = forms.CheckboxSelectMultiple(
				choices=self.fields["assigned_users"].choices
			)
			self.fields["assigned_users"].help_text = (
				"Open the list and check every user who should receive this target."
			)

	class Meta:
		model = CaseRecord
		fields = (
			"code",
			"assigned_users",
			"assigned_user",
			"first_name",
			"last_name",
			"authorizer",
			"case_type",
			"personal_identifications",
			"start_date",
			"remarks",
		)
		widgets = {
			"case_type": forms.Textarea(attrs={"rows": 5}),
			"remarks": forms.Textarea(attrs={"rows": 3}),
			"personal_identifications": forms.ClearableFileInput(
				attrs={"accept": "image/*"}
			),
			"start_date": forms.DateInput(attrs={"type": "date"}),
		}

	def save(self, commit=True):
		case_record = super().save(commit=commit)
		legacy_user = self.cleaned_data.get("assigned_user")
		if commit and legacy_user:
			case_record.assigned_users.add(legacy_user)
		return case_record


class CaseRecordEditorForm(CaseRecordIntakeForm):
	"""Target editor form; monitoring dates are managed as history rows."""

	class Meta(CaseRecordIntakeForm.Meta):
		fields = (
			"code",
			"assigned_users",
			"assigned_user",
			"first_name",
			"last_name",
			"authorizer",
			"case_type",
			"personal_identifications",
			"attached_document",
			"start_date",
			"remarks",
		)


class TargetMonitoringPeriodForm(forms.ModelForm):
	class Meta:
		model = TargetMonitoringPeriod
		fields = ("started_on", "terminated_on", "remarks")
		widgets = {
			"started_on": forms.DateInput(attrs={"type": "date"}),
			"terminated_on": forms.DateInput(attrs={"type": "date"}),
			"remarks": forms.Textarea(attrs={"rows": 2}),
		}

	def clean(self):
		cleaned_data = super().clean()
		started_on = cleaned_data.get("started_on")
		terminated_on = cleaned_data.get("terminated_on")
		if started_on and terminated_on and terminated_on < started_on:
			raise ValidationError(
				"Monitoring termination cannot be earlier than its start date."
			)
		return cleaned_data


class CasePhoneNumberInlineFormSet(BaseInlineFormSet):
	def clean(self):
		super().clean()
		seen_numbers = set()

		for form in self.forms:
			if not hasattr(form, "cleaned_data") or form.errors:
				continue
			if form.cleaned_data.get("DELETE"):
				continue

			number = form.cleaned_data.get("number")
			if not number:
				continue
			normalized_number = "".join(
				character for character in number if character.isdigit()
			)
			if normalized_number in seen_numbers:
				raise ValidationError(
					"A target number can only be entered once per target."
				)
			seen_numbers.add(normalized_number)


class CasePhoneNumberEditFormSet(CasePhoneNumberInlineFormSet):
	def clean(self):
		super().clean()
		if any(
			form.instance.pk is None
			and form.has_changed()
			for form in self.forms
		):
			raise ValidationError("Only existing phone numbers can be edited.")


CasePhoneNumberFormSet = inlineformset_factory(
	CaseRecord,
	CasePhoneNumber,
	fields=("label", "number"),
	formset=CasePhoneNumberInlineFormSet,
	can_delete=False,
	extra=1,
)

CasePhoneNumberEditFormSet = inlineformset_factory(
	CaseRecord,
	CasePhoneNumber,
	fields=("number",),
	formset=CasePhoneNumberEditFormSet,
	can_delete=False,
	extra=0,
)

# Full target editors can maintain labels, add numbers, and remove obsolete
# numbers. The restricted phone-number editor above intentionally remains
# limited to changing existing numbers.
CasePhoneNumberManagementFormSet = inlineformset_factory(
	CaseRecord,
	CasePhoneNumber,
	fields=("label", "number"),
	formset=CasePhoneNumberInlineFormSet,
	can_delete=True,
	extra=1,
)


CaseReportFormSet = inlineformset_factory(
	CaseRecord,
	CaseReport,
	fields=("file",),
	can_delete=False,
	extra=1,
)

CaseReportManagementFormSet = inlineformset_factory(
	CaseRecord,
	CaseReport,
	fields=("file",),
	can_delete=True,
	extra=1,
)


class TargetMonitoringPeriodBaseFormSet(BaseInlineFormSet):
	def clean(self):
		super().clean()
		active_periods = []
		periods = []
		for form in self.forms:
			if not hasattr(form, "cleaned_data") or form.errors:
				continue
			if form.cleaned_data.get("DELETE"):
				continue
			started_on = form.cleaned_data.get("started_on")
			terminated_on = form.cleaned_data.get("terminated_on")
			if not started_on:
				continue
			periods.append((started_on, terminated_on))
			if terminated_on is None:
				active_periods.append(form)

		if len(active_periods) > 1:
			self.add_error(
				None,
				"Only one monitoring period can be current at a time.",
			)
		if not periods:
			self.add_error(None, "Keep at least one monitoring period for this target.")
		periods.sort(key=lambda period: period[0])
		for previous, current in zip(periods, periods[1:]):
			if previous[1] and current[0] < previous[1]:
				self.add_error(
					None,
					"Monitoring periods cannot overlap.",
				)
				break


TargetMonitoringPeriodFormSet = inlineformset_factory(
	CaseRecord,
	TargetMonitoringPeriod,
	form=TargetMonitoringPeriodForm,
	formset=TargetMonitoringPeriodBaseFormSet,
	can_delete=True,
	extra=1,
)
