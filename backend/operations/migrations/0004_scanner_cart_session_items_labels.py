from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0003_pickingtask_picked_prepared"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScannerCart",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[("available", "Available"), ("in_use", "In use")],
                        default="available",
                        max_length=32,
                    ),
                ),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="ScannerSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("worker_code", models.CharField(blank=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("closed", "Closed")],
                        default="active",
                        max_length=32,
                    ),
                ),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "cart",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sessions",
                        to="operations.scannercart",
                    ),
                ),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="CartPickedItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity_picked", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("quantity_prepared", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                (
                    "cart",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="picked_items",
                        to="operations.scannercart",
                    ),
                ),
                (
                    "picking_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cart_items",
                        to="operations.pickingtask",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cart_items",
                        to="warehouse.product",
                    ),
                ),
                (
                    "route_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cart_items",
                        to="operations.routerun",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="picked_items",
                        to="operations.scannersession",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="ScannerCustomerLabel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("printer_code", models.CharField(max_length=64)),
                ("printed_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scanner_labels",
                        to="operations.order",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="customer_labels",
                        to="operations.scannersession",
                    ),
                ),
            ],
            options={"ordering": ["-printed_at"]},
        ),
        migrations.AddIndex(model_name="scannercart", index=models.Index(fields=["code"], name="operations__code_8279de_idx")),
        migrations.AddIndex(model_name="scannercart", index=models.Index(fields=["status"], name="operations__status_d7650a_idx")),
        migrations.AddIndex(model_name="scannersession", index=models.Index(fields=["status"], name="operations__status_b3c4de_idx")),
        migrations.AddIndex(model_name="scannersession", index=models.Index(fields=["worker_code"], name="operations__worker__4ab27a_idx")),
        migrations.AddIndex(model_name="cartpickeditem", index=models.Index(fields=["session", "product"], name="operations__session_edfc9d_idx")),
        migrations.AddIndex(model_name="cartpickeditem", index=models.Index(fields=["cart"], name="operations__cart_id_d313a2_idx")),
        migrations.AddIndex(model_name="cartpickeditem", index=models.Index(fields=["route_run"], name="operations__route_r_bf96aa_idx")),
        migrations.AddIndex(model_name="scannercustomerlabel", index=models.Index(fields=["printer_code"], name="operations__printer_d6f913_idx")),
        migrations.AddIndex(model_name="scannercustomerlabel", index=models.Index(fields=["printed_at"], name="operations__printed_46a265_idx")),
        migrations.AddConstraint(
            model_name="cartpickeditem",
            constraint=models.UniqueConstraint(fields=("session", "picking_task"), name="unique_cart_item_per_session_task"),
        ),
        migrations.AddConstraint(
            model_name="cartpickeditem",
            constraint=models.CheckConstraint(check=models.Q(("quantity_picked__gte", 0)), name="cart_item_picked_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="cartpickeditem",
            constraint=models.CheckConstraint(check=models.Q(("quantity_prepared__gte", 0)), name="cart_item_prepared_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="scannercustomerlabel",
            constraint=models.UniqueConstraint(fields=("session", "order"), name="unique_scanner_label_per_session_order"),
        ),
    ]
