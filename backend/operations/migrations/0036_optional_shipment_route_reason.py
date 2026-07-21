from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0035_shipment_line_quantity_adjustment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shipmentrouteassignment",
            name="reason",
            field=models.TextField(blank=True),
        ),
    ]
