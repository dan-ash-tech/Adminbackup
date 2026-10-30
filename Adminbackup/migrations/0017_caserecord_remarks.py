from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("Adminbackup", "0016_casereport"),
	]

	operations = [
		migrations.AddField(
			model_name="caserecord",
			name="remarks",
			field=models.TextField(
				blank=True,
				default="",
				help_text="For stopped targets, record why monitoring ended.",
				verbose_name="Remarks",
			),
		),
	]
