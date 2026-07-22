from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.utils import timezone

from accounts.models import UserBranchMembership
from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkSession,
    CartWorkParticipant,
    DeliveryRoute,
    ExternalReturnDocument,
    ExternalReturnDocumentLine,
    InterBranchTransfer,
    Order,
    OrderLine,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingJobTask,
    PickingShortage,
    PickingShortageAllocation,
    PickingTask,
    PickingTaskClaim,
    PickingTaskReallocation,
    ReplenishmentRequest,
    ReturnAction,
    ReturnBatch,
    ReturnLine,
    BranchDispatchPolicy,
    RouteRoundSchedule,
    RouteRun,
    SalesCorrection,
    SalesCorrectionLine,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    Shipment,
    ShipmentLine,
    ShipmentLineQuantityAdjustment,
    ShipmentRouteAssignment,
    ShipmentStatusHistory,
    StockMovement,
    TransferDiscrepancy,
    TransferDiscrepancyItem,
    TransferDiscrepancyManualReconciliationDecision,
    TransferDiscrepancyReconciliation,
    TransferDiscrepancyRecovery,
    TransferDiscrepancyShortageConfirmation,
    TransferDiscrepancySourceReview,
    TransferDiscrepancySourceStockRecovery,
    TransferDiscrepancySourceStockVerification,
    TransferDiscrepancySourceStockVerificationItem,
    TransferDiscrepancyTransitInvestigation,
    TransferPallet,
    TransferPalletArrival,
    TransferPalletItem,
)
from operations.route_services import operational_identifier
from warehouse.models import Branch, InventoryItem, Location, Product


class Command(BaseCommand):
    help = "Seed realistic demo data for the warehouse portfolio application."

    def handle(self, *args, **options):
        self.seed_now = timezone.localtime().replace(microsecond=0)
        with transaction.atomic():
            self.cleanup_demo_workflow()
            branches = self.create_branches()
            demo_users = self.create_demo_users(branches)
            locations = self.create_locations(branches)
            products = self.create_products()
            inventory_items = self.create_inventory_items(branches, locations, products)
            delivery_routes = self.create_delivery_routes(branches)
            route_runs = self.create_route_runs(delivery_routes)
            orders, order_lines = self.create_orders(branches, products, route_runs)
            return_batch, return_lines = self.create_returns(branches, products)
            external_return, external_return_lines = self.create_external_return_documents(branches, products)
            picking_tasks = self.create_picking_tasks(branches, locations, order_lines)
            scanner_carts = self.create_scanner_carts()
            transfer_pallets = self.create_transfer_pallets(branches, products)
            shipments = self.create_shipments(branches, orders, order_lines, route_runs, transfer_pallets)
            self.create_scanner_demo_work(branches, demo_users, scanner_carts, shipments)
            stock_movements = self.create_stock_movements(branches, locations, products, inventory_items)
            audit_logs = self.create_audit_logs(orders, return_batch)

        self.stdout.write(self.style.SUCCESS("Demo warehouse data seeded successfully."))
        self.stdout.write(f"Branches: {len(branches)}")
        self.stdout.write(f"Demo users: {len(demo_users)}")
        self.stdout.write(f"Locations: {len(locations)}")
        self.stdout.write(f"Products: {len(products)}")
        self.stdout.write(f"Inventory items: {len(inventory_items)}")
        self.stdout.write(f"Delivery routes: {len(delivery_routes)}")
        self.stdout.write(f"Route runs: {len(route_runs)}")
        self.stdout.write(f"Orders: {len(orders)}")
        self.stdout.write(f"Order lines: {len(order_lines)}")
        self.stdout.write(f"Returns: 1 batch, {len(return_lines)} lines")
        self.stdout.write(f"External return documents: 1 document, {len(external_return_lines)} lines")
        self.stdout.write(f"Picking tasks: {len(picking_tasks)}")
        self.stdout.write(f"Scanner carts: {len(scanner_carts)}")
        self.stdout.write(f"Transfer pallets: {len(transfer_pallets)}")
        self.stdout.write(f"Shipments: {len(shipments)}")
        self.stdout.write(f"Stock movements: {len(stock_movements)}")
        self.stdout.write(f"Audit logs: {len(audit_logs)}")
        self.stdout.write("Demo password: demo12345")
        self.write_scenario_report(branches, shipments, route_runs)

    def write_scenario_report(self, branches, shipments, route_runs):
        scenarios = [
            ("READY_BEFORE_CUTOFF", "SHP-GDY-0003", "neutral", "prepared", "Wait until cutoff; confirm the route changes to ready."),
            ("READY_AFTER_CUTOFF", "SHP-GDY-READY-AFTER", "ready", "prepared", "Verify the green ready state."),
            ("INCOMPLETE_AFTER_CUTOFF", "SHP-GDY-0001", "cutoff_warning", "not_started", "Complete the remaining pick and verify ready."),
            ("DELAYED_OPEN", "SHP-GDY-0008", "delayed", "not_started", "Keep the run open and verify red delayed."),
            ("ACTIVE_PICKING", "SHP-GDY-0002", "cutoff_warning", "started", "Continue the partially picked line."),
            ("PARTIAL_PICKING", "SHP-GDY-0002", "cutoff_warning", "started", "Continue ACTIVE_PICKING from its half-picked quantity."),
            ("QUANTITY_REMOVAL", "SHP-GDY-0006", "neutral", "not_started", "Remove quantity from a shipment line."),
            ("PICKING_SHORTAGE", "SHP-GDY-0001", "cutoff_warning", "not_started", "Continue INCOMPLETE_AFTER_CUTOFF by reporting a shortage."),
            ("CLOSED_AND_NEXT_ROUND", "SHP-GDY-0007", "neutral", "not_started", "Compare the closed first round with this open next round."),
            ("ROUTE_REASSIGNMENT", "SHP-GDY-REASSIGN", "neutral", "not_started", "Move this shipment to another eligible open RouteRun."),
            ("SCANNER_UNSTARTED", "SHP-GDY-REASSIGN", "neutral", "not_started", "Continue ROUTE_REASSIGNMENT by selecting its exact RouteRun ID in Scanner."),
            ("SCANNER_ACTIVE_PICKING", "SHP-GDY-0002", "cutoff_warning", "started", "Continue ACTIVE_PICKING in the active WOZEK-03 session."),
            ("SCANNER_PARTIAL_PICK", "SHP-GDY-0002", "cutoff_warning", "started", "Continue SCANNER_ACTIVE_PICKING and verify the remaining half quantity."),
            ("SCANNER_ZERO_EFFECTIVE_EXCLUDED", "SHP-GDY-0006", "neutral", "zero_effective_excluded", "Continue QUANTITY_REMOVAL and verify line 2 is not pickable."),
            ("SCANNER_PREPARED_EXCLUDED", "SHP-GDY-0003", "neutral", "prepared", "Verify the route remains on Route Monitor and is absent from Scanner Proformas."),
            ("SCANNER_CLOSED_ROUTE_EXCLUDED", "SHP-GDY-0004", "muted", "prepared_historical", "Verify the closed RouteRun is absent from both active lists."),
        ]
        self.stdout.write("Operational demo scenarios (demo-owned records rebuilt):")
        for name, reference, attention, line_state, action in scenarios:
            shipment = shipments[reference]
            route_identifier = shipment.route_run.operational_identifier if shipment.route_run else "unassigned"
            self.stdout.write(
                f"- {name}: route={route_identifier}; shipment={reference}; "
                f"attention={attention}; line_state={line_state}; action={action}"
            )
        self.stdout.write(
            "Operational totals: "
            f"branch={branches['GDY'].code}, "
            f"active_routes={RouteRun.objects.filter(pk__in=[run.pk for run in route_runs.values()], route__branch=branches['GDY']).exclude(status__in=[RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED]).count()}, "
            f"closed_routes={RouteRun.objects.filter(pk__in=[run.pk for run in route_runs.values()], route__branch=branches['GDY'], status=RouteRun.Status.CLOSED).count()}, "
            f"shipments={Shipment.objects.filter(branch=branches['GDY']).count()}, "
            f"shipment_lines={ShipmentLine.objects.filter(shipment__branch=branches['GDY']).count()}, "
            f"picking_tasks={PickingTask.objects.filter(branch=branches['GDY']).count()}, "
            f"active_scanner_sessions={ScannerSession.objects.filter(status=ScannerSession.Status.ACTIVE).count()}"
        )
    def cleanup_demo_workflow(self):
        demo_cart_codes = ["WOZEK-01", "WOZEK-02", "WOZEK-03"]
        demo_transfer_refs = ["IBT-GDA-GDY-001", "IBT-GDA-GDY-DISC-001", "IBT-SHP-GDA-GDY-001"]
        demo_pallet_codes = ["PAL-GDA-GDY-001", "PAL-GDA-GDY-DISC-001", "PAL-SHP-GDA-GDY-001"]
        demo_shipment_refs = [
            "SHP-GDY-0001",
            "SHP-GDY-0002",
            "SHP-GDY-0003",
            "SHP-GDY-0004",
            "SHP-GDY-0005",
            "SHP-GDA-GDY-0001",
            "SHP-GDA-GDY-0002",
            "SHP-GDY-0006",
            "SHP-GDY-0007",
            "SHP-GDY-0008",
            "SHP-GDY-READY-AFTER",
            "SHP-GDY-REASSIGN",
        ]
        demo_order_refs = [
            "AX-ORDER-0001",
            "AX-ORDER-0002",
            "AX-ORDER-0003",
            "AX-ORDER-0004",
            "AX-ORDER-0005",
            "AX-ORDER-LABEL-TEST",
            "AX-SALE-RET-001",
            "AX-SALE-RET-002",
            "AX-SALE-RET-003",
            "AX-ORDER-READY-AFTER",
            "AX-ORDER-REASSIGN",
        ]
        demo_external_return_refs = ["ZW1103872"]
        demo_sales_correction_refs = ["SC-000001", "SC-000002", "SC-000003"]
        cart_code_filter = models.Q()
        related_cart_code_filter = models.Q()
        job_cart_code_filter = models.Q()
        for code in demo_cart_codes:
            cart_code_filter |= models.Q(code__iexact=code)
            related_cart_code_filter |= models.Q(cart__code__iexact=code)
            job_cart_code_filter |= models.Q(cart_work_sessions__cart__code__iexact=code)

        demo_sessions = ScannerSession.objects.filter(related_cart_code_filter)
        demo_cart_work = CartWorkSession.objects.filter(related_cart_code_filter)
        demo_jobs = PickingJob.objects.filter(
            job_cart_code_filter
            | models.Q(job_tasks__picking_task__order_line__order__external_reference__in=demo_order_refs)
        ).distinct()

        demo_shortages = PickingShortage.objects.filter(order__external_reference__in=demo_order_refs)
        demo_return_actions = ReturnAction.objects.filter(document__external_reference__in=demo_external_return_refs)
        demo_shipments = Shipment.objects.filter(reference__in=demo_shipment_refs)
        ShipmentRouteAssignment.objects.filter(shipment__in=demo_shipments).delete()
        ShipmentStatusHistory.objects.filter(shipment__in=demo_shipments).delete()
        ShipmentLineQuantityAdjustment.objects.filter(shipment__in=demo_shipments).delete()
        ShipmentLine.objects.filter(shipment__in=demo_shipments).delete()
        demo_shipments.delete()
        StockMovement.objects.filter(
            models.Q(external_return_action__in=demo_return_actions)
            | models.Q(reference__in=demo_external_return_refs)
            | models.Q(reference__in=demo_sales_correction_refs)
        ).delete()
        ReturnAction.objects.filter(document__external_reference__in=demo_external_return_refs).delete()
        ExternalReturnDocument.objects.filter(external_reference__in=demo_external_return_refs).delete()
        SalesCorrection.objects.filter(
            models.Q(reference__in=demo_sales_correction_refs)
            | models.Q(lines__source_order__external_reference__in=demo_order_refs)
        ).distinct().delete()
        PickingShortageAllocation.objects.filter(shortage__in=demo_shortages).delete()
        PickingTaskReallocation.objects.filter(
            models.Q(original_picking_task__order_line__order__external_reference__in=demo_order_refs)
            | models.Q(replacement_picking_task__order_line__order__external_reference__in=demo_order_refs)
        ).delete()
        ReplenishmentRequest.objects.filter(picking_shortage__in=demo_shortages).delete()
        ReplenishmentRequest.objects.filter(picking_task__order_line__order__external_reference__in=demo_order_refs).delete()
        StockMovement.objects.filter(reference__startswith="PS-").delete()
        demo_shortages.delete()
        ScannerCustomerLabel.objects.filter(session__in=demo_sessions).delete()
        CartPickedItem.objects.filter(
            models.Q(session__in=demo_sessions) | models.Q(cart_work_session__in=demo_cart_work)
        ).delete()
        demo_cart_work.delete()
        demo_sessions.update(status=ScannerSession.Status.CLOSED, ended_at=timezone.now())
        ScannerCart.objects.filter(cart_code_filter).update(status=ScannerCart.Status.AVAILABLE)
        demo_jobs.delete()
        PickingTask.objects.filter(order_line__order__external_reference__in=demo_order_refs).delete()

        demo_pallets = TransferPallet.objects.filter(scan_code__in=demo_pallet_codes)
        TransferPalletArrival.objects.filter(pallet__in=demo_pallets).delete()
        demo_discrepancies = TransferDiscrepancy.objects.filter(pallet__in=demo_pallets)
        demo_reconciliations = TransferDiscrepancyReconciliation.objects.filter(discrepancy__in=demo_discrepancies)
        demo_verifications = TransferDiscrepancySourceStockVerification.objects.filter(
            reconciliation__in=demo_reconciliations
        )
        TransferDiscrepancySourceStockRecovery.objects.filter(verification__in=demo_verifications).delete()
        TransferDiscrepancySourceStockVerificationItem.objects.filter(verification__in=demo_verifications).delete()
        demo_verifications.delete()
        TransferDiscrepancyTransitInvestigation.objects.filter(reconciliation__in=demo_reconciliations).delete()
        TransferDiscrepancyManualReconciliationDecision.objects.filter(reconciliation__in=demo_reconciliations).delete()
        TransferDiscrepancyReconciliation.objects.filter(discrepancy__in=demo_discrepancies).delete()
        TransferDiscrepancySourceReview.objects.filter(discrepancy__in=demo_discrepancies).delete()
        TransferDiscrepancyShortageConfirmation.objects.filter(discrepancy__in=demo_discrepancies).delete()
        TransferDiscrepancyRecovery.objects.filter(discrepancy__in=demo_discrepancies).delete()
        TransferDiscrepancyItem.objects.filter(discrepancy__in=demo_discrepancies).delete()
        demo_discrepancies.delete()
        PalletReceivingScan.objects.filter(pallet__in=demo_pallets).delete()
        PalletReceivingSession.objects.filter(pallet__in=demo_pallets).delete()
        TransferPalletItem.objects.filter(pallet__in=demo_pallets).delete()
        StockMovement.objects.filter(reference__in=demo_pallet_codes).delete()
        StockMovement.objects.filter(reference__startswith="DIS-").delete()
        StockMovement.objects.filter(reference__startswith="SSV-").delete()
        demo_pallets.delete()
        InterBranchTransfer.objects.filter(reference__in=demo_transfer_refs).delete()
        InventoryItem.objects.filter(location__code__iexact="UNCONFIRMED", location__branch__code="GDY").delete()
        InventoryItem.objects.filter(location__code__iexact="UNCONFIRMED", location__branch__code="GDA").delete()
        InventoryItem.objects.filter(location__code__iexact="A-03-01", location__branch__code="GDY").delete()
        InventoryItem.objects.filter(location__code__iexact="A-03-01", location__branch__code="GDA").delete()

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

    def create_demo_users(self, branches):
        user_data = [
            ("GDY_WORKER", "GDY", UserBranchMembership.Role.WORKER),
            ("GDY_LEADER", "GDY", UserBranchMembership.Role.LEADER),
            ("GDA_WORKER", "GDA", UserBranchMembership.Role.WORKER),
            ("GDA_LEADER", "GDA", UserBranchMembership.Role.LEADER),
            ("DEMO", "GDY", UserBranchMembership.Role.LEADER),
            ("DEMO", "GDA", UserBranchMembership.Role.LEADER),
        ]
        users = {}
        User = get_user_model()
        for username, branch_code, role in user_data:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "is_active": True,
                    "email": f"{username.lower()}@example.local",
                },
            )
            if created or not user.has_usable_password():
                user.set_password("demo12345")
                user.save(update_fields=["password"])
            UserBranchMembership.objects.update_or_create(
                user=user,
                branch=branches[branch_code],
                defaults={"role": role},
            )
            users[username] = user
        return users

    def create_locations(self, branches):
        location_data = [
            ("GDY", "A-01-01", Location.LocationType.STORAGE),
            ("GDY", "A-01-02", Location.LocationType.STORAGE),
            ("GDY", "A-02-01", Location.LocationType.PICKING),
            ("GDY", "A-03-01", Location.LocationType.STORAGE),
            ("GDY", "B-02-01", Location.LocationType.PICKING),
            ("GDY", "C-01-03", Location.LocationType.PICKING),
            ("GDY", "RETURNS", Location.LocationType.RETURNS),
            ("GDY", "RET-01", Location.LocationType.RETURNS),
            ("GDY", "PACK-01", Location.LocationType.SHIPPING),
            ("GDY", "UNCONFIRMED", Location.LocationType.RECEIVING),
            ("GDA", "A-01-01", Location.LocationType.STORAGE),
            ("GDA", "B-01-01", Location.LocationType.STORAGE),
            ("GDA", "B-01-02", Location.LocationType.PICKING),
            ("GDA", "A-03-01", Location.LocationType.STORAGE),
            ("GDA", "RETURNS", Location.LocationType.RETURNS),
            ("GDA", "RET-01", Location.LocationType.RETURNS),
            ("GDA", "PACK-01", Location.LocationType.SHIPPING),
            ("GDA", "UNCONFIRMED", Location.LocationType.RECEIVING),
        ]

        locations = {}
        for branch_code, code, location_type in location_data:
            location, _ = Location.objects.update_or_create(
                branch=branches[branch_code],
                code=code,
                defaults={
                    "name": "Returns Area" if code == "RETURNS" else code,
                    "location_type": location_type,
                    "is_active": True,
                },
            )
            locations[(branch_code, code)] = location
        return locations

    def create_products(self):
        product_data = [
            ("FILTR-001", "Oil Filter RFO-301", "Ravenol", "High-performance oil filter for selected passenger vehicles.", "/products/oil-filter.svg", "590000000001", "pcs"),
            ("OLEJ-001", "Motor Oil 5W-30", "Lubrix", "Synthetic engine oil supplied in a 5 litre container.", "/products/motor-oil.svg", "590000000002", "pcs"),
            ("KLOCKI-001", "Front Brake Pads", "Brakemax", "Front axle brake pad set for selected passenger vehicles.", "/products/brake-pads.svg", "590000000003", "pcs"),
            ("WYCIER-001", "Wiper Blade Set", "Clearway", "Durable front windscreen wiper blade set.", "", "590000000004", "pcs"),
        ]

        products = {}
        for sku, name, brand, description, image_url, barcode, unit_of_measure in product_data:
            product, _ = Product.objects.update_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "brand": brand,
                    "description": description,
                    "image_url": image_url,
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
            ("GDY", "A-01-02", "OLEJ-001", "5", "0"),
            ("GDY", "B-02-01", "OLEJ-001", "2", "0"),
            ("GDY", "C-01-03", "OLEJ-001", "2", "0"),
            ("GDY", "A-02-01", "FILTR-001", "12", "0"),
            ("GDY", "A-02-01", "KLOCKI-001", "6", "0"),
            ("GDY", "RETURNS", "FILTR-001", "0", "0"),
            ("GDY", "RETURNS", "OLEJ-001", "0", "0"),
            ("GDY", "RET-01", "WYCIER-001", "3", "0"),
            ("GDA", "B-01-01", "FILTR-001", "8", "0"),
            ("GDA", "B-01-02", "OLEJ-001", "12", "0"),
            ("GDA", "A-03-01", "FILTR-001", "4", "0"),
            ("GDA", "RETURNS", "FILTR-001", "0", "0"),
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
        now = self.seed_now
        today = now.date()

        def as_time(delta: timedelta):
            return (now + delta).time().replace(microsecond=0)

        def as_datetime(day, value):
            return timezone.make_aware(datetime.combine(day, value), timezone.get_current_timezone())

        run_data = [
            (
                "GDY",
                "ROUTE-01",
                1,
                0,
                as_time(timedelta(minutes=-30)),
                as_time(timedelta(minutes=-20)),
                as_time(timedelta(minutes=30)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-01",
                2,
                0,
                as_time(timedelta(hours=3, minutes=50)),
                as_time(timedelta(hours=3, minutes=51)),
                as_time(timedelta(hours=4)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-02",
                1,
                0,
                as_time(timedelta(minutes=30)),
                as_time(timedelta(minutes=40)),
                as_time(timedelta(minutes=60)),
                RouteRun.Status.READY_TO_CLOSE,
            ),
            (
                "GDY",
                "ROUTE-03",
                1,
                0,
                as_time(timedelta(hours=5, minutes=50)),
                as_time(timedelta(hours=5, minutes=51)),
                as_time(timedelta(hours=6)),
                RouteRun.Status.CLOSED,
            ),
            (
                "GDA",
                "ROUTE-01",
                1,
                0,
                as_time(timedelta(hours=2, minutes=50)),
                as_time(timedelta(hours=2, minutes=51)),
                as_time(timedelta(hours=3)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDA",
                "ROUTE-02",
                1,
                0,
                as_time(timedelta(hours=4, minutes=50)),
                as_time(timedelta(hours=4, minutes=51)),
                as_time(timedelta(hours=5)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-04",
                1,
                0,
                as_time(timedelta(hours=6, minutes=50)),
                as_time(timedelta(hours=6, minutes=51)),
                as_time(timedelta(hours=7)),
                RouteRun.Status.OPEN,
            ),
            (
                "GDY",
                "ROUTE-05",
                1,
                0,
                as_time(timedelta(minutes=-60)),
                as_time(timedelta(minutes=-45)),
                as_time(timedelta(minutes=-15)),
                RouteRun.Status.OPEN,
            ),
            ("GDY", "ROUTE-06", 1, 0, as_time(timedelta(minutes=-30)), as_time(timedelta(minutes=-20)), as_time(timedelta(minutes=30)), RouteRun.Status.OPEN),
            ("GDY", "ROUTE-07", 1, 0, as_time(timedelta(minutes=90)), as_time(timedelta(minutes=100)), as_time(timedelta(minutes=120)), RouteRun.Status.OPEN),
            ("GDY", "ROUTE-08", 1, 0, as_time(timedelta(minutes=150)), as_time(timedelta(minutes=160)), as_time(timedelta(minutes=180)), RouteRun.Status.OPEN),
            ("GDY", "ROUTE-09", 1, 0, as_time(timedelta(minutes=210)), as_time(timedelta(minutes=220)), as_time(timedelta(minutes=240)), RouteRun.Status.OPEN),
            ("GDY", "ROUTE-10", 1, 0, as_time(timedelta(minutes=270)), as_time(timedelta(minutes=280)), as_time(timedelta(minutes=300)), RouteRun.Status.OPEN),
        ]

        route_runs = {}
        for branch_code, route_code, run_number, day_offset, cutoff_time, sync_time, departure_time, status in run_data:
            route = delivery_routes[(branch_code, route_code)]
            service_date = today + timedelta(days=day_offset)
            BranchDispatchPolicy.objects.get_or_create(branch=route.branch)
            schedule, _ = RouteRoundSchedule.objects.update_or_create(
                route=route,
                weekday=service_date.weekday(),
                round_number=run_number,
                defaults={
                    "cutoff_time": cutoff_time,
                    "departure_time": departure_time,
                    "dispatch_wave": departure_time.strftime("%H:%M"),
                    "operational_label": "",
                    "is_active": True,
                },
            )
            cutoff_at = as_datetime(service_date, cutoff_time)
            departure_at = as_datetime(service_date, departure_time)
            scenario_window = {
                ("GDY", "ROUTE-01", 1): (-30, 30),
                ("GDY", "ROUTE-02", 1): (30, 60),
                ("GDY", "ROUTE-05", 1): (-60, -15),
                ("GDY", "ROUTE-06", 1): (-30, 30),
            }.get((branch_code, route_code, run_number))
            if scenario_window:
                cutoff_at = now + timedelta(minutes=scenario_window[0])
                departure_at = now + timedelta(minutes=scenario_window[1])
            ready_at = None
            documents_printed_at = None
            closed_at = None
            if status == RouteRun.Status.CLOSED:
                ready_at = departure_at - timedelta(minutes=25)
                documents_printed_at = departure_at - timedelta(minutes=15)
                closed_at = departure_at - timedelta(minutes=5)
            elif status == RouteRun.Status.READY_TO_CLOSE:
                ready_at = departure_at - timedelta(minutes=25)
                documents_printed_at = departure_at - timedelta(minutes=15)

            route_run, _ = RouteRun.objects.update_or_create(
                route=route,
                service_date=service_date,
                run_number=run_number,
                defaults={
                    "schedule": schedule,
                    "order_cutoff_time": cutoff_time,
                    "sync_time": sync_time,
                    "departure_time": departure_time,
                    "cutoff_at": cutoff_at,
                    "planned_departure_at": departure_at,
                    "dispatch_wave": schedule.dispatch_wave,
                    "operational_identifier": operational_identifier(route, service_date, run_number),
                    "status": status,
                    "ready_at": ready_at,
                    "documents_printed_at": documents_printed_at,
                    "closed_at": closed_at,
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
                "ABC-CAR",
                ("GDY", "ROUTE-01", 1),
                [("FILTR-001", 1, "2"), ("OLEJ-001", 2, "5")],
            ),
            ("AX-ORDER-0002", "GDY", "Demo Client Two", "BRAKE-PL", ("GDY", "ROUTE-01", 1), [("KLOCKI-001", 1, "1")]),
            ("AX-ORDER-0003", "GDA", "Demo Client Three", "GDA-AUTO", ("GDA", "ROUTE-01", 1), [("FILTR-001", 1, "3")]),
            ("AX-ORDER-0004", "GDY", "Demo Client Four", "OIL-SHOP", ("GDY", "ROUTE-02", 1), [("OLEJ-001", 1, "2")]),
            ("AX-ORDER-0005", "GDA", "Demo Client Five", "GDA-FLEET", ("GDA", "ROUTE-02", 1), [("OLEJ-001", 1, "2")]),
            (
                "AX-ORDER-LABEL-TEST",
                "GDY",
                "Demo Client Label Test",
                "LABEL-TEST",
                ("GDY", "ROUTE-04", 1),
                [("FILTR-001", 1, "3"), ("OLEJ-001", 2, "2")],
            ),
            ("AX-ORDER-0006", "GDY", "Demo Client Six", "TARGET-TODAY", ("GDY", "ROUTE-01", 2), [("FILTR-001", 1, "1")]),
            ("AX-ORDER-0007", "GDY", "Demo Client Seven", "TARGET-WEEK", ("GDY", "ROUTE-05", 1), [("OLEJ-001", 1, "1")]),
            ("AX-ORDER-READY-AFTER", "GDY", "Demo Ready After", "READY-AFTER", ("GDY", "ROUTE-06", 1), [("FILTR-001", 1, "1")]),
            ("AX-ORDER-REASSIGN", "GDY", "Demo Reassignment", "REASSIGN", ("GDY", "ROUTE-07", 1), [("OLEJ-001", 1, "1")]),
            (
                "AX-SALE-RET-001",
                "GDY",
                "Demo Return Customer One",
                "RET-CUST-1",
                ("GDY", "ROUTE-03", 1),
                [("FILTR-001", 1, "4"), ("OLEJ-001", 2, "2")],
            ),
            (
                "AX-SALE-RET-002",
                "GDY",
                "Demo Return Customer Two",
                "RET-CUST-2",
                ("GDY", "ROUTE-03", 1),
                [("FILTR-001", 1, "2")],
            ),
            (
                "AX-SALE-RET-003",
                "GDA",
                "Demo Gda Return Customer",
                "GDA-RET",
                ("GDA", "ROUTE-01", 1),
                [("FILTR-001", 1, "1")],
            ),
        ]

        orders = {}
        order_lines = {}
        for reference, branch_code, customer_name, customer_alias, route_run_key, lines in orders_data:
            order, _ = Order.objects.update_or_create(
                external_reference=reference,
                defaults={
                        "branch": branches[branch_code],
                        "route_run": route_runs[route_run_key],
                        "customer_name": customer_name,
                        "customer_alias": customer_alias,
                        "status": Order.Status.COMPLETED if reference.startswith("AX-SALE-RET") else Order.Status.IMPORTED,
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

    def create_external_return_documents(self, branches, products):
        document, _ = ExternalReturnDocument.objects.update_or_create(
            source_system="AX",
            external_reference="ZW1103872",
            defaults={
                "branch": branches["GDY"],
                "customer_name": "Demo Return Customer One",
                "customer_alias": "RET-CUST-1",
                "source_sales_document_reference": "AX-SALE-RET-001",
                "external_created_at": timezone.now(),
                "last_synced_at": timezone.now(),
                "status": ExternalReturnDocument.Status.OPEN,
                "completed_at": None,
            },
        )
        return_lines = {}
        line_data = [
            (1, "FILTR-001", "5"),
            (2, "OLEJ-001", "2"),
        ]
        for line_number, sku, quantity in line_data:
            line, _ = ExternalReturnDocumentLine.objects.update_or_create(
                document=document,
                line_number=line_number,
                defaults={
                    "product": products[sku],
                    "expected_quantity": Decimal(quantity),
                    "accepted_quantity": Decimal("0"),
                    "rejected_quantity": Decimal("0"),
                    "on_hold_quantity": Decimal("0"),
                },
            )
            return_lines[line_number] = line
        return document, return_lines

    def create_picking_tasks(self, branches, locations, order_lines):
        picking_data = [
            (("AX-ORDER-0001", 1), "GDY", "A-01-01", PickingTask.Status.OPEN),
            (("AX-ORDER-0001", 2), "GDY", "A-01-02", PickingTask.Status.ASSIGNED),
            (("AX-ORDER-0002", 1), "GDY", "A-02-01", PickingTask.Status.OPEN),
            (("AX-ORDER-0003", 1), "GDA", "B-01-01", PickingTask.Status.OPEN),
            (("AX-ORDER-0004", 1), "GDY", "A-01-02", PickingTask.Status.OPEN),
            (("AX-ORDER-0005", 1), "GDA", "B-01-02", PickingTask.Status.OPEN),
            (("AX-ORDER-LABEL-TEST", 1), "GDY", "A-02-01", PickingTask.Status.OPEN),
            (("AX-ORDER-LABEL-TEST", 2), "GDY", "A-01-02", PickingTask.Status.OPEN),
            (("AX-ORDER-0006", 1), "GDY", "A-01-01", PickingTask.Status.OPEN),
            (("AX-ORDER-0007", 1), "GDY", "A-01-02", PickingTask.Status.OPEN),
            (("AX-ORDER-READY-AFTER", 1), "GDY", "A-01-01", PickingTask.Status.OPEN),
            (("AX-ORDER-REASSIGN", 1), "GDY", "A-01-02", PickingTask.Status.OPEN),
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
                    "shortage_quantity": Decimal("0"),
                    "quantity_prepared": Decimal("0"),
                },
            )
            picking_tasks[order_line_key] = task
        return picking_tasks

    def create_scanner_carts(self):
        carts = {}
        for code in ["WOZEK-01", "WOZEK-02", "WOZEK-03"]:
            cart, _ = ScannerCart.objects.update_or_create(
                code=code,
                defaults={
                    "name": code,
                    "status": ScannerCart.Status.AVAILABLE,
                },
            )
            carts[code] = cart
        ScannerCart.objects.filter(code__in=carts.keys()).update(status=ScannerCart.Status.AVAILABLE)
        return carts

    def create_transfer_pallets(self, branches, products):
        pallet_specs = [
            (
                "IBT-GDA-GDY-001",
                "PAL-GDA-GDY-001",
                InterBranchTransfer.Status.IN_TRANSIT,
                TransferPallet.Status.IN_TRANSIT,
                timezone.now(),
                [("FILTR-001", "3"), ("OLEJ-001", "2")],
            ),
            (
                "IBT-GDA-GDY-DISC-001",
                "PAL-GDA-GDY-DISC-001",
                InterBranchTransfer.Status.IN_TRANSIT,
                TransferPallet.Status.IN_TRANSIT,
                timezone.now(),
                [("FILTR-001", "5"), ("OLEJ-001", "2")],
            ),
            (
                "IBT-SHP-GDA-GDY-001",
                "PAL-SHP-GDA-GDY-001",
                InterBranchTransfer.Status.DRAFT,
                TransferPallet.Status.IN_TRANSIT,
                None,
                [("FILTR-001", "5"), ("OLEJ-001", "2")],
            ),
        ]
        pallets = {}
        for transfer_reference, pallet_code, transfer_status, pallet_status, released_at, manifest_data in pallet_specs:
            transfer, _ = InterBranchTransfer.objects.update_or_create(
                reference=transfer_reference,
                defaults={
                    "source_branch": branches["GDA"],
                    "destination_branch": branches["GDY"],
                    "status": transfer_status,
                    "released_at": released_at,
                    "completed_at": None,
                },
            )
            pallet, _ = TransferPallet.objects.update_or_create(
                scan_code=pallet_code,
                defaults={
                    "transfer": transfer,
                    "status": pallet_status,
                    "released_at": released_at,
                    "receiving_started_at": None,
                    "received_at": None,
                },
            )
            for sku, expected_quantity in manifest_data:
                TransferPalletItem.objects.update_or_create(
                    pallet=pallet,
                    product=products[sku],
                    defaults={
                        "expected_quantity": Decimal(expected_quantity),
                        "received_quantity": Decimal("0"),
                    },
                )
            pallets[pallet_code] = pallet

        TransferPalletArrival.objects.update_or_create(
            pallet=pallets["PAL-GDA-GDY-DISC-001"],
            defaults={"scanned_by_worker_code": "DEMO", "client_operation_id": "seed-demo-discrepancy-arrival"},
        )

        return pallets

    def create_shipments(self, branches, orders, order_lines, route_runs, transfer_pallets):
        now = timezone.now()
        shipment_specs = [
            {
                "reference": "SHP-GDY-0001",
                "order": "AX-ORDER-0001",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-01", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.ACTIVE,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Client One",
                "external_notes": "Active customer shipment on an incomplete shared route.",
            },
            {
                "reference": "SHP-GDY-0002",
                "order": "AX-ORDER-0002",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-01", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.PICKING,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Client Two",
                "external_notes": "Picking-in-progress shipment.",
                "partial_picked": True,
            },
            {
                "reference": "SHP-GDY-0003",
                "order": "AX-ORDER-0004",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-02", 1),
                "shipment_type": Shipment.ShipmentType.COURIER_DISPATCH,
                "status": Shipment.Status.PREPARED,
                "document_status": Shipment.DocumentStatus.PRINTED,
                "payment_method": "Card",
                "delivery_name": "Demo Client Four",
                "external_notes": "Prepared shipment on a route ready to close.",
                "prepared": True,
            },
            {
                "reference": "SHP-GDY-0004",
                "order": "AX-SALE-RET-001",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-03", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.COMPLETED,
                "document_status": Shipment.DocumentStatus.POSTED,
                "payment_method": "Account",
                "delivery_name": "Demo Return Customer One",
                "external_notes": "Completed/dispatched historical shipment.",
            },
            {
                "reference": "SHP-GDY-0005",
                "order": "AX-SALE-RET-002",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-03", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.CANCELLED,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Return Customer Two",
                "external_notes": "Cancelled demo shipment.",
                "cancelled": True,
            },
            {
                "reference": "SHP-GDA-GDY-0001",
                "order": "AX-ORDER-0005",
                "branch": "GDA",
                "route": ("GDA", "ROUTE-02", 1),
                "shipment_type": Shipment.ShipmentType.INTER_BRANCH,
                "transfer_pallet": "PAL-SHP-GDA-GDY-001",
                "status": Shipment.Status.PREPARED,
                "document_status": Shipment.DocumentStatus.AVAILABLE,
                "payment_method": "Inter-branch",
                "delivery_name": "Magazyn Gdynia",
                "external_notes": "Inter-branch shipment awaiting document posting.",
                "prepared": True,
            },
            {
                "reference": "SHP-GDA-GDY-0002",
                "order": "AX-ORDER-0003",
                "branch": "GDA",
                "route": ("GDA", "ROUTE-01", 1),
                "shipment_type": Shipment.ShipmentType.INTER_BRANCH,
                "transfer_pallet": "PAL-GDA-GDY-001",
                "status": Shipment.Status.DOCUMENTS_POSTED,
                "document_status": Shipment.DocumentStatus.POSTED,
                "payment_method": "Inter-branch",
                "delivery_name": "Magazyn Gdynia",
                "external_notes": "Inter-branch shipment with documents posted. Freight release is not implemented.",
                "prepared": True,
                "posted": True,
            },
            {
                "reference": "SHP-GDY-0006",
                "order": "AX-ORDER-LABEL-TEST",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-04", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.ACTIVE,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Client Label Test",
                "external_notes": "Shipment for quantity removal with one zero-effective line.",
                "zero_effective_lines": [2],
            },
            {
                "reference": "SHP-GDY-0007",
                "order": "AX-ORDER-0006",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-01", 2),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.ACTIVE,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Client Six",
                "external_notes": "Today's eligible route target shipment.",
            },
            {
                "reference": "SHP-GDY-0008",
                "order": "AX-ORDER-0007",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-05", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.ACTIVE,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Client Seven",
                "external_notes": "Weekly eligible route target shipment.",
            },
            {
                "reference": "SHP-GDY-READY-AFTER",
                "order": "AX-ORDER-READY-AFTER",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-06", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.PREPARED,
                "document_status": Shipment.DocumentStatus.PRINTED,
                "payment_method": "Account",
                "delivery_name": "Demo Ready After",
                "external_notes": "Prepared shipment on its dedicated post-cutoff RouteRun.",
                "prepared": True,
            },
            {
                "reference": "SHP-GDY-REASSIGN",
                "order": "AX-ORDER-REASSIGN",
                "branch": "GDY",
                "route": ("GDY", "ROUTE-07", 1),
                "shipment_type": Shipment.ShipmentType.CUSTOMER_DELIVERY,
                "status": Shipment.Status.ACTIVE,
                "document_status": Shipment.DocumentStatus.NOT_AVAILABLE,
                "payment_method": "Account",
                "delivery_name": "Demo Reassignment",
                "external_notes": "Dedicated shipment for RouteRun reassignment testing.",
            },
        ]

        shipments = {}
        for spec in shipment_specs:
            order = orders[spec["order"]]
            transfer = None
            if spec.get("transfer_pallet"):
                transfer = transfer_pallets[spec["transfer_pallet"]].transfer
            shipment, _ = Shipment.objects.update_or_create(
                reference=spec["reference"],
                defaults={
                    "branch": branches[spec["branch"]],
                    "order": order,
                    "route_run": route_runs[spec["route"]],
                    "inter_branch_transfer": transfer,
                    "shipment_type": spec["shipment_type"],
                    "status": spec["status"],
                    "document_status": spec["document_status"],
                    "source_system": "AX",
                    "external_reference": f"AX-{spec['reference']}",
                    "external_order_reference": order.external_reference,
                    "external_status": "imported",
                    "external_customer_account": order.customer_alias,
                    "external_delivery_reference": f"DLV-{spec['reference']}",
                    "external_notes": spec["external_notes"],
                    "external_created_at": now - timedelta(hours=4),
                    "external_updated_at": now - timedelta(hours=2),
                    "customer_name": order.customer_name,
                    "customer_alias": order.customer_alias,
                    "recipient_account": order.customer_alias,
                    "delivery_name": spec["delivery_name"],
                    "delivery_address": "Demo delivery address",
                    "delivery_date": order.requested_ship_date or timezone.localdate(),
                    "payment_method": spec["payment_method"],
                    "activated_at": now - timedelta(hours=3) if spec["status"] != Shipment.Status.PENDING_ACTIVATION else None,
                    "picking_lists_posted_at": now - timedelta(hours=2) if spec["status"] not in [Shipment.Status.PENDING_ACTIVATION, Shipment.Status.CANCELLED] else None,
                    "prepared_at": now - timedelta(minutes=30) if spec.get("prepared") else None,
                    "cancelled_at": now - timedelta(minutes=45) if spec.get("cancelled") else None,
                    "cancellation_reason": "Demo cancelled before dispatch." if spec.get("cancelled") else "",
                    "documents_printed_at": now - timedelta(minutes=20) if spec["document_status"] in [Shipment.DocumentStatus.POSTED, Shipment.DocumentStatus.PRINTED] else None,
                    "document_print_count": 1 if spec["document_status"] in [Shipment.DocumentStatus.POSTED, Shipment.DocumentStatus.PRINTED] else 0,
                    "documents_posted_at": now - timedelta(minutes=10) if spec.get("posted") else None,
                },
            )
            shipments[spec["reference"]] = shipment

            order.route_run = route_runs[spec["route"]]
            order.save(update_fields=["route_run", "updated_at"])

            for line in order.lines.select_related("product"):
                ShipmentLine.objects.update_or_create(
                    shipment=shipment,
                    line_number=line.line_number,
                    defaults={
                        "order_line": line,
                        "product": line.product,
                        "external_line_reference": f"{shipment.reference}-L{line.line_number:03d}",
                        "ordered_quantity": line.quantity_ordered,
                        "cancelled_quantity": line.quantity_ordered if spec.get("cancelled") else Decimal("0"),
                        "delivery_date": shipment.delivery_date,
                    },
                )
                tasks = PickingTask.objects.filter(order_line=line)
                if line.line_number in spec.get("zero_effective_lines", []):
                    ShipmentLine.objects.filter(shipment=shipment, line_number=line.line_number).update(
                        cancelled_quantity=line.quantity_ordered
                    )
                    tasks.update(status=PickingTask.Status.CANCELLED)
                    continue
                if spec.get("prepared"):
                    tasks.update(
                        status=PickingTask.Status.COMPLETED,
                        quantity_to_pick=line.quantity_ordered,
                        quantity_picked=line.quantity_ordered,
                        quantity_prepared=line.quantity_ordered,
                        shortage_quantity=Decimal("0"),
                    )
                elif spec.get("partial_picked"):
                    tasks.update(
                        status=PickingTask.Status.IN_PROGRESS,
                        quantity_to_pick=line.quantity_ordered,
                        quantity_picked=line.quantity_ordered / Decimal("2"),
                        quantity_prepared=Decimal("0"),
                        shortage_quantity=Decimal("0"),
                    )
                elif spec.get("cancelled"):
                    tasks.update(status=PickingTask.Status.CANCELLED)

        return shipments

    def create_scanner_demo_work(self, branches, users, carts, shipments):
        """Attach scanner activity to the same canonical seeded picking graph."""
        shipment = shipments["SHP-GDY-0002"]
        task = PickingTask.objects.get(order_line__shipment_line__shipment=shipment)
        job = PickingJob.objects.create(status=PickingJob.Status.IN_PROGRESS, mode=PickingJob.Mode.MERGED, started_at=self.seed_now)
        job.route_runs.add(shipment.route_run)
        PickingJobTask.objects.create(picking_job=job, picking_task=task)
        cart = carts["WOZEK-03"]
        cart.status = ScannerCart.Status.IN_USE
        cart.save(update_fields=["status", "updated_at"])
        scanner_session = ScannerSession.objects.create(
            cart=cart,
            worker_code=users["GDY_WORKER"].username,
            status=ScannerSession.Status.ACTIVE,
            started_at=self.seed_now,
        )
        work_session = CartWorkSession.objects.create(
            cart=cart,
            picking_job=job,
            scanner_session=scanner_session,
            status=CartWorkSession.Status.ACTIVE,
            started_at=self.seed_now,
        )
        participant = CartWorkParticipant.objects.create(
            cart_work_session=work_session,
            user=users["GDY_WORKER"],
            branch=branches["GDY"],
            status=CartWorkParticipant.Status.ACTIVE,
            current_picking_task=task,
        )
        PickingTaskClaim.objects.create(
            picking_task=task,
            cart_work_participant=participant,
            status=PickingTaskClaim.Status.CLAIMED,
        )

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
