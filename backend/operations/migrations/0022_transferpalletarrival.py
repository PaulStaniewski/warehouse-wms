import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_arrivals(apps, schema_editor):
    TransferPallet = apps.get_model("operations", "TransferPallet")
    TransferPalletArrival = apps.get_model("operations", "TransferPalletArrival")
    historical = TransferPallet.objects.filter(
        models.Q(receiving_started_at__isnull=False)
        | models.Q(status__in=["receiving", "received", "closed_with_discrepancy"])
    )
    for pallet in historical.iterator():
        scanned_at = pallet.receiving_started_at or pallet.received_at or pallet.updated_at
        TransferPalletArrival.objects.get_or_create(pallet=pallet, defaults={"scanned_at": scanned_at})


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("operations", "0021_add_auditlog_expected_checked_quantity"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransferPalletArrival",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("scanned_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("scanned_by_worker_code", models.CharField(blank=True, max_length=64)),
                ("client_operation_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                ("pallet", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="arrival", to="operations.transferpallet")),
                ("scanned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="transfer_pallet_arrivals", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-scanned_at"]},
        ),
        migrations.AddIndex(
            model_name="transferpalletarrival",
            index=models.Index(fields=["scanned_at"], name="operations__scanned_d09602_idx"),
        ),
        migrations.RunPython(backfill_arrivals, migrations.RunPython.noop),
    ]
