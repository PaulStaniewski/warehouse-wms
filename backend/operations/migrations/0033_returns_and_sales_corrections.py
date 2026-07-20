import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0032_scannerquicktransferoperation"),
        ("warehouse", "0002_product_presentation_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="stockmovement",
            name="movement_type",
            field=models.CharField(
                choices=[
                    ("receipt", "Receipt"),
                    ("pick", "Pick"),
                    ("return", "Return"),
                    ("adjustment", "Adjustment"),
                    ("transfer", "Transfer"),
                    ("receiving_discrepancy", "Receiving discrepancy"),
                    ("discrepancy_recovery", "Discrepancy recovery"),
                    ("discrepancy_shortage", "Discrepancy shortage"),
                    ("source_discrepancy_recovery", "Source discrepancy recovery"),
                    ("picking_shortage", "Picking shortage"),
                    ("picking_shortage_found", "Picking shortage found"),
                    ("picking_shortage_confirmed_missing", "Picking shortage confirmed missing"),
                    ("return_receipt", "Return receipt"),
                    ("sales_correction_receipt", "Sales correction receipt"),
                ],
                max_length=64,
            ),
        ),
        migrations.CreateModel(
            name="ExternalReturnDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("external_reference", models.CharField(max_length=128)),
                ("source_system", models.CharField(default="AX", max_length=64)),
                ("customer_name", models.CharField(max_length=255)),
                ("customer_alias", models.CharField(blank=True, max_length=128)),
                ("source_sales_document_reference", models.CharField(blank=True, max_length=128)),
                ("external_created_at", models.DateTimeField(blank=True, null=True)),
                ("imported_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("in_progress", "In progress"),
                            ("on_hold", "On hold"),
                            ("completed", "Completed"),
                        ],
                        default="open",
                        max_length=32,
                    ),
                ),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="external_return_documents",
                        to="warehouse.branch",
                    ),
                ),
            ],
            options={
                "ordering": ["-imported_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ExternalReturnDocumentLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("line_number", models.PositiveIntegerField()),
                ("expected_quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("accepted_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("rejected_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("on_hold_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="operations.externalreturndocument",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="external_return_lines",
                        to="warehouse.product",
                    ),
                ),
            ],
            options={
                "ordering": ["document", "line_number"],
            },
        ),
        migrations.CreateModel(
            name="ReturnAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("accept_remaining", "Accept remaining quantity"),
                            ("reject_remaining", "Reject remaining quantity"),
                            ("put_on_hold", "Put remaining quantity on hold"),
                            ("accept_on_hold", "Accept on-hold quantity"),
                            ("reject_on_hold", "Reject on-hold quantity"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "source_pool",
                    models.CharField(choices=[("remaining", "Remaining"), ("on_hold", "On hold")], max_length=32),
                ),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("note", models.TextField(blank=True)),
                ("client_operation_id", models.CharField(max_length=64, unique=True)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                ("inventory_quantity_before", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("inventory_quantity_after", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="return_actions",
                        to="warehouse.branch",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="actions",
                        to="operations.externalreturndocument",
                    ),
                ),
                (
                    "line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="actions",
                        to="operations.externalreturndocumentline",
                    ),
                ),
                (
                    "performed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="return_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="return_actions",
                        to="warehouse.product",
                    ),
                ),
                (
                    "stock_movement",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="return_action",
                        to="operations.stockmovement",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="external_return_action",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="stock_movements",
                to="operations.returnaction",
            ),
        ),
        migrations.CreateModel(
            name="SalesCorrection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reference", models.CharField(blank=True, max_length=64, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "Draft"), ("completed", "Completed"), ("cancelled", "Cancelled")],
                        default="draft",
                        max_length=32,
                    ),
                ),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.TextField(blank=True)),
                ("confirmation_client_operation_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("confirmation_payload_fingerprint", models.CharField(blank=True, max_length=64)),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_corrections",
                        to="warehouse.branch",
                    ),
                ),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="confirmed_sales_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_sales_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SalesCorrectionLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("customer_name_snapshot", models.CharField(max_length=255)),
                ("customer_alias_snapshot", models.CharField(blank=True, max_length=128)),
                ("source_sales_document_reference", models.CharField(max_length=128)),
                ("sold_quantity_snapshot", models.DecimalField(decimal_places=3, max_digits=12)),
                ("corrected_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("inventory_quantity_before", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("inventory_quantity_after", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                (
                    "correction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="operations.salescorrection",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_correction_lines",
                        to="warehouse.product",
                    ),
                ),
                (
                    "returns_location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_correction_lines",
                        to="warehouse.location",
                    ),
                ),
                (
                    "source_order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_correction_lines",
                        to="operations.order",
                    ),
                ),
                (
                    "source_order_line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_correction_lines",
                        to="operations.orderline",
                    ),
                ),
                (
                    "stock_movement",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="posted_sales_correction_line",
                        to="operations.stockmovement",
                    ),
                ),
            ],
            options={
                "ordering": ["correction", "id"],
            },
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="sales_correction_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="stock_movements",
                to="operations.salescorrectionline",
            ),
        ),
        migrations.AddIndex(
            model_name="externalreturndocument",
            index=models.Index(fields=["branch", "status"], name="operations__branch__3580e4_idx"),
        ),
        migrations.AddIndex(
            model_name="externalreturndocument",
            index=models.Index(fields=["external_reference"], name="operations__externa_0b8c15_idx"),
        ),
        migrations.AddIndex(
            model_name="externalreturndocument",
            index=models.Index(fields=["source_sales_document_reference"], name="operations__source__89d27f_idx"),
        ),
        migrations.AddIndex(
            model_name="externalreturndocument",
            index=models.Index(fields=["customer_name"], name="operations__custome_f35a80_idx"),
        ),
        migrations.AddIndex(
            model_name="externalreturndocument",
            index=models.Index(fields=["imported_at"], name="operations__importe_9496a6_idx"),
        ),
        migrations.AddConstraint(
            model_name="externalreturndocument",
            constraint=models.UniqueConstraint(
                fields=("source_system", "external_reference"),
                name="unique_external_return_document_reference_per_source",
            ),
        ),
        migrations.AddIndex(
            model_name="externalreturndocumentline",
            index=models.Index(fields=["document"], name="operations__documen_9e882f_idx"),
        ),
        migrations.AddIndex(
            model_name="externalreturndocumentline",
            index=models.Index(fields=["product"], name="operations__product_972bc1_idx"),
        ),
        migrations.AddConstraint(
            model_name="externalreturndocumentline",
            constraint=models.UniqueConstraint(fields=("document", "line_number"), name="unique_external_return_line_number"),
        ),
        migrations.AddConstraint(
            model_name="externalreturndocumentline",
            constraint=models.CheckConstraint(condition=models.Q(("expected_quantity__gt", 0)), name="external_return_expected_positive"),
        ),
        migrations.AddConstraint(
            model_name="externalreturndocumentline",
            constraint=models.CheckConstraint(condition=models.Q(("accepted_quantity__gte", 0)), name="external_return_accepted_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="externalreturndocumentline",
            constraint=models.CheckConstraint(condition=models.Q(("rejected_quantity__gte", 0)), name="external_return_rejected_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="externalreturndocumentline",
            constraint=models.CheckConstraint(condition=models.Q(("on_hold_quantity__gte", 0)), name="external_return_on_hold_non_negative"),
        ),
        migrations.AddIndex(
            model_name="returnaction",
            index=models.Index(fields=["document", "created_at"], name="operations__documen_7155a9_idx"),
        ),
        migrations.AddIndex(
            model_name="returnaction",
            index=models.Index(fields=["line", "created_at"], name="operations__line_id_7df7c6_idx"),
        ),
        migrations.AddIndex(
            model_name="returnaction",
            index=models.Index(fields=["branch", "action_type"], name="operations__branch__93e26f_idx"),
        ),
        migrations.AddIndex(
            model_name="returnaction",
            index=models.Index(fields=["product"], name="operations__product_c791d2_idx"),
        ),
        migrations.AddIndex(
            model_name="returnaction",
            index=models.Index(fields=["performed_by"], name="operations__perform_836911_idx"),
        ),
        migrations.AddIndex(
            model_name="returnaction",
            index=models.Index(fields=["client_operation_id"], name="operations__client__b04886_idx"),
        ),
        migrations.AddConstraint(
            model_name="returnaction",
            constraint=models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="return_action_quantity_positive"),
        ),
        migrations.AddIndex(
            model_name="salescorrection",
            index=models.Index(fields=["reference"], name="operations__referen_775fd3_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrection",
            index=models.Index(fields=["branch", "status"], name="operations__branch__d3024d_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrection",
            index=models.Index(fields=["confirmed_at"], name="operations__confirm_971bb4_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrection",
            index=models.Index(fields=["created_by"], name="operations__created_90f865_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrection",
            index=models.Index(fields=["confirmed_by"], name="operations__confirm_3de864_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrectionline",
            index=models.Index(fields=["correction"], name="operations__correct_fc09b2_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrectionline",
            index=models.Index(fields=["product"], name="operations__product_729cf6_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrectionline",
            index=models.Index(fields=["source_order_line"], name="operations__source__bfc17c_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrectionline",
            index=models.Index(fields=["source_sales_document_reference"], name="operations__source__0a1a05_idx"),
        ),
        migrations.AddIndex(
            model_name="salescorrectionline",
            index=models.Index(fields=["customer_name_snapshot"], name="operations__custome_f6079d_idx"),
        ),
        migrations.AddConstraint(
            model_name="salescorrectionline",
            constraint=models.UniqueConstraint(
                fields=("correction", "source_order_line"),
                name="unique_sales_correction_source_line_per_correction",
            ),
        ),
        migrations.AddConstraint(
            model_name="salescorrectionline",
            constraint=models.CheckConstraint(condition=models.Q(("sold_quantity_snapshot__gt", 0)), name="sales_correction_sold_positive"),
        ),
        migrations.AddConstraint(
            model_name="salescorrectionline",
            constraint=models.CheckConstraint(condition=models.Q(("corrected_quantity__gte", 0)), name="sales_correction_corrected_non_negative"),
        ),
    ]
