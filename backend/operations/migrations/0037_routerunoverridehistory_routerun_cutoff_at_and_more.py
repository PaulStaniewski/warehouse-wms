import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0036_optional_shipment_route_reason"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RouteRunOverrideHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("previous_cutoff_at", models.DateTimeField(blank=True, null=True)),
                ("new_cutoff_at", models.DateTimeField(blank=True, null=True)),
                ("previous_planned_departure_at", models.DateTimeField(blank=True, null=True)),
                ("new_planned_departure_at", models.DateTimeField(blank=True, null=True)),
                ("previous_dispatch_wave", models.CharField(blank=True, max_length=32)),
                ("new_dispatch_wave", models.CharField(blank=True, max_length=32)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="routerun",
            name="cutoff_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="routerun",
            name="dispatch_wave",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="routerun",
            name="operational_identifier",
            field=models.CharField(blank=True, max_length=96),
        ),
        migrations.AddField(
            model_name="routerun",
            name="planned_departure_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="BranchDispatchPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("max_routes_per_wave", models.PositiveIntegerField(default=3)),
                ("min_wave_gap_minutes", models.PositiveIntegerField(default=10)),
                (
                    "branch",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dispatch_policy",
                        to="warehouse.branch",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "branch dispatch policies",
            },
        ),
        migrations.CreateModel(
            name="RouteRoundSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "weekday",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (0, "Monday"),
                            (1, "Tuesday"),
                            (2, "Wednesday"),
                            (3, "Thursday"),
                            (4, "Friday"),
                            (5, "Saturday"),
                            (6, "Sunday"),
                        ]
                    ),
                ),
                ("round_number", models.PositiveIntegerField()),
                ("cutoff_time", models.TimeField()),
                ("departure_time", models.TimeField()),
                ("dispatch_wave", models.CharField(max_length=32)),
                ("operational_label", models.CharField(blank=True, max_length=64)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "route",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="round_schedules",
                        to="operations.deliveryroute",
                    ),
                ),
            ],
            options={
                "ordering": ["route__branch__code", "weekday", "departure_time", "route__code", "round_number"],
            },
        ),
        migrations.AddField(
            model_name="routerun",
            name="schedule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="route_runs",
                to="operations.routeroundschedule",
            ),
        ),
        migrations.AddIndex(
            model_name="routerun",
            index=models.Index(fields=["operational_identifier"], name="operations__operati_d84638_idx"),
        ),
        migrations.AddIndex(
            model_name="routerun",
            index=models.Index(fields=["dispatch_wave"], name="operations__dispatc_63e2b3_idx"),
        ),
        migrations.AddField(
            model_name="routerunoverridehistory",
            name="changed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="route_run_overrides",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="routerunoverridehistory",
            name="route_run",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="override_history",
                to="operations.routerun",
            ),
        ),
        migrations.AddIndex(
            model_name="routeroundschedule",
            index=models.Index(fields=["route", "weekday", "is_active"], name="operations__route_i_ab0f14_idx"),
        ),
        migrations.AddIndex(
            model_name="routeroundschedule",
            index=models.Index(fields=["weekday", "dispatch_wave"], name="operations__weekday_d67577_idx"),
        ),
        migrations.AddConstraint(
            model_name="routeroundschedule",
            constraint=models.UniqueConstraint(fields=("route", "weekday", "round_number"), name="unique_route_round_schedule"),
        ),
        migrations.AddConstraint(
            model_name="routeroundschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(("cutoff_time__lt", models.F("departure_time"))),
                name="route_round_cutoff_before_departure",
            ),
        ),
        migrations.AddIndex(
            model_name="routerunoverridehistory",
            index=models.Index(fields=["route_run", "created_at"], name="operations__route_r_3842ca_idx"),
        ),
    ]
