import Adminbackup.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Adminbackup", "0021_upload_size_limits"),
    ]

    operations = [
        migrations.AlterField(
            model_name="caserecord",
            name="code",
            field=models.CharField(
                blank=True,
                max_length=50,
                null=True,
                unique=True,
                verbose_name="Call-sign",
            ),
        ),
        migrations.AlterField(
            model_name="caserecord",
            name="personal_identifications",
            field=models.ImageField(
                blank=True,
                upload_to="personal_identifications/",
                validators=[Adminbackup.models.validate_upload_size],
                verbose_name="Image",
            ),
        ),
    ]
