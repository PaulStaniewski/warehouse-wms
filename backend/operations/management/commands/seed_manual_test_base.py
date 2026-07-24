from datetime import time

from django.core.management.base import CommandError
from django.db import transaction

from operations.management.commands.seed_demo_data import Command as DemoSeedCommand
from operations.models import (
    AuditLog,
    BranchDispatchPolicy,
    CartPickedItem,
    DeliveryRoute,
    ExternalReturnDocument,
    Order,
    OrderLine,
    PickingShortage,
    PickingTask,
    ReturnBatch,
    RouteRoundSchedule,
    RouteRun,
    RouteRunOverrideHistory,
    SalesCorrection,
    ScannerCart,
    ScannerSession,
    Shipment,
    ShipmentLine,
    TransferDiscrepancy,
)
from warehouse.models import Branch, InventoryItem, Location, Product


class Command(DemoSeedCommand):
    help = "Seed stable master data for clean manual WMS workflow testing."

    demo_printer_code = "ZEBRA-01"

    def handle(self, *args, **options):
        self._require_clean_operational_database()

        with transaction.atomic():
            branches = self.create_branches()
            users = self.create_demo_users(branches)
            locations = self.create_locations(branches)
            products = self.create_products()
            inventory = self.create_inventory_items(branches, locations, products)
            carts = self.create_scanner_carts()
            routes = self.create_delivery_routes(branches)
            schedules = self.create_manual_route_schedules(routes)

        self.stdout.write(self.style.SUCCESS("Manual test master data seeded successfully."))
        self.stdout.write(f"Users: {len(users)}")
        self.stdout.write(f"Branches: {len(branches)}")
        self.stdout.write(f"Products: {len(products)}")
        self.stdout.write(f"Locations: {len(locations)}")
        self.stdout.write(f"Inventory: {len(inventory)}")
        self.stdout.write(f"Carts: {len(carts)}")
        self.stdout.write(f"Routes: {len(routes)}")
        self.stdout.write(f"Route schedules: {len(schedules)}")
        self.stdout.write(f"Demo printer: {self.demo_printer_code}")
        self.stdout.write("Demo password: demo12345")
        self.stdout.write("")
        self.stdout.write("Operational Orders: 0")
        self.stdout.write("Shipments: 0")
        self.stdout.write("RouteRuns: 0")
        self.stdout.write("PickingTasks: 0")
        self.stdout.write("Active Scanner Sessions: 0")

    def _require_clean_operational_database(self):
        operational_counts = {
            "Orders": Order.objects.count(),
            "OrderLines": OrderLine.objects.count(),
            "Shipments": Shipment.objects.count(),
            "ShipmentLines": ShipmentLine.objects.count(),
            "RouteRuns": RouteRun.objects.count(),
            "PickingTasks": PickingTask.objects.count(),
            "ScannerSessions": ScannerSession.objects.count(),
            "CartPickedItems": CartPickedItem.objects.count(),
            "PickingShortages": PickingShortage.objects.count(),
            "Returns": ReturnBatch.objects.count(),
            "ExternalReturns": ExternalReturnDocument.objects.count(),
            "Discrepancies": TransferDiscrepancy.objects.count(),
            "SalesCorrections": SalesCorrection.objects.count(),
            "RouteHistory": RouteRunOverrideHistory.objects.count(),
            "OperationalEvents": AuditLog.objects.count(),
        }
        populated = [f"{name}={count}" for name, count in operational_counts.items() if count]
        if populated:
            raise CommandError(
                "Manual test base requires a clean operational database; no data was changed. "
                + ", ".join(populated)
            )

    def create_manual_route_schedules(self, routes):
        schedules = {}
        for (branch_code, route_code), route in routes.items():
            route_number = int(route_code.split("-")[-1])
            departure_hour = 8 + ((route_number - 1) % 8)
            round_specs = [(1, time(departure_hour - 1, 30), time(departure_hour, 0))]
            if branch_code == "GDY" and route_code == "ROUTE-01":
                round_specs.append((2, time(12, 30), time(13, 0)))

            BranchDispatchPolicy.objects.update_or_create(
                branch=route.branch,
                defaults={
                    "max_routes_per_wave": 3,
                    "min_wave_gap_minutes": 10,
                },
            )
            for weekday in range(7):
                for round_number, cutoff_time, departure_time in round_specs:
                    schedule, _ = RouteRoundSchedule.objects.update_or_create(
                        route=route,
                        weekday=weekday,
                        round_number=round_number,
                        defaults={
                            "cutoff_time": cutoff_time,
                            "departure_time": departure_time,
                            "dispatch_wave": departure_time.strftime("%H:%M"),
                            "operational_label": "",
                            "is_active": True,
                        },
                    )
                    schedules[(branch_code, route_code, weekday, round_number)] = schedule
        return schedules
