import uuid

from django.db import migrations, models

import operations.models


def backfill_scan_codes(apps, schema_editor):
    ScannerCustomerLabel = apps.get_model("operations", "ScannerCustomerLabel")
    for label in ScannerCustomerLabel.objects.filter(scan_code__isnull=True).order_by("id"):
        while True:
            scan_code = f"CL-{uuid.uuid4().hex[:10].upper()}"
            if not ScannerCustomerLabel.objects.filter(scan_code=scan_code).exists():
                break
        label.scan_code = scan_code
        label.save(update_fields=["scan_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0006_pickingjob_pickingjobtask_cartworksession_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scannercustomerlabel",
            name="scan_code",
            field=models.CharField(blank=True, editable=False, max_length=32, null=True),
        ),
        migrations.RunPython(backfill_scan_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="scannercustomerlabel",
            name="scan_code",
            field=models.CharField(
                default=operations.models.generate_customer_label_scan_code,
                editable=False,
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AddIndex(
            model_name="scannercustomerlabel",
            index=models.Index(fields=["scan_code"], name="operations__scan_co_50d407_idx"),
        ),
    ]
