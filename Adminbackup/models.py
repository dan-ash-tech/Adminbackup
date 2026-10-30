from django.db import models
from django.db.models import Exists, F, OuterRef, Q
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import router, transaction
from django.conf import settings


class CaseRecordQuerySet(models.QuerySet):
	@staticmethod
	def _reprovisioned_after_termination():
		return (
			Q(reprovisioned_date__isnull=False)
			& (
				Q(first_termination_date__isnull=True)
				| Q(reprovisioned_date__gte=F("first_termination_date"))
			)
			& (
				Q(second_termination_date__isnull=True)
				| Q(reprovisioned_date__gte=F("second_termination_date"))
			)
		)

	def current_monitored(self):
		periods = TargetMonitoringPeriod.objects.filter(target_id=OuterRef("pk"))
		active_periods = periods.filter(terminated_on__isnull=True)
		return self.annotate(
			_has_monitoring_period=Exists(periods),
			_has_active_monitoring_period=Exists(active_periods),
		).filter(
			Q(
				_has_monitoring_period=True,
				_has_active_monitoring_period=True,
			)
			| (
				Q(_has_monitoring_period=False)
				& (
					Q(
						first_termination_date__isnull=True,
						second_termination_date__isnull=True,
					)
					| self._reprovisioned_after_termination()
				)
			)
		)

	def stopped(self):
		periods = TargetMonitoringPeriod.objects.filter(target_id=OuterRef("pk"))
		active_periods = periods.filter(terminated_on__isnull=True)
		return self.annotate(
			_has_monitoring_period=Exists(periods),
			_has_active_monitoring_period=Exists(active_periods),
		).filter(
			Q(
				_has_monitoring_period=True,
				_has_active_monitoring_period=False,
			)
			| (
				Q(_has_monitoring_period=False)
				& (
					Q(first_termination_date__isnull=False)
					| Q(second_termination_date__isnull=False)
				)
				& ~self._reprovisioned_after_termination()
			)
		)

	def delete(self):
		using = self.db
		with transaction.atomic(using=using):
			result = super().delete()
			renumber_case_records(using)
			return result


class CaseRecord(models.Model):
	objects = CaseRecordQuerySet.as_manager()

	serial_number = models.CharField(
		max_length=50,
		blank=True,
		default="",
		unique=True,
		verbose_name="S/N",
	)
	code = models.CharField(
		max_length=50,
		unique=True,
		verbose_name="Call-sign",
	)
	assigned_users = models.ManyToManyField(
		settings.AUTH_USER_MODEL,
		related_name="assigned_target_records",
		blank=True,
		verbose_name="Assigned users",
	)
	# Kept for compatibility with older integrations; assignments are managed via
	# assigned_users and existing assigned_user values are copied by migration.
	assigned_user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		related_name="assigned_cases",
		blank=True,
		null=True,
		verbose_name="Legacy assigned user",
	)
	first_name = models.CharField(max_length=150)
	last_name = models.CharField(max_length=150)
	authorizer = models.CharField(max_length=150)
	case_type = models.TextField()
	personal_identifications = models.ImageField(
		upload_to="personal_identifications/",
		verbose_name="Image",
	)
	attached_document = models.FileField(
		upload_to="case_documents/",
		blank=True,
		verbose_name="Report",
		validators=[FileExtensionValidator(["doc", "docx"])],
	)
	start_date = models.DateField()
	first_termination_date = models.DateField(blank=True, null=True)
	reprovisioned_date = models.DateField(
		blank=True,
		null=True,
		verbose_name="Reprovisioned on",
	)
	reprovisioned = models.BooleanField(
		default=False,
		verbose_name="Case reprovisioned",
	)
	second_termination_date = models.DateField(blank=True, null=True)
	remarks = models.TextField(
		blank=True,
		default="",
		help_text="For stopped targets, record why monitoring ended.",
		verbose_name="Remarks",
	)

	class Meta:
		ordering = ["pk"]

	def save(self, *args, **kwargs):
		using = kwargs.get("using") or self._state.db or router.db_for_write(
			type(self), instance=self
		)
		with transaction.atomic(using=using):
			if self._state.adding:
				self.serial_number = str(
					type(self).objects.using(using).count() + 1
				)
			super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		using = kwargs.get("using") or self._state.db or router.db_for_write(
			type(self), instance=self
		)
		with transaction.atomic(using=using):
			result = super().delete(*args, **kwargs)
			renumber_case_records(using)
			return result

	def __str__(self):
		return f"{self.code} - {self.last_name}, {self.first_name}"

	def is_assigned_to(self, user):
		return (
			self.assigned_user_id == user.pk
			or self.assigned_users.filter(pk=user.pk).exists()
		)

	@property
	def assigned_users_display(self):
		users = list(self.assigned_users.all())
		if self.assigned_user and all(
			user.pk != self.assigned_user_id for user in users
		):
			users.append(self.assigned_user)
		return ", ".join(user.get_username() for user in users) or "-"

	@property
	def monitoring_status(self):
		periods = list(self.monitoring_periods.all())
		latest_period = periods[-1] if periods else None
		if latest_period is not None:
			return (
				"Current monitored"
				if latest_period.terminated_on is None
				else "Stopped from the system"
			)
		termination_dates = [
			termination_date
			for termination_date in (
				self.first_termination_date,
				self.second_termination_date,
			)
			if termination_date is not None
		]
		if not termination_dates or (
			self.reprovisioned_date
			and self.reprovisioned_date >= max(termination_dates)
		):
			return "Current monitored"
		return "Stopped from the system"

	@property
	def monitoring_periods_display(self):
		periods = self.monitoring_periods.all()
		display = []
		for period in periods:
			end = (
				period.terminated_on.strftime("%Y-%m-%d")
				if period.terminated_on
				else "current"
			)
			display.append(f"{period.started_on:%Y-%m-%d} to {end}")
		return "; ".join(display)


class TargetMonitoringPeriod(models.Model):
	target = models.ForeignKey(
		CaseRecord,
		on_delete=models.CASCADE,
		related_name="monitoring_periods",
	)
	started_on = models.DateField(verbose_name="Monitoring started")
	terminated_on = models.DateField(
		blank=True,
		null=True,
		verbose_name="Monitoring terminated",
	)
	remarks = models.TextField(blank=True, default="")

	class Meta:
		ordering = ("started_on", "pk")

	def __str__(self):
		end = self.terminated_on.strftime("%Y-%m-%d") if self.terminated_on else "current"
		return f"{self.started_on:%Y-%m-%d} to {end}"


def renumber_case_records(using):
	records = list(
		CaseRecord.objects.using(using).order_by("pk").only("pk", "serial_number")
	)
	for record in records:
		record.serial_number = f"__renumber__{record.pk}"
	CaseRecord.objects.using(using).bulk_update(records, ["serial_number"])

	for number, record in enumerate(records, start=1):
		record.serial_number = str(number)
	CaseRecord.objects.using(using).bulk_update(records, ["serial_number"])


class CasePhoneNumber(models.Model):
	case_record = models.ForeignKey(
		CaseRecord,
		on_delete=models.CASCADE,
		related_name="phone_numbers",
	)
	label = models.CharField(max_length=50, blank=True)
	number = models.CharField(max_length=100)
	reprovisioned = models.BooleanField(
		default=False,
		verbose_name="Target reprovisioned",
	)
	reprovisioned_date = models.DateField(
		blank=True,
		null=True,
		verbose_name="Target reprovisioned on (legacy)",
	)

	class Meta:
		ordering = ["id"]
		constraints = [
			models.UniqueConstraint(
				fields=("case_record", "number"),
				name="unique_target_number_per_case",
			),
		]

	def __str__(self):
		return f"{self.label}: {self.number}" if self.label else self.number


class CaseReport(models.Model):
	case_record = models.ForeignKey(
		CaseRecord,
		on_delete=models.CASCADE,
		related_name="attached_reports",
	)
	file = models.FileField(
		upload_to="case_documents/",
		validators=[FileExtensionValidator(["doc", "docx"])],
		verbose_name="Report",
	)

	class Meta:
		ordering = ["pk"]

	def __str__(self):
		return self.file.name.rsplit("/", 1)[-1]
