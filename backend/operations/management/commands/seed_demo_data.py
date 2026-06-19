from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from operations.models import (
    AuditLog,
    DeliveryRoute,
    Order,
    OrderLine,
    PickingTask,
    ReturnBatch,
    ReturnLine,
    RouteRun,
    StockMovement,
)
from warehouse.models import Branch, InventoryItem, Location, Product


class Command(BaseCommand):
    help = "Seed realistic demo data for the warehouse portfolio application."

    @transaction.atomic
    def handle(self, *args, **options):
        branches = self.create_branches()
        locations = self.create_locations(branches)
        products = self.create_products()
        inventory_items = self.create_inventory_items(branches, locations, products)
        delivery_routes = self.create_delivery_routes(branches)
        route_runs = self.create_route_runs(delivery_routes)
        orders, order_lines = self.create_orders(branches, products, route_runs)
        return_batch, return_lines = self.create_returns(branches, products)
        picking_tasks = self.create_picking_tasks(branches, locations, order_lines)
        stock_movements = self.create_stock_movements(branches, locations, products, inventory_items)
        audit_logs = self.create_audit_logs(orders, return_batch)

        self.stdout.write(self.style.SUCCESS("Demo warehouse data seeded successfully."))
        self.stdout.write(f"Branches: {len(branches)}")
        self.stdout.write(f"Locations: {len(locations)}")
        self.stdout.write(f"Products: {len(products)}")
        self.stdout.write(f"Inventory items: {len(inventory_items)}")
        self.stdout.write(f"Delivery routes: {len(delivery_routes)}")
        self.stdout.write(f"Route runs: {len(route_runs)}")
        self.stdout.write(f"Orders: {len(orders)}")
        self.stdout.write(f"Order lines: {len(order_lines)}")
        self.stdout.write(f"Returns: 1 batch, {len(return_lines)} lines")
        self.stdout.write(f"Picking tasks: {len(picking_tasks)}")
        self.stdout.write(f"Stock movements: {len(stock_movements)}")
        self.stdout.write(f"Audit logs: {len(audit_logs)}")

    def create_branches(self):
        branch_data = [
            {"code": "GDY", "name": "Magazyn Gdynia", "city": "Gdynia", "country": "Poland"},
            {"code": "GDA", "name": "Magazyn Gdańsk", "city": "Gdańsk", "country": "Poland"},
        ]

        branches = {}
        for data in branch_data:
            branch, _ = Branch.objects.update_or_create(
                code=data["code"],
                defaults={
                    "name": data["name"],
                    "city": data["city"],
                    "country": data["country"],
                    "is_active": True,
                },
            )
            branches[branch.code] = branch
        return branches

    def create_locations(self, branches):
        location_data = [
            ("GDY", "A-01-01", Location.LocationType.STORAGE),
            ("GDY", "A-01-02", Location.LocationType.STORAGE),
            ("GDY", "A-02-01", Location.LocationType.PICKING),
            ("GDY", "RET-01", Location.LocationType.RETURNS),
            ("GDY", "PACK-01", Location.LocationType.SHIPPING),
            ("GDA", "B-01-01", Location.LocationType.STORAGE),
            ("GDA", "B-01-02", Location.LocationType.PICKING),
            ("GDA", "RET-01", Location.LocationType.RETURNS),
            ("GDA", "PACK-01", Location.LocationType.SHIPPING),
        ]

        locations = {}
        for branch_code, code, location_type in location_data:
            location, _ = Location.objects.update_or_create(
                branch=branches[branch_code],
                code=code,
                defaults={
                    "name": code,
                    "location_type": location_type,
                    "is_active": True,
                },
            )
            locations[(branch_code, code)] = location
        return locations

    def create_products(self):
        product_data = [
            ("FILTR-001", "Filtr oleju demo", "590000000001", "pcs"),
            ("OLEJ-001", "Olej 5W30 demo", "590000000002", "pcs"),
            ("KLOCKI-001", "Klocki hamulcowe demo", "590000000003", "pcs"),
            ("WYCIER-001", "Wycieraczki demo", "590000000004", "pcs"),
        ]

        products = {}
        for sku, name, barcode, unit_of_measure in product_data:
            product, _ = Product.objects.update_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "barcode": barcode,
                    "unit_of_measure": unit_of_measure,
                    "is_active": True,
                },
            )
            products[sku] = product
        return products

    def create_inventory_items(self, branches, locations, products):
        inventory_data = [
            ("GDY", "A-01-01", "FILTR-001", "10", "0"),
            ("GDY", "A-01-02", "OLEJ-001", "24", "0"),
            ("GDY", "A-02-01", "KLOCKI-001", "6", "0"),
            ("GDY", "RET-01", "WYCIER-001", "3", "0"),
            ("GDA", "B-01-01", "FILTR-001", "8", "0"),
            ("GDA", "B-01-02", "OLEJ-001", "12", "0"),
        ]

        inventory_items = {}
        for branch_code, location_code, sku, on_hand, reserved in inventory_data:
            item, _ = InventoryItem.objects.update_or_create(
                branch=branches[branch_code],
                location=locations[(branch_code, location_code)],
                product=products[sku],
                defaults={
                    "quantity_on_hand": Decimal(on_hand),
                    "quantity_reserved": Decimal(reserved),
                },
            )
            inventory_items[(branch_code, location_code, sku)] = item
        return inventory_items

    def create_delivery_routes(self, branches):
        route_data = []
        route_data.extend(("GDY", f"ROUTE-{number:02d}", f"Trasa {number}") for number in range(1, 11))
        route_data.extend(("GDA", f"ROUTE-{number:02d}", f"Trasa Gdańsk {number}") for number in range(1, 4))

        routes = {}
        for branch_code, code, name in route_data:
            route, _ = DeliveryRoute.objects.update_or_create(
                branch=branches[branch_code],
                code=code,
                defaults={
                    "name": name,
                    "is_active": True,
                },
            )
            routes[(branch_code, code)] = route
        return routes

    def create_route_runs(self, delivery_routes):
        now = timezone.localtime()
        today = timezone.localdate()

        def as_time(delta: timedelta):
            return (now + delta).time().replace(microsecond=0)

        run_data = [
            (
                "GDY",
                "ROUTE-01",
                1,
                as_time(timedelta(minutes=0)),
                as_time(timedelta(minutes=1)),
                as_time(timedelta(minutes=10)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-01",
                2,
                as_time(timedelta(hours=3, minutes=50)),
                as_time(timedelta(hours=3, minutes=51)),
                as_time(timedelta(hours=4)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-02",
                1,
                as_time(timedelta(hours=1, minutes=50)),
                as_time(timedelta(hours=1, minutes=51)),
                as_time(timedelta(hours=2)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-03",
                1,
                as_time(timedelta(hours=5, minutes=50)),
                as_time(timedelta(hours=5, minutes=51)),
                as_time(timedelta(hours=6)),
                RouteRun.Status.CLOSED,
            ),
            (
                "GDA",
                "ROUTE-01",
                1,
                as_time(timedelta(hours=2, minutes=50)),
                as_time(timedelta(hours=2, minutes=51)),
                as_time(timedelta(hours=3)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDA",
                "ROUTE-02",
                1,
                as_time(timedelta(hours=4, minutes=50)),
                as_time(timedelta(hours=4, minutes=51)),
                as_time(timedelta(hours=5)),
                RouteRun.Status.OPEN,
            ),
        ]

        route_runs = {}
        for branch_code, route_code, run_number, cutoff_time, sync_time, departure_time, status in run_data:
            route = delivery_routes[(branch_code, route_code)]
            route_run, _ = RouteRun.objects.update_or_create(
                route=route,
                service_date=today,
                run_number=run_number,
                defaults={
                    "order_cutoff_time": cutoff_time,
                    "sync_time": sync_time,
                    "departure_time": departure_time,
                    "status": status,
                },
            )
            route_runs[(branch_code, route_code, run_number)] = route_run
        return route_runs

    def create_orders(self, branches, products, route_runs):
        orders_data = [
            (
                "AX-ORDER-0001",
                "GDY",
                "Demo Client One",
                ("GDY", "ROUTE-01", 1),
                [("FILTR-001", 1, "2"), ("OLEJ-001", 2, "4")],
            ),
            ("AX-ORDER-0002", "GDY", "Demo Client Two", ("GDY", "ROUTE-01", 2), [("KLOCKI-001", 1, "1")]),
            ("AX-ORDER-0003", "GDA", "Demo Client Three", ("GDA", "ROUTE-01", 1), [("FILTR-001", 1, "3")]),
        ]

        orders = {}
        order_lines = {}
        for reference, branch_code, customer_name, route_run_key, lines in orders_data:
            order, _ = Order.objects.update_or_create(
                external_reference=reference,
                defaults={
                    "branch": branches[branch_code],
                    "route_run": route_runs[route_run_key],
                    "customer_name": customer_name,
                    "status": Order.Status.IMPORTED,
                    "requested_ship_date": None,
                },
            )
            orders[reference] = order

            for sku, line_number, quantity in lines:
                line, _ = OrderLine.objects.update_or_create(
                    order=order,
                    line_number=line_number,
                    defaults={
                        "product": products[sku],
                        "quantity_ordered": Decimal(quantity),
                        "quantity_picked": Decimal("0"),
                    },
                )
                order_lines[(reference, line_number)] = line

        return orders, order_lines

    def create_returns(self, branches, products):
        return_batch, _ = ReturnBatch.objects.update_or_create(
            reference="RET-GDY-0001",
            defaults={
                "branch": branches["GDY"],
                "status": ReturnBatch.Status.VERIFIED,
                "received_at": timezone.now(),
            },
        )

        return_data = [
            (1, "WYCIER-001", "2", ReturnLine.Condition.SELLABLE),
            (2, "FILTR-001", "1", ReturnLine.Condition.DAMAGED),
        ]

        return_lines = {}
        for line_number, sku, quantity, condition in return_data:
            line, _ = ReturnLine.objects.update_or_create(
                return_batch=return_batch,
                line_number=line_number,
                defaults={
                    "product": products[sku],
                    "quantity": Decimal(quantity),
                    "condition": condition,
                },
            )
            return_lines[line_number] = line

        return return_batch, return_lines

    def create_picking_tasks(self, branches, locations, order_lines):
        picking_data = [
            (("AX-ORDER-0001", 1), "GDY", "A-01-01", PickingTask.Status.OPEN),
            (("AX-ORDER-0001", 2), "GDY", "A-01-02", PickingTask.Status.ASSIGNED),
            (("AX-ORDER-0002", 1), "GDY", "A-02-01", PickingTask.Status.OPEN),
            (("AX-ORDER-0003", 1), "GDA", "B-01-01", PickingTask.Status.OPEN),
        ]

        picking_tasks = {}
        for order_line_key, branch_code, location_code, status in picking_data:
            order_line = order_lines[order_line_key]
            task, _ = PickingTask.objects.update_or_create(
                order_line=order_line,
                source_location=locations[(branch_code, location_code)],
                defaults={
                    "branch": branches[branch_code],
                    "assigned_to": None,
                    "status": status,
                    "quantity_to_pick": order_line.quantity_ordered,
                    "quantity_picked": Decimal("0"),
                },
            )
            picking_tasks[order_line_key] = task
        return picking_tasks

    def create_stock_movements(self, branches, locations, products, inventory_items):
        movement_data = [
            {
                "reference": "RCPT-GDY-0001",
                "branch": "GDY",
                "product": "FILTR-001",
                "inventory": ("GDY", "A-01-01", "FILTR-001"),
                "source": None,
                "destination": ("GDY", "A-01-01"),
                "movement_type": StockMovement.MovementType.RECEIPT,
                "quantity": "10",
            },
            {
                "reference": "RET-GDY-0001",
                "branch": "GDY",
                "product": "WYCIER-001",
                "inventory": ("GDY", "RET-01", "WYCIER-001"),
                "source": None,
                "destination": ("GDY", "RET-01"),
                "movement_type": StockMovement.MovementType.RETURN,
                "quantity": "2",
            },
            {
                "reference": "ADJ-GDA-0001",
                "branch": "GDA",
                "product": "OLEJ-001",
                "inventory": ("GDA", "B-01-02", "OLEJ-001"),
                "source": None,
                "destination": ("GDA", "B-01-02"),
                "movement_type": StockMovement.MovementType.ADJUSTMENT,
                "quantity": "1",
            },
        ]

        movements = {}
        for data in movement_data:
            source_location = locations[data["source"]] if data["source"] else None
            destination_location = locations[data["destination"]] if data["destination"] else None
            movement, _ = StockMovement.objects.update_or_create(
                reference=data["reference"],
                branch=branches[data["branch"]],
                product=products[data["product"]],
                movement_type=data["movement_type"],
                defaults={
                    "inventory_item": inventory_items[data["inventory"]],
                    "source_location": source_location,
                    "destination_location": destination_location,
                    "quantity": Decimal(data["quantity"]),
                    "performed_by": None,
                },
            )
            movements[(data["reference"], data["product"])] = movement
        return movements

    def create_audit_logs(self, orders, return_batch):
        audit_data = [
            (
                AuditLog.ActionType.SYSTEM,
                "DemoData",
                "seed_demo_data",
                "System created demo warehouse data.",
            ),
            (
                AuditLog.ActionType.CREATE,
                "Order",
                orders["AX-ORDER-0001"].external_reference,
                "Order AX-ORDER-0001 imported from ERP demo feed.",
            ),
            (
                AuditLog.ActionType.STATUS_CHANGE,
                "ReturnBatch",
                return_batch.reference,
                "Return batch RET-GDY-0001 verified.",
            ),
        ]

        audit_logs = {}
        for action_type, entity_name, entity_id, message in audit_data:
            log, _ = AuditLog.objects.update_or_create(
                action_type=action_type,
                entity_name=entity_name,
                entity_id=entity_id,
                defaults={
                    "actor": None,
                    "message": message,
                },
            )
            audit_logs[(action_type, entity_name, entity_id)] = log
        return audit_logs
