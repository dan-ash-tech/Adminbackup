from django.conf import settings
from django.db import migrations, models


def copy_legacy_assignments(apps, schema_editor):
	CaseRecord = apps.get_model("Adminbackup", "CaseRecord")
	database = schema_editor.connection.alias
	for case_record in CaseRecord.objects.using(database).exclude(
		assigned_user_id__isnull=True
	).iterator():
		case_record.assigned_users.add(case_record.assigned_user_id)


class Migration(migrations.Migration):
	dependencies = [
		("Adminbackup", "0017_caserecord_remarks"),
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
	]

	operations = [
		migrations.AddField(
			model_name="caserecord",
			name="assigned_users",
			field=models.ManyToManyField(
				blank=True,
				related_name="assigned_target_records",
				to=settings.AUTH_USER_MODEL,
				verbose_name="Assigned users",
			),
		),
		migrations.RunPython(copy_legacy_assignments, migrations.RunPython.noop),
	]
