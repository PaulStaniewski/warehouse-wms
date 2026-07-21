import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0034_shipment_command_center"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShipmentLineQuantityAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity_removed", models.DecimalField(decimal_places=3, max_digits=12)),
                ("previous_effective_quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("new_effective_quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("reason", models.TextField()),
                ("client_operation_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                (
                    "adjusted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shipment_line_quantity_adjustments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "shipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="line_quantity_adjustments",
                        to="operations.shipment",
                    ),
                ),
                (
                    "shipment_line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quantity_adjustments",
                        to="operations.shipmentline",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="shipmentlinequantityadjustment",
            index=models.Index(fields=["shipment", "created_at"], name="ship_line_adj_shipment_idx"),
        ),
        migrations.AddIndex(
            model_name="shipmentlinequantityadjustment",
            index=models.Index(fields=["shipment_line", "created_at"], name="ship_line_adj_line_idx"),
        ),
        migrations.AddIndex(
            model_name="shipmentlinequantityadjustment",
            index=models.Index(fields=["client_operation_id"], name="ship_line_adj_client_idx"),
        ),
        migrations.AddConstraint(
            model_name="shipmentlinequantityadjustment",
            constraint=models.CheckConstraint(condition=models.Q(("quantity_removed__gt", 0)), name="ship_line_adj_removed_positive"),
        ),
        migrations.AddConstraint(
            model_name="shipmentlinequantityadjustment",
            constraint=models.CheckConstraint(condition=models.Q(("previous_effective_quantity__gte", 0)), name="ship_line_adj_prev_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="shipmentlinequantityadjustment",
            constraint=models.CheckConstraint(condition=models.Q(("new_effective_quantity__gte", 0)), name="ship_line_adj_new_non_negative"),
        ),
    ]
