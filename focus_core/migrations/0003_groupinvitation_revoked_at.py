from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("focus_core", "0002_focususer_availability_focususer_bio_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="groupinvitation",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
