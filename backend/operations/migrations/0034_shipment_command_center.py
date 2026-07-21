import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0033_returns_and_sales_corrections"),
        ("warehouse", "0002_product_presentation_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Shipment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reference", models.CharField(max_length=128, unique=True)),
                (
                    "shipment_type",
                    models.CharField(
                        choices=[
                            ("customer_delivery", "Customer delivery"),
                            ("branch_collection", "Branch collection"),
                            ("courier_dispatch", "Courier dispatch"),
                            ("inter_branch", "Inter-branch transfer"),
                        ],
                        default="customer_delivery",
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending_activation", "Pending activation"),
                            ("active", "Active"),
                            ("picking", "Picking"),
                            ("picked", "Picked"),
                            ("controlled", "Controlled"),
                            ("prepared", "Prepared"),
                            ("documents_posted", "Documents posted"),
                            ("ready_for_dispatch", "Ready for dispatch"),
                            ("dispatched", "Dispatched"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                            ("exception", "Exception"),
                        ],
                        default="pending_activation",
                        max_length=32,
                    ),
                ),
                (
                    "document_status",
                    models.CharField(
                        choices=[
                            ("not_available", "Not available"),
                            ("available", "Available"),
                            ("previewed", "Previewed"),
                            ("printed", "Printed"),
                            ("posted", "Posted"),
                            ("requires_refresh", "Requires refresh"),
                        ],
                        default="not_available",
                        max_length=32,
                    ),
                ),
                ("source_system", models.CharField(default="AX", max_length=64)),
                ("external_reference", models.CharField(max_length=128)),
                ("external_order_reference", models.CharField(blank=True, max_length=128)),
                ("external_status", models.CharField(blank=True, max_length=64)),
                ("external_customer_account", models.CharField(blank=True, max_length=128)),
                ("external_delivery_reference", models.CharField(blank=True, max_length=128)),
                ("external_notes", models.TextField(blank=True)),
                ("external_created_at", models.DateTimeField(blank=True, null=True)),
                ("external_updated_at", models.DateTimeField(blank=True, null=True)),
                ("customer_name", models.CharField(blank=True, max_length=255)),
                ("customer_alias", models.CharField(blank=True, max_length=128)),
                ("recipient_account", models.CharField(blank=True, max_length=128)),
                ("delivery_name", models.CharField(blank=True, max_length=255)),
                ("delivery_address", models.TextField(blank=True)),
                ("delivery_date", models.DateField(blank=True, null=True)),
                ("payment_method", models.CharField(blank=True, max_length=64)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("picking_lists_posted_at", models.DateTimeField(blank=True, null=True)),
                ("prepared_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("cancellation_reason", models.TextField(blank=True)),
                ("documents_printed_at", models.DateTimeField(blank=True, null=True)),
                ("document_print_count", models.PositiveIntegerField(default=0)),
                ("documents_posted_at", models.DateTimeField(blank=True, null=True)),
                ("picking_route_confirmed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "activated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activated_shipments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipments",
                        to="warehouse.branch",
                    ),
                ),
                (
                    "cancelled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cancelled_shipments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "documents_posted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="posted_shipment_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "documents_printed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="printed_shipment_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "inter_branch_transfer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipments",
                        to="operations.interbranchtransfer",
                    ),
                ),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipment",
                        to="operations.order",
                    ),
                ),
                (
                    "picking_lists_posted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="posted_picking_list_shipments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "picking_route_confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="confirmed_shipment_picking_routes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "prepared_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="prepared_shipments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "route_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipments",
                        to="operations.routerun",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ShipmentLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("line_number", models.PositiveIntegerField()),
                ("external_line_reference", models.CharField(blank=True, max_length=128)),
                ("ordered_quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("cancelled_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("delivery_date", models.DateField(blank=True, null=True)),
                (
                    "order_line",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipment_line",
                        to="operations.orderline",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipment_lines",
                        to="warehouse.product",
                    ),
                ),
                (
                    "shipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="operations.shipment",
                    ),
                ),
            ],
            options={"ordering": ["shipment", "line_number"]},
        ),
        migrations.CreateModel(
            name="ShipmentRouteAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reason", models.TextField()),
                ("previous_route_snapshot", models.CharField(blank=True, max_length=255)),
                ("new_route_snapshot", models.CharField(max_length=255)),
                ("client_operation_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shipment_route_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "new_route_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="new_shipment_assignments",
                        to="operations.routerun",
                    ),
                ),
                (
                    "previous_route_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="previous_shipment_assignments",
                        to="operations.routerun",
                    ),
                ),
                (
                    "shipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="route_assignments",
                        to="operations.shipment",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ShipmentStatusHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("previous_status", models.CharField(blank=True, max_length=32)),
                ("new_status", models.CharField(max_length=32)),
                ("reason", models.TextField()),
                ("client_operation_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shipment_status_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "shipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="status_history",
                        to="operations.shipment",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["branch", "status"], name="operations__branch__9a8d0f_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["branch", "delivery_date"], name="operations__branch__8584c6_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["route_run", "status"], name="operations__route_r_983bc2_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["external_reference"], name="operations__externa_80ef8d_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["customer_alias"], name="operations__custome_f596de_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["document_status"], name="operations__documen_67964e_idx")),
        migrations.AddConstraint(
            model_name="shipment",
            constraint=models.UniqueConstraint(fields=("source_system", "external_reference"), name="unique_shipment_external_reference_per_source"),
        ),
        migrations.AddIndex(model_name="shipmentline", index=models.Index(fields=["shipment"], name="operations__shipmen_3f4d8b_idx")),
        migrations.AddIndex(model_name="shipmentline", index=models.Index(fields=["product"], name="operations__product_cecf22_idx")),
        migrations.AddIndex(model_name="shipmentline", index=models.Index(fields=["external_line_reference"], name="operations__externa_aeab2c_idx")),
        migrations.AddConstraint(
            model_name="shipmentline",
            constraint=models.UniqueConstraint(fields=("shipment", "line_number"), name="unique_shipment_line_number"),
        ),
        migrations.AddConstraint(
            model_name="shipmentline",
            constraint=models.CheckConstraint(condition=models.Q(("ordered_quantity__gt", 0)), name="shipment_line_ordered_quantity_positive"),
        ),
        migrations.AddConstraint(
            model_name="shipmentline",
            constraint=models.CheckConstraint(condition=models.Q(("cancelled_quantity__gte", 0)), name="shipment_line_cancelled_non_negative"),
        ),
        migrations.AddIndex(model_name="shipmentrouteassignment", index=models.Index(fields=["shipment", "created_at"], name="operations__shipmen_f1a5bf_idx")),
        migrations.AddIndex(model_name="shipmentrouteassignment", index=models.Index(fields=["new_route_run"], name="operations__new_rou_4214ac_idx")),
        migrations.AddIndex(model_name="shipmentrouteassignment", index=models.Index(fields=["client_operation_id"], name="operations__client__ff8af6_idx")),
        migrations.AddIndex(model_name="shipmentstatushistory", index=models.Index(fields=["shipment", "created_at"], name="operations__shipmen_c87f60_idx")),
        migrations.AddIndex(model_name="shipmentstatushistory", index=models.Index(fields=["new_status"], name="operations__new_sta_e0bc3b_idx")),
        migrations.AddIndex(model_name="shipmentstatushistory", index=models.Index(fields=["client_operation_id"], name="operations__client__6d8594_idx")),
    ]
