from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0004_scanner_cart_session_items_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="routerun",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="routerun",
            name="documents_printed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="routerun",
            name="ready_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="routerun",
            index=models.Index(fields=["closed_at"], name="operations__closed__886347_idx"),
        ),
    ]
