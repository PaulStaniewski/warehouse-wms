from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0018_transferdiscrepancytransitinvestigation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="transferdiscrepancymanualreconciliationdecision",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("source_loss_confirmed", "Source loss confirmed"),
                    ("transit_loss_confirmed", "Transit loss confirmed"),
                    ("unresolved_loss_closed", "Unresolved loss - cause not determined"),
                    ("administrative_error", "Administrative or process error"),
                ],
                max_length=64,
            ),
        ),
    ]
