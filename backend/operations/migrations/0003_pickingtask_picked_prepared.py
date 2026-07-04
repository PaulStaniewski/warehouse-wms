from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0002_deliveryroute_routerun_order_route_run_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pickingtask",
            name="quantity_prepared",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name="pickingtask",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Open"),
                    ("assigned", "Assigned"),
                    ("in_progress", "In progress"),
                    ("picked", "Picked"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="open",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="pickingtask",
            constraint=models.CheckConstraint(
                check=models.Q(("quantity_prepared__gte", 0)),
                name="picking_prepared_non_negative",
            ),
        ),
    ]
