from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0020_auditlog_branch_auditlog_cart_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="expected_quantity",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="checked_quantity",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True),
        ),
    ]
