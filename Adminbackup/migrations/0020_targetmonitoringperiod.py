from django.db import migrations, models
import django.db.models.deletion


def copy_legacy_monitoring_periods(apps, schema_editor):
	CaseRecord = apps.get_model("Adminbackup", "CaseRecord")
	TargetMonitoringPeriod = apps.get_model(
		"Adminbackup", "TargetMonitoringPeriod"
	)
	database = schema_editor.connection.alias

	for target in CaseRecord.objects.using(database).all().iterator():
		first_end = target.first_termination_date
		if (
			not target.reprovisioned_date
			and target.second_termination_date
			and (
				first_end is None
				or target.second_termination_date > first_end
			)
		):
			first_end = target.second_termination_date

		if target.start_date:
			TargetMonitoringPeriod.objects.using(database).create(
				target_id=target.pk,
				started_on=target.start_date,
				terminated_on=first_end,
				remarks=target.remarks,
			)

		if target.reprovisioned_date:
			TargetMonitoringPeriod.objects.using(database).create(
				target_id=target.pk,
				started_on=target.reprovisioned_date,
				terminated_on=target.second_termination_date,
			)


class Migration(migrations.Migration):
	dependencies = [
		("Adminbackup", "0019_alter_caserecord_assigned_user"),
	]

	operations = [
		migrations.CreateModel(
			name="TargetMonitoringPeriod",
			fields=[
				(
					"id",
					models.BigAutoField(
						auto_created=True,
						primary_key=True,
						serialize=False,
						verbose_name="ID",
					),
				),
				(
					"started_on",
					models.DateField(verbose_name="Monitoring started"),
				),
				(
					"terminated_on",
					models.DateField(
						blank=True,
						null=True,
						verbose_name="Monitoring terminated",
					),
				),
				(
					"remarks",
					models.TextField(blank=True, default=""),
				),
				(
					"target",
					models.ForeignKey(
						on_delete=django.db.models.deletion.CASCADE,
						related_name="monitoring_periods",
						to="Adminbackup.caserecord",
					),
				),
			],
			options={"ordering": ("started_on", "pk")},
		),
		migrations.RunPython(
			copy_legacy_monitoring_periods,
			migrations.RunPython.noop,
		),
	]
