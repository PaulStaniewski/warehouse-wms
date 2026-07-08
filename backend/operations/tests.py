from datetime import datetime, time
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkSession,
    DeliveryRoute,
    InterBranchTransfer,
    Order,
    OrderLine,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingTask,
    RouteRun,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
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
    TransferPalletItem,
)
from operations.services import recalculate_route_readiness, reconciliation_route_for_finding, route_close_result
from warehouse.models import Branch, InventoryItem, Location, Product


class PickingTaskCompleteActionTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="TST", name="Test Branch", city="Gdynia", country="Poland")
        self.location = Location.objects.create(
            branch=self.branch,
            code="A-01-01",
            name="A-01-01",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(
            sku="TEST-001",
            name="Test Product",
            barcode="990000000001",
            unit_of_measure="pcs",
        )
        self.inventory_item = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("10"),
            quantity_reserved=Decimal("0"),
        )
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="ROUTE-T", name="Test Route")
        self.route_run = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 50),
            sync_time=time(8, 51),
            departure_time=time(9, 0),
            status=RouteRun.Status.OPEN,
        )
        self.order = Order.objects.create(
            branch=self.branch,
            route_run=self.route_run,
            external_reference="TEST-ORDER-001",
            customer_name="Test Customer",
            status=Order.Status.IMPORTED,
        )
        self.order_line = OrderLine.objects.create(
            order=self.order,
            product=self.product,
            line_number=1,
            quantity_ordered=Decimal("2"),
            quantity_picked=Decimal("0"),
        )
        self.task = PickingTask.objects.create(
            branch=self.branch,
            order_line=self.order_line,
            source_location=self.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("2"),
            quantity_picked=Decimal("0"),
        )
    def complete_task(self, task=None, location_code="A-01-01", product_code="990000000001"):
        task = task or self.task
        return self.client.post(
            f"/api/picking-tasks/{task.id}/complete/",
            {
                "location_code": location_code,
                "product_code": product_code,
            },
            format="json",
        )

    def test_completing_with_correct_location_and_product_succeeds(self):
        response = self.complete_task()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, PickingTask.Status.COMPLETED)
        self.assertEqual(self.task.quantity_picked, Decimal("2.000"))
        self.assertEqual(self.task.quantity_prepared, Decimal("2.000"))

    def test_wrong_location_code_fails(self):
        response = self.complete_task(location_code="WRONG-01")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("location", response.data["detail"])

    def test_wrong_product_code_fails(self):
        response = self.complete_task(product_code="WRONG-SKU")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", response.data["detail"])

    def test_product_sku_can_be_used_instead_of_barcode(self):
        response = self.complete_task(product_code="TEST-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, PickingTask.Status.COMPLETED)

    def test_completed_task_cannot_be_completed_again(self):
        first_response = self.complete_task()
        second_response = self.complete_task()

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already completed", second_response.data["detail"])

    def test_task_cannot_pick_more_than_available_stock(self):
        self.inventory_item.quantity_on_hand = Decimal("1")
        self.inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

        response = self.complete_task()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Not enough stock", response.data["detail"])

    def test_order_line_quantity_picked_is_updated(self):
        self.complete_task()

        self.order_line.refresh_from_db()
        self.assertEqual(self.order_line.quantity_picked, Decimal("2.000"))

    def test_inventory_quantity_on_hand_is_decreased(self):
        self.complete_task()

        self.inventory_item.refresh_from_db()
        self.assertEqual(self.inventory_item.quantity_on_hand, Decimal("8.000"))

    def test_stock_movement_is_created(self):
        self.complete_task()

        movement = StockMovement.objects.get(reference=f"PICK-TASK-{self.task.id}")
        self.assertEqual(movement.movement_type, StockMovement.MovementType.PICK)
        self.assertEqual(movement.quantity, Decimal("2.000"))
        self.assertEqual(movement.source_location, self.location)


class ScannerPickingScanActionTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="SCN", name="Scanner Branch", city="Gdynia", country="Poland")
        self.location = Location.objects.create(
            branch=self.branch,
            code="S-01-01",
            name="S-01-01",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(
            sku="SCAN-001",
            name="Scanner Product",
            barcode="880000000001",
            unit_of_measure="pcs",
        )
        self.inventory_item = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="ROUTE-S", name="Scanner Route")
        self.route_run = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 50),
            sync_time=time(8, 51),
            departure_time=time(9, 0),
            status=RouteRun.Status.OPEN,
        )
        self.order = Order.objects.create(
            branch=self.branch,
            route_run=self.route_run,
            external_reference="SCAN-ORDER-001",
            customer_name="Scanner Customer",
            status=Order.Status.IMPORTED,
        )
        self.order_line = OrderLine.objects.create(
            order=self.order,
            product=self.product,
            line_number=1,
            quantity_ordered=Decimal("2"),
            quantity_picked=Decimal("0"),
        )
        self.task = PickingTask.objects.create(
            branch=self.branch,
            order_line=self.order_line,
            source_location=self.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("2"),
            quantity_picked=Decimal("0"),
        )
        self.cart = ScannerCart.objects.create(code="CART-01", name="Cart 01", status=ScannerCart.Status.IN_USE)
        self.session = ScannerSession.objects.create(cart=self.cart, worker_code="DEMO")

    def scan(self, route_run_id=None, code="SCAN-001"):
        return self.client.post(
            "/api/scanner/picking/scan/",
            {
                "route_run_id": route_run_id if route_run_id is not None else self.route_run.id,
                "code": code,
            },
            format="json",
        )

    def pick_to_cart(self, code="SCAN-001", quantity="1"):
        return self.client.post(
            "/api/scanner/picking/pick/",
            {
                "route_run_id": self.route_run.id,
                "code": code,
                "quantity": quantity,
                "session_id": self.session.id,
            },
            format="json",
        )

    def test_missing_route_run_id_returns_400(self):
        response = self.client.post("/api/scanner/picking/scan/", {"code": "SCAN-001"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("route_run_id", response.data["detail"])

    def test_missing_code_returns_400(self):
        response = self.client.post(
            "/api/scanner/picking/scan/",
            {"route_run_id": self.route_run.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", response.data["detail"])

    def test_route_run_not_found_returns_404(self):
        response = self.scan(route_run_id=999999)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_matching_open_task_returns_400(self):
        response = self.scan(code="UNKNOWN")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("No matching open picking task", response.data["detail"])

    def test_scan_by_sku_marks_progress(self):
        response = self.scan(code="SCAN-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.order_line.refresh_from_db()
        self.inventory_item.refresh_from_db()
        self.assertEqual(self.task.status, PickingTask.Status.IN_PROGRESS)
        self.assertEqual(self.task.quantity_picked, Decimal("1.000"))
        self.assertEqual(self.order_line.quantity_picked, Decimal("1.000"))
        self.assertEqual(self.inventory_item.quantity_on_hand, Decimal("4.000"))

    def test_scan_by_barcode_marks_picked_after_required_quantity(self):
        first_response = self.scan(code="880000000001")
        second_response = self.scan(code="880000000001")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, PickingTask.Status.PICKED)
        self.assertEqual(self.task.quantity_picked, Decimal("2.000"))
        self.assertEqual(self.task.quantity_prepared, Decimal("0.000"))

    def test_scan_by_order_reference_matches_task(self):
        response = self.scan(code="SCAN-ORDER-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.quantity_picked, Decimal("1.000"))

    def test_completed_matching_task_returns_400(self):
        self.task.status = PickingTask.Status.COMPLETED
        self.task.quantity_picked = Decimal("2")
        self.task.quantity_prepared = Decimal("2")
        self.task.save(update_fields=["status", "quantity_picked", "quantity_prepared", "updated_at"])

        response = self.scan()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already prepared", response.data["detail"])

    def test_scan_creates_audit_log(self):
        self.scan()

        self.assertTrue(
            AuditLog.objects.filter(
                entity_name="PickingTask",
                entity_id=str(self.task.id),
                message__icontains="Scanner picking picked",
            ).exists()
        )

    def test_pick_rejects_closed_route(self):
        self.route_run.status = RouteRun.Status.CLOSED
        self.route_run.save(update_fields=["status", "updated_at"])

        response = self.pick_to_cart()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not open", response.data["detail"])

    def prepare(self, route_run_id=None, code="SCAN-ORDER-001", product_code="SCAN-001", quantity="1"):
        return self.client.post(
            "/api/scanner/picking/prepare/",
            {
                "session_id": self.session.id,
                "route_run_id": route_run_id if route_run_id is not None else self.route_run.id,
                "order_reference": code,
                "product_code": product_code,
                "quantity": quantity,
            },
            format="json",
        )

    def test_pick_endpoint_accepts_valid_product_scan(self):
        response = self.client.post(
            "/api/scanner/picking/pick/",
            {"route_run_id": self.route_run.id, "code": "SCAN-001", "quantity": "1", "session_id": self.session.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.quantity_picked, Decimal("1.000"))
        self.assertTrue(CartPickedItem.objects.filter(session=self.session, picking_task=self.task).exists())

    def test_prepare_requires_picked_quantity_first(self):
        self.print_label()
        response = self.prepare()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("active cart", response.data["detail"])

    def test_prepare_endpoint_increments_prepared_quantity(self):
        self.pick_to_cart()
        self.print_label()

        response = self.prepare()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.quantity_prepared, Decimal("1.000"))
        self.assertEqual(self.task.status, PickingTask.Status.IN_PROGRESS)

    def test_task_becomes_completed_when_prepared_quantity_reaches_required_quantity(self):
        self.pick_to_cart()
        self.pick_to_cart()
        self.print_label()
        first_response = self.prepare(quantity="1")
        second_response = self.prepare(quantity="1")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, PickingTask.Status.COMPLETED)
        self.assertEqual(self.task.quantity_prepared, Decimal("2.000"))

    def test_prepare_creates_audit_log(self):
        self.pick_to_cart()
        self.print_label()
        self.prepare()

        self.assertTrue(
            AuditLog.objects.filter(
                entity_name="PickingTask",
                entity_id=str(self.task.id),
                message__icontains="Scanner picking prepared",
            ).exists()
        )

    def test_prepare_rejects_closed_route(self):
        self.pick_to_cart()
        self.print_label()
        self.route_run.status = RouteRun.Status.CLOSED
        self.route_run.save(update_fields=["status", "updated_at"])

        response = self.prepare()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("closed", response.data["detail"])

    def test_prepare_invalid_session_returns_clear_error(self):
        response = self.client.post(
            "/api/scanner/picking/prepare/",
            {
                "session_id": 999999,
                "order_reference": "SCAN-ORDER-001",
                "product_code": "SCAN-001",
                "quantity": "1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("session", response.data["detail"])

    def test_pick_invalid_product_code_returns_clear_error(self):
        response = self.scan(code="WRONG")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("No matching open", response.data["detail"])

    def test_prepare_invalid_order_code_returns_clear_error(self):
        self.pick_to_cart()
        self.print_label()

        response = self.prepare(code="WRONG-ORDER")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("not found", response.data["detail"])

    def test_preparing_more_than_picked_quantity_returns_clear_error(self):
        self.pick_to_cart()
        self.print_label()

        response = self.prepare(quantity="2")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("picked quantity", response.data["detail"])

    def test_prepare_rejects_decimal_quantity(self):
        self.pick_to_cart()
        self.print_label()

        response = self.prepare(quantity="1.000")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("whole number", response.data["detail"])

    def print_label(self, order_reference="SCAN-ORDER-001", printer_code="ZEBRA-01"):
        return self.client.post(
            "/api/scanner/control/print-label/",
            {
                "session_id": self.session.id,
                "order_reference": order_reference,
                "printer_code": printer_code,
            },
            format="json",
        )

    def test_start_session_creates_or_reuses_cart(self):
        response = self.client.post(
            "/api/scanner/session/start/",
            {"cart_code": "WOZEK-99", "worker_code": "DEMO"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["session"]["cart_code"], "WOZEK-99")
        self.assertTrue(ScannerCart.objects.filter(code="WOZEK-99", status=ScannerCart.Status.IN_USE).exists())

    def test_control_cart_items_returns_active_cart_contents(self):
        self.client.post(
            "/api/scanner/picking/pick/",
            {"route_run_id": self.route_run.id, "code": "SCAN-001", "quantity": "1", "session_id": self.session.id},
            format="json",
        )

        response = self.client.get("/api/scanner/control/cart-items/", {"session_id": self.session.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"][0]["product_sku"], "SCAN-001")
        self.assertEqual(response.data["items"][0]["customer_label_ready"], False)

    def test_control_cart_items_keeps_completed_lines_visible(self):
        self.pick_to_cart()
        self.print_label()
        self.prepare()

        response = self.client.get("/api/scanner/control/cart-items/", {"session_id": self.session.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["remaining_quantity"], "0.000")
        self.assertEqual(response.data["items"][0]["customer_label_ready"], True)

    def test_control_target_returns_candidates_for_scanned_product(self):
        self.client.post(
            "/api/scanner/picking/pick/",
            {"route_run_id": self.route_run.id, "code": "SCAN-001", "quantity": "1", "session_id": self.session.id},
            format="json",
        )

        response = self.client.get(
            "/api/scanner/control/target/",
            {"session_id": self.session.id, "product_code": "SCAN-001"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["candidates"][0]["order_reference"], "SCAN-ORDER-001")

    def test_print_label_creates_audit_log_and_label(self):
        response = self.print_label()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        label = ScannerCustomerLabel.objects.get(session=self.session, order=self.order)
        self.assertEqual(response.data["label"]["scan_code"], label.scan_code)
        self.assertTrue(label.scan_code.startswith("CL-"))
        self.assertTrue(AuditLog.objects.filter(message__icontains="Customer label printed").exists())

    def test_print_label_reuses_existing_customer_label(self):
        first = self.print_label()
        second = self.print_label(printer_code="ZEBRA-02")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["label"]["id"], second.data["label"]["id"])
        self.assertEqual(first.data["label"]["scan_code"], second.data["label"]["scan_code"])
        self.assertEqual(ScannerCustomerLabel.objects.filter(session=self.session, order=self.order).count(), 1)

    def test_customer_label_scan_code_is_immutable(self):
        self.print_label()
        label = ScannerCustomerLabel.objects.get(session=self.session, order=self.order)
        original_scan_code = label.scan_code

        label.scan_code = "CL-CHANGED"
        label.save()

        label.refresh_from_db()
        self.assertEqual(label.scan_code, original_scan_code)

    def test_finish_control_rejects_unprepared_items(self):
        self.client.post(
            "/api/scanner/picking/pick/",
            {"route_run_id": self.route_run.id, "code": "SCAN-001", "quantity": "1", "session_id": self.session.id},
            format="json",
        )

        response = self.client.post("/api/scanner/control/finish/", {"session_id": self.session.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unprepared", response.data["detail"])

    def test_finish_control_releases_cart_when_all_items_prepared(self):
        self.client.post(
            "/api/scanner/picking/pick/",
            {"route_run_id": self.route_run.id, "code": "SCAN-001", "quantity": "1", "session_id": self.session.id},
            format="json",
        )
        self.print_label()
        self.prepare()

        response = self.client.post("/api/scanner/control/finish/", {"session_id": self.session.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.cart.refresh_from_db()
        self.assertEqual(self.session.status, ScannerSession.Status.CLOSED)
        self.assertEqual(self.cart.status, ScannerCart.Status.AVAILABLE)


class ScannerContentsLookupTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="CNT", name="Contents Branch", city="Gdynia", country="Poland")
        self.location = Location.objects.create(
            branch=self.branch,
            code="C-01-01",
            name="Contents Location",
            location_type=Location.LocationType.PICKING,
        )
        self.empty_location = Location.objects.create(
            branch=self.branch,
            code="C-EMPTY",
            name="Empty Location",
            location_type=Location.LocationType.STORAGE,
        )
        self.product = Product.objects.create(
            sku="CNT-001",
            name="Contents Product",
            barcode="881000000001",
            unit_of_measure="pcs",
        )
        self.other_product = Product.objects.create(
            sku="CNT-002",
            name="Zero Product",
            barcode="881000000002",
            unit_of_measure="pcs",
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("8"),
            quantity_reserved=Decimal("1"),
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.other_product,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
        )
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="CNT-R", name="Contents Route")
        self.route_run = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 50),
            sync_time=time(8, 51),
            departure_time=time(9, 0),
            status=RouteRun.Status.OPEN,
        )
        self.order_1 = self.create_order("CNT-ORDER-1", "Customer One")
        self.order_2 = self.create_order("CNT-ORDER-2", "Customer Two")
        self.empty_order = self.create_order("CNT-ORDER-EMPTY", "Customer Empty")
        self.task_1 = self.create_task(self.order_1.lines.first())
        self.task_2 = self.create_task(self.order_2.lines.first())
        self.empty_task = self.create_task(self.empty_order.lines.first())
        self.cart = ScannerCart.objects.create(code="CNT-CART", name="Contents Cart", status=ScannerCart.Status.IN_USE)
        self.empty_cart = ScannerCart.objects.create(code="CNT-EMPTY-CART", name="Empty Cart", status=ScannerCart.Status.AVAILABLE)
        self.session = ScannerSession.objects.create(cart=self.cart, worker_code="CONTENTS")
        self.picking_job = PickingJob.objects.create(status=PickingJob.Status.IN_PROGRESS, mode=PickingJob.Mode.MERGED)
        self.picking_job.route_runs.add(self.route_run)
        self.cart_work_session = CartWorkSession.objects.create(
            cart=self.cart,
            picking_job=self.picking_job,
            scanner_session=self.session,
        )
        self.cart_item_1 = CartPickedItem.objects.create(
            session=self.session,
            cart_work_session=self.cart_work_session,
            cart=self.cart,
            route_run=self.route_run,
            picking_task=self.task_1,
            product=self.product,
            quantity_picked=Decimal("3"),
            quantity_prepared=Decimal("1"),
        )
        self.cart_item_2 = CartPickedItem.objects.create(
            session=self.session,
            cart_work_session=self.cart_work_session,
            cart=self.cart,
            route_run=self.route_run,
            picking_task=self.task_2,
            product=self.product,
            quantity_picked=Decimal("2"),
            quantity_prepared=Decimal("0"),
        )
        self.label = ScannerCustomerLabel.objects.create(
            session=self.session,
            order=self.order_1,
            printer_code="ZEBRA-01",
        )
        self.empty_label = ScannerCustomerLabel.objects.create(
            session=self.session,
            order=self.empty_order,
            printer_code="ZEBRA-01",
        )

    def create_order(self, reference, customer_name):
        order = Order.objects.create(
            branch=self.branch,
            route_run=self.route_run,
            external_reference=reference,
            customer_name=customer_name,
            status=Order.Status.IMPORTED,
        )
        OrderLine.objects.create(
            order=order,
            product=self.product,
            line_number=1,
            quantity_ordered=Decimal("3"),
            quantity_picked=Decimal("0"),
        )
        return order

    def create_task(self, order_line):
        return PickingTask.objects.create(
            branch=self.branch,
            order_line=order_line,
            source_location=self.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("3"),
            quantity_picked=Decimal("0"),
            quantity_prepared=Decimal("0"),
        )

    def get_contents(self, code):
        return self.client.get("/api/scanner/contents/", {"code": code})

    def test_known_location_resolves_with_positive_inventory_only(self):
        response = self.get_contents("C-01-01")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "location")
        self.assertEqual(response.data["code"], "C-01-01")
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["sku"], "CNT-001")
        self.assertEqual(response.data["items"][0]["quantity"], 8)

    def test_known_empty_location_returns_empty_items(self):
        response = self.get_contents("C-EMPTY")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "location")
        self.assertEqual(response.data["items"], [])

    def test_unknown_code_returns_clear_not_found(self):
        response = self.get_contents("UNKNOWN-CODE")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["detail"], "Code not found.")

    def test_active_cart_resolves_actual_picked_contents(self):
        response = self.get_contents("CNT-CART")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "cart")
        self.assertEqual(response.data["description"], f"Picking Job {self.picking_job.id}")
        self.assertEqual(len(response.data["items"]), 2)
        first_item = response.data["items"][0]
        self.assertEqual(first_item["picked_quantity"], 3)
        self.assertEqual(first_item["prepared_quantity"], 1)
        self.assertEqual(first_item["remaining_quantity"], 2)

    def test_cart_preserves_same_product_for_different_customers(self):
        response = self.get_contents("CNT-CART")

        order_references = [item["order_reference"] for item in response.data["items"]]
        self.assertEqual(order_references, ["CNT-ORDER-1", "CNT-ORDER-2"])

    def test_known_empty_cart_returns_empty_items(self):
        response = self.get_contents("CNT-EMPTY-CART")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "cart")
        self.assertEqual(response.data["items"], [])

    def test_known_customer_label_resolves_prepared_contents(self):
        response = self.get_contents(self.label.scan_code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "customer_label")
        self.assertEqual(response.data["code"], self.label.scan_code)
        self.assertEqual(response.data["description"], "Customer One / CNT-ORDER-1")
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["quantity"], 1)
        self.assertEqual(response.data["items"][0]["order_reference"], "CNT-ORDER-1")

    def test_known_empty_customer_label_returns_empty_items(self):
        response = self.get_contents(self.empty_label.scan_code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "customer_label")
        self.assertEqual(response.data["items"], [])

    def test_customer_label_scan_codes_are_unique(self):
        self.assertNotEqual(self.label.scan_code, self.empty_label.scan_code)
        self.assertEqual(ScannerCustomerLabel.objects.values("scan_code").distinct().count(), 2)

    def test_unknown_customer_label_scan_code_returns_not_found(self):
        response = self.get_contents("CL-NOTREAL")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["detail"], "Code not found.")

    def test_ambiguous_code_returns_conflict(self):
        ScannerCart.objects.create(code="C-01-01", name="Ambiguous Cart", status=ScannerCart.Status.AVAILABLE)

        response = self.get_contents("C-01-01")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("matched_object_types", response.data)
        self.assertEqual(set(response.data["matched_object_types"]), {"location", "cart"})


class ScannerReceivingWorkflowTests(APITestCase):
    def setUp(self):
        self.source_branch = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.destination_branch = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.destination_location = Location.objects.create(
            branch=self.destination_branch,
            code="A-01-01",
            name="A-01-01",
            location_type=Location.LocationType.STORAGE,
        )
        self.unconfirmed_location = Location.objects.create(
            branch=self.destination_branch,
            code="UNCONFIRMED",
            name="UNCONFIRMED",
            location_type=Location.LocationType.RECEIVING,
        )
        self.wrong_branch_location = Location.objects.create(
            branch=self.source_branch,
            code="B-01-01",
            name="B-01-01",
            location_type=Location.LocationType.STORAGE,
        )
        self.product = Product.objects.create(
            sku="FILTR-001",
            name="Filter",
            barcode="590000000001",
            unit_of_measure="pcs",
        )
        self.second_product = Product.objects.create(
            sku="OLEJ-001",
            name="Oil",
            barcode="590000000002",
            unit_of_measure="pcs",
        )
        self.unexpected_product = Product.objects.create(
            sku="OTHER-001",
            name="Other",
            barcode="590000000099",
            unit_of_measure="pcs",
        )
        self.transfer = InterBranchTransfer.objects.create(
            reference="IBT-TEST-001",
            source_branch=self.source_branch,
            destination_branch=self.destination_branch,
            status=InterBranchTransfer.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        self.pallet = TransferPallet.objects.create(
            transfer=self.transfer,
            scan_code="PAL-TEST-001",
            status=TransferPallet.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        self.item = TransferPalletItem.objects.create(
            pallet=self.pallet,
            product=self.product,
            expected_quantity=Decimal("3"),
            received_quantity=Decimal("0"),
        )
        self.second_item = TransferPalletItem.objects.create(
            pallet=self.pallet,
            product=self.second_product,
            expected_quantity=Decimal("2"),
            received_quantity=Decimal("0"),
        )

    def create_transfer_pallet_fixture(self, suffix=None):
        suffix = suffix or f"{TransferPallet.objects.count() + 1:03d}"
        transfer = InterBranchTransfer.objects.create(
            reference=f"IBT-TEST-{suffix}",
            source_branch=self.source_branch,
            destination_branch=self.destination_branch,
            status=InterBranchTransfer.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        pallet = TransferPallet.objects.create(
            transfer=transfer,
            scan_code=f"PAL-TEST-{suffix}",
            status=TransferPallet.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        TransferPalletItem.objects.create(
            pallet=pallet,
            product=self.product,
            expected_quantity=Decimal("3"),
            received_quantity=Decimal("0"),
        )
        TransferPalletItem.objects.create(
            pallet=pallet,
            product=self.second_product,
            expected_quantity=Decimal("2"),
            received_quantity=Decimal("0"),
        )
        return pallet

    def start_receiving(self, pallet_code="PAL-TEST-001"):
        return self.client.post(
            "/api/scanner/receiving/start/",
            {"pallet_code": pallet_code, "worker_code": "WORKER-1"},
            format="json",
        )

    def scan_product(self, session_id, product_code="FILTR-001", quantity="1"):
        return self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": product_code, "quantity": quantity},
            format="json",
        )

    def put_away(self, session_id, location_code="A-01-01"):
        return self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": location_code},
            format="json",
        )

    def complete(self, session_id):
        return self.client.post(
            "/api/scanner/receiving/complete/",
            {"receiving_session_id": session_id},
            format="json",
        )

    def print_report(self, discrepancy, printer_code="ZEBRA-01"):
        return self.client.post(
            f"/api/transfer-discrepancies/{discrepancy.id}/print-report/",
            {"printer_code": printer_code, "worker_code": "WORKER-1"},
            format="json",
        )

    def recover_item(self, discrepancy, product_code="FILTR-001", location_code="A-01-01", quantity="1", operation_id="op-1"):
        return self.client.post(
            f"/api/transfer-discrepancies/{discrepancy.id}/recover-item/",
            {
                "product_code": product_code,
                "destination_location_code": location_code,
                "quantity": quantity,
                "worker_code": "WORKER-1",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def confirm_shortage(self, discrepancy, product_code="FILTR-001", quantity="1", operation_id="shortage-1"):
        return self.client.post(
            f"/api/transfer-discrepancies/{discrepancy.id}/confirm-shortage/",
            {
                "product_code": product_code,
                "quantity": quantity,
                "worker_code": "WORKER-1",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def begin_source_review(self, review, worker_code="WORKER-1"):
        return self.client.post(
            f"/api/transfer-discrepancy-source-reviews/{review.id}/begin/",
            {"worker_code": worker_code},
            format="json",
        )

    def complete_source_review(
        self,
        review,
        finding=TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND,
        note="Picking evidence shows only 4 units confirmed.",
        operation_id="source-review-complete-1",
    ):
        return self.client.post(
            f"/api/transfer-discrepancy-source-reviews/{review.id}/complete/",
            {
                "finding": finding,
                "finding_note": note,
                "worker_code": "WORKER-1",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def acknowledge_reconciliation(self, reconciliation, worker_code="WORKER-1"):
        return self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/acknowledge/",
            {"worker_code": worker_code},
            format="json",
        )

    def begin_source_stock_verification(self, verification, worker_code="WORKER-1"):
        return self.client.post(
            f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/begin/",
            {"worker_code": worker_code},
            format="json",
        )

    def record_source_stock_found(
        self,
        verification,
        product_code="FILTR-001",
        location_code="B-01-01",
        quantity="1",
        operation_id="source-found-1",
    ):
        return self.client.post(
            f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/record-found/",
            {
                "product_code": product_code,
                "destination_location_code": location_code,
                "quantity": quantity,
                "worker_code": "WORKER-1",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def complete_source_search(
        self,
        verification,
        note="Checked picking, staging and loading areas. Remaining stock was not found.",
        operation_id="source-search-complete-1",
    ):
        return self.client.post(
            f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/complete-search/",
            {
                "worker_code": "WORKER-1",
                "search_completion_note": note,
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def complete_manual_reconciliation(
        self,
        reconciliation,
        outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED,
        note="Source search was completed and the remaining unit could not be located.",
        operation_id="manual-decision-1",
    ):
        return self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/complete-manual/",
            {
                "outcome": outcome,
                "decision_note": note,
                "worker_code": "WORKER-1",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def create_acknowledged_manual_reconciliation(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy, operation_id=f"manual-shortage-{discrepancy.id}")
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.INCONCLUSIVE,
            operation_id=f"manual-route-review-{discrepancy.id}",
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.acknowledge_reconciliation(reconciliation)
        reconciliation.refresh_from_db()
        return reconciliation

    def create_acknowledged_transit_reconciliation(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy, operation_id=f"transit-shortage-{discrepancy.id}")
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
            operation_id=f"transit-review-complete-{discrepancy.id}",
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.acknowledge_reconciliation(reconciliation)
        reconciliation.refresh_from_db()
        return reconciliation

    def begin_transit_investigation(self, investigation, worker_code="WORKER-1"):
        return self.client.post(
            f"/api/transfer-discrepancy-transit-investigations/{investigation.id}/begin/",
            {"worker_code": worker_code},
            format="json",
        )

    def complete_transit_investigation(
        self,
        investigation,
        finding=TransferDiscrepancyTransitInvestigation.Finding.TRANSIT_IRREGULARITY_FOUND,
        note="The route history contains an unexplained interruption before destination arrival.",
        operation_id="transit-complete-1",
    ):
        return self.client.post(
            f"/api/transfer-discrepancy-transit-investigations/{investigation.id}/complete/",
            {
                "finding": finding,
                "finding_note": note,
                "worker_code": "WORKER-1",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def create_completed_transit_investigation(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        self.begin_transit_investigation(investigation)
        self.complete_transit_investigation(investigation, operation_id=f"transit-complete-{investigation.id}")
        reconciliation.refresh_from_db()
        investigation.refresh_from_db()
        return reconciliation, investigation

    def create_shortage_discrepancy(self):
        self.pallet.refresh_from_db()
        if self.pallet.status != TransferPallet.Status.IN_TRANSIT:
            self.pallet = self.create_transfer_pallet_fixture()
            self.transfer = self.pallet.transfer
        session_id = self.start_receiving(self.pallet.scan_code).data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")
        self.put_away(session_id)
        self.scan_product(session_id, self.second_product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)
        return TransferDiscrepancy.objects.get(pallet=self.pallet)

    def test_start_known_pallet_creates_active_session(self):
        response = self.start_receiving()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PalletReceivingSession.objects.count(), 1)
        self.pallet.refresh_from_db()
        self.transfer.refresh_from_db()
        self.assertEqual(self.pallet.status, TransferPallet.Status.RECEIVING)
        self.assertEqual(self.transfer.status, InterBranchTransfer.Status.RECEIVING)
        self.assertIsNotNone(self.pallet.receiving_started_at)
        self.assertTrue(AuditLog.objects.filter(message__icontains="Receiving started").exists())

    def test_unknown_pallet_returns_not_found(self):
        response = self.start_receiving("PAL-NOT-FOUND")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_completed_pallet_cannot_start(self):
        self.pallet.status = TransferPallet.Status.RECEIVED
        self.pallet.received_at = timezone.now()
        self.pallet.save(update_fields=["status", "received_at", "updated_at"])

        response = self.start_receiving()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_reopens_existing_active_session(self):
        first = self.start_receiving()
        second = self.start_receiving()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(PalletReceivingSession.objects.count(), 1)
        self.assertEqual(first.data["receiving_session"]["id"], second.data["receiving_session"]["id"])

    def test_start_reopens_existing_pending_session(self):
        first = self.start_receiving()
        session_id = first.data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")

        response = self.start_receiving()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PalletReceivingSession.objects.count(), 1)
        self.assertEqual(response.data["receiving_session"]["id"], session_id)
        self.assertEqual(response.data["receiving_session"]["state"], "waiting_for_location")
        self.assertEqual(response.data["receiving_session"]["pending"]["product_sku"], self.product.sku)
        self.assertEqual(response.data["receiving_session"]["pending"]["quantity"], 2)

    def test_current_returns_pending_session_state(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.barcode, "2")

        response = self.client.get(f"/api/scanner/receiving/current/?receiving_session_id={session_id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["receiving_session"]["id"], session_id)
        self.assertEqual(response.data["receiving_session"]["session_id"], session_id)
        self.assertEqual(response.data["receiving_session"]["pallet"]["scan_code"], "PAL-TEST-001")
        self.assertEqual(response.data["receiving_session"]["state"], "waiting_for_location")
        self.assertEqual(response.data["receiving_session"]["pending"]["product_sku"], self.product.sku)
        self.assertEqual(response.data["receiving_session"]["current_item"]["product_sku"], self.product.sku)
        self.assertEqual(response.data["receiving_session"]["pending"]["quantity"], 2)
        self.assertEqual(response.data["receiving_session"]["pending_quantity"], 2)

    def test_scan_expected_product_by_barcode_succeeds(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]

        response = self.scan_product(session_id, self.product.barcode, "2")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["receiving_session"]["state"], "waiting_for_location")
        self.assertEqual(response.data["receiving_session"]["pending"]["quantity"], 2)

    def test_unexpected_product_is_rejected(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]

        response = self.scan_product(session_id, self.unexpected_product.sku, "1")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not expected", response.data["detail"])

    def test_invalid_quantities_are_rejected(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]

        for quantity in ["0", "-1", "1.5", "abc"]:
            with self.subTest(quantity=quantity):
                response = self.scan_product(session_id, self.product.sku, quantity)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_quantity_above_remaining_is_rejected(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]

        response = self.scan_product(session_id, self.product.sku, "4")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("remaining pallet quantity", response.data["detail"])

    def test_put_away_updates_inventory_manifest_scan_movement_and_audit(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")

        response = self.put_away(session_id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        inventory = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
        )
        self.assertEqual(inventory.quantity_on_hand, Decimal("2"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.received_quantity, Decimal("2"))
        self.assertEqual(PalletReceivingScan.objects.count(), 1)
        self.assertTrue(StockMovement.objects.filter(reference=self.pallet.scan_code, quantity=Decimal("2")).exists())
        self.assertTrue(AuditLog.objects.filter(message__icontains="Received 2").exists())
        self.assertEqual(response.data["receiving_session"]["state"], "waiting_for_product")
        self.assertIsNone(response.data["receiving_session"]["current_item"])
        self.assertIsNone(response.data["receiving_session"]["pending"])
        self.assertIsNone(response.data["receiving_session"]["pending_quantity"])

    def test_wrong_branch_location_is_rejected(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "1")

        response = self.put_away(session_id, "B-01-01")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Wrong branch", response.data["detail"])
        current = self.client.get(f"/api/scanner/receiving/current/?receiving_session_id={session_id}")
        self.assertEqual(current.status_code, status.HTTP_200_OK)
        self.assertEqual(current.data["receiving_session"]["state"], "waiting_for_location")
        self.assertEqual(current.data["receiving_session"]["pending"]["product_sku"], self.product.sku)
        self.assertEqual(current.data["receiving_session"]["pending"]["quantity"], 1)

    def test_unknown_location_keeps_pending_product(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")

        response = self.put_away(session_id, "UNKNOWN-LOC")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        current = self.client.get(f"/api/scanner/receiving/current/?receiving_session_id={session_id}")
        self.assertEqual(current.data["receiving_session"]["state"], "waiting_for_location")
        self.assertEqual(current.data["receiving_session"]["current_item"]["product_sku"], self.product.sku)
        self.assertEqual(current.data["receiving_session"]["pending_quantity"], 2)

    def test_incomplete_pallet_closes_with_shortage_discrepancy(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")
        self.put_away(session_id)
        self.scan_product(session_id, self.second_product.sku, "2")
        self.put_away(session_id)

        response = self.complete(session_id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["result"], "discrepancy")
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.status, TransferPallet.Status.CLOSED_WITH_DISCREPANCY)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)
        self.assertEqual(discrepancy.items.count(), 1)
        item = discrepancy.items.get()
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.discrepancy_type, TransferDiscrepancyItem.DiscrepancyType.SHORTAGE)
        self.assertEqual(item.expected_quantity, Decimal("3.000"))
        self.assertEqual(item.received_quantity, Decimal("2.000"))
        self.assertEqual(item.difference_quantity, Decimal("-1.000"))
        self.assertEqual(item.discrepancy_quantity, Decimal("1.000"))
        session = PalletReceivingSession.objects.get(pk=session_id)
        self.assertEqual(session.status, PalletReceivingSession.Status.COMPLETED)

    def test_multiple_shortage_lines_create_one_case_with_multiple_items(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]

        response = self.complete(session_id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)
        self.assertEqual(discrepancy.items.count(), 2)
        self.assertEqual(
            set(discrepancy.items.values_list("product__sku", flat=True)),
            {self.product.sku, self.second_product.sku},
        )

    def test_exactly_received_manifest_can_be_completed(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "3")
        self.put_away(session_id)
        self.scan_product(session_id, self.second_product.sku, "2")
        self.put_away(session_id)

        response = self.complete(session_id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.pallet.refresh_from_db()
        self.transfer.refresh_from_db()
        session = PalletReceivingSession.objects.get(pk=session_id)
        self.assertEqual(session.status, PalletReceivingSession.Status.COMPLETED)
        self.assertEqual(self.pallet.status, TransferPallet.Status.RECEIVED)
        self.assertEqual(self.transfer.status, InterBranchTransfer.Status.RECEIVED)
        self.assertIsNotNone(self.pallet.received_at)
        self.assertFalse(TransferDiscrepancy.objects.filter(pallet=self.pallet).exists())

    def test_completed_pallet_cannot_receive_more_goods(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "3")
        self.put_away(session_id)
        self.scan_product(session_id, self.second_product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)

        response = self.start_receiving()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repeated_close_does_not_duplicate_discrepancy_cases(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)

        response = self.complete(session_id)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TransferDiscrepancy.objects.filter(pallet=self.pallet).count(), 1)
        self.assertEqual(TransferDiscrepancyItem.objects.filter(discrepancy__pallet=self.pallet).count(), 2)

    def test_pallet_cannot_close_while_waiting_for_location(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")

        response = self.complete(session_id)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pending put-away", response.data["detail"])
        current = self.client.get(f"/api/scanner/receiving/current/?receiving_session_id={session_id}")
        self.assertEqual(current.data["receiving_session"]["state"], "waiting_for_location")
        self.assertEqual(current.data["receiving_session"]["pending_quantity"], 2)

    def test_completed_session_is_not_restored_as_active(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "3")
        self.put_away(session_id)
        self.scan_product(session_id, self.second_product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)

        response = self.client.get(f"/api/scanner/receiving/current/?receiving_session_id={session_id}")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("not active", response.data["detail"])

    def test_contents_resolves_pallet_manifest(self):
        response = self.client.get("/api/scanner/contents/?code=PAL-TEST-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "pallet")
        self.assertEqual(response.data["code"], "PAL-TEST-001")
        self.assertEqual(len(response.data["items"]), 2)

    def test_contents_resolves_discrepancy_pallet(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)

        response = self.client.get("/api/scanner/contents/?code=PAL-TEST-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], TransferPallet.Status.CLOSED_WITH_DISCREPANCY)
        self.assertIn("discrepancy_reference", response.data)
        shortage_row = next(item for item in response.data["items"] if item["sku"] == self.product.sku)
        self.assertEqual(shortage_row["missing_quantity"], 1)

    def test_discrepancy_register_and_detail_expose_summary_lines_and_scan_history(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)

        list_response = self.client.get("/api/transfer-discrepancies/")
        detail_response = self.client.get(f"/api/transfer-discrepancies/{discrepancy.id}/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["count"], 1)
        self.assertEqual(list_response.data["results"][0]["reference"], discrepancy.reference)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["pallet_code"], self.pallet.scan_code)
        self.assertEqual(detail_response.data["line_count"], 2)
        product_line = next(item for item in detail_response.data["items"] if item["product_sku"] == self.product.sku)
        self.assertEqual(product_line["scan_history"][0]["destination_location_code"], self.destination_location.code)

    def test_shortage_is_not_posted_before_report_print(self):
        discrepancy = self.create_shortage_discrepancy()

        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.OPEN)
        self.assertIsNone(discrepancy.report_printed_at)
        self.assertFalse(
            InventoryItem.objects.filter(
                branch=self.destination_branch,
                location=self.unconfirmed_location,
                product=self.product,
            ).exists()
        )

    def test_first_report_print_posts_shortage_to_unconfirmed_once(self):
        discrepancy = self.create_shortage_discrepancy()

        response = self.print_report(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["first_print"])
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.INVESTIGATING)
        self.assertEqual(discrepancy.report_print_count, 1)
        self.assertEqual(discrepancy.last_report_printer_code, "ZEBRA-01")
        self.assertIsNotNone(discrepancy.report_printed_at)
        self.assertIsNotNone(discrepancy.shortage_posted_at)
        shortage_line = discrepancy.items.get(product=self.product)
        self.assertEqual(shortage_line.posted_to_unconfirmed_quantity, Decimal("1.000"))
        inventory = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.unconfirmed_location,
            product=self.product,
        )
        self.assertEqual(inventory.quantity_on_hand, Decimal("1.000"))
        self.assertTrue(
            StockMovement.objects.filter(
                reference=discrepancy.reference,
                movement_type=StockMovement.MovementType.RECEIVING_DISCREPANCY,
                quantity=Decimal("1.000"),
            ).exists()
        )
        self.assertTrue(AuditLog.objects.filter(message__icontains="posted to location UNCONFIRMED").exists())

    def test_report_reprint_does_not_post_shortage_again(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.print_report(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["first_print"])
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.report_print_count, 2)
        inventory = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.unconfirmed_location,
            product=self.product,
        )
        self.assertEqual(inventory.quantity_on_hand, Decimal("1.000"))
        shortage_line = discrepancy.items.get(product=self.product)
        self.assertEqual(shortage_line.posted_to_unconfirmed_quantity, Decimal("1.000"))
        self.assertEqual(
            StockMovement.objects.filter(
                reference=discrepancy.reference,
                movement_type=StockMovement.MovementType.RECEIVING_DISCREPANCY,
            ).count(),
            1,
        )

    def test_missing_unconfirmed_location_rolls_back_report_print(self):
        discrepancy = self.create_shortage_discrepancy()
        self.unconfirmed_location.delete()

        response = self.print_report(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.report_print_count, 0)
        self.assertIsNone(discrepancy.report_printed_at)
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.OPEN)
        self.assertFalse(StockMovement.objects.filter(reference=discrepancy.reference).exists())

    def test_unconfirmed_contents_shows_posted_shortage(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.client.get("/api/scanner/contents/?code=UNCONFIRMED")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_type"], "location")
        self.assertEqual(response.data["items"][0]["sku"], self.product.sku)
        self.assertEqual(response.data["items"][0]["quantity"], 1)

    def test_full_recovery_moves_stock_and_resolves_discrepancy(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.recover_item(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["recovery"]["status"], TransferDiscrepancy.Status.RESOLVED)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.RESOLVED)
        self.assertIsNotNone(discrepancy.resolved_at)
        line = discrepancy.items.get(product=self.product)
        self.assertEqual(line.recovered_quantity, Decimal("1.000"))
        unconfirmed = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product)
        destination = InventoryItem.objects.get(location=self.destination_location, product=self.product)
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("0.000"))
        self.assertEqual(destination.quantity_on_hand, Decimal("3.000"))
        self.assertEqual(TransferDiscrepancyRecovery.objects.filter(discrepancy=discrepancy).count(), 1)
        self.assertTrue(StockMovement.objects.filter(reference=discrepancy.reference, movement_type=StockMovement.MovementType.DISCREPANCY_RECOVERY).exists())
        self.assertTrue(AuditLog.objects.filter(message__icontains="was resolved").exists())

    def test_recovery_retry_with_same_operation_id_does_not_move_twice(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        first = self.recover_item(discrepancy, operation_id="retry-1")
        second = self.recover_item(discrepancy, operation_id="retry-1")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancyRecovery.objects.filter(discrepancy=discrepancy).count(), 1)
        destination = InventoryItem.objects.get(location=self.destination_location, product=self.product)
        self.assertEqual(destination.quantity_on_hand, Decimal("3.000"))

    def test_open_discrepancy_cannot_recover_before_report_print(self):
        discrepancy = self.create_shortage_discrepancy()

        response = self.recover_item(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("investigating", response.data["detail"])

    def test_recovery_rejects_unrelated_product_and_unconfirmed_destination(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        wrong_product = self.recover_item(discrepancy, product_code=self.unexpected_product.sku)
        wrong_location = self.recover_item(discrepancy, location_code="UNCONFIRMED", operation_id="op-2")

        self.assertEqual(wrong_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_location.status_code, status.HTTP_400_BAD_REQUEST)

    def test_recovery_rejects_quantity_above_remaining(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.recover_item(discrepancy, quantity="2")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exceeds", response.data["detail"])

    def test_full_confirmed_shortage_removes_unconfirmed_and_closes_case(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.confirm_shortage(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["confirmation"]["status"], TransferDiscrepancy.Status.CONFIRMED_SHORTAGE)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE)
        self.assertIsNotNone(discrepancy.confirmed_shortage_at)
        self.assertEqual(discrepancy.confirmed_shortage_by_worker_code, "WORKER-1")
        line = discrepancy.items.get(product=self.product)
        self.assertEqual(line.confirmed_shortage_quantity, Decimal("1.000"))
        self.assertEqual(line.recovered_quantity, Decimal("0.000"))
        unconfirmed = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product)
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("0.000"))
        self.assertEqual(TransferDiscrepancyShortageConfirmation.objects.filter(discrepancy=discrepancy).count(), 1)
        self.assertTrue(
            StockMovement.objects.filter(
                reference=discrepancy.reference,
                movement_type=StockMovement.MovementType.DISCREPANCY_SHORTAGE,
                quantity=Decimal("1.000"),
            ).exists()
        )
        self.assertTrue(AuditLog.objects.filter(message__icontains="closed with confirmed shortage").exists())
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.assertEqual(review.status, TransferDiscrepancySourceReview.Status.PENDING_REVIEW)
        self.assertEqual(review.source_branch, self.source_branch)
        self.assertTrue(review.reference.startswith("SRV-"))
        self.assertTrue(AuditLog.objects.filter(message__icontains="Source review").exists())

    def test_resolved_discrepancy_does_not_create_source_review(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.recover_item(discrepancy)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(TransferDiscrepancySourceReview.objects.filter(discrepancy=discrepancy).exists())

    def test_partial_and_multiple_shortage_confirmations_accumulate(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.complete(session_id)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)
        self.print_report(discrepancy)

        first = self.confirm_shortage(discrepancy, quantity="1", operation_id="shortage-part-1")
        second = self.confirm_shortage(discrepancy, quantity="2", operation_id="shortage-part-2")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["confirmation"]["status"], TransferDiscrepancy.Status.INVESTIGATING)
        self.assertEqual(first.data["confirmation"]["line_remaining_quantity"], "2.000")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        discrepancy.refresh_from_db()
        line = discrepancy.items.get(product=self.product)
        self.assertEqual(line.confirmed_shortage_quantity, Decimal("3.000"))
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.INVESTIGATING)
        self.assertEqual(TransferDiscrepancyShortageConfirmation.objects.filter(discrepancy=discrepancy, product=self.product).count(), 2)
        unconfirmed = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product)
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("0.000"))

    def test_mixed_recovery_and_shortage_final_status_is_confirmed_shortage(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.complete(session_id)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)
        self.print_report(discrepancy)

        recovery = self.recover_item(discrepancy, quantity="1", operation_id="mixed-recovery")
        confirmation = self.confirm_shortage(discrepancy, quantity="2", operation_id="mixed-shortage")

        self.assertEqual(recovery.status_code, status.HTTP_200_OK)
        self.assertEqual(confirmation.status_code, status.HTTP_200_OK)
        discrepancy.refresh_from_db()
        line = discrepancy.items.get(product=self.product)
        self.assertEqual(line.recovered_quantity, Decimal("1.000"))
        self.assertEqual(line.confirmed_shortage_quantity, Decimal("2.000"))
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.INVESTIGATING)

        final_line = discrepancy.items.get(product=self.second_product)
        final = self.confirm_shortage(discrepancy, product_code=final_line.product.sku, quantity="2", operation_id="mixed-final")

        self.assertEqual(final.status_code, status.HTTP_200_OK)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE)
        self.assertEqual(final.data["confirmation"]["total_remaining_quantity"], "0.000")

    def test_shortage_confirmation_retry_does_not_remove_inventory_twice(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        first = self.confirm_shortage(discrepancy, operation_id="retry-shortage")
        second = self.confirm_shortage(discrepancy, operation_id="retry-shortage")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancyShortageConfirmation.objects.filter(discrepancy=discrepancy).count(), 1)
        self.assertEqual(TransferDiscrepancySourceReview.objects.filter(discrepancy=discrepancy).count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(
                reference=discrepancy.reference,
                movement_type=StockMovement.MovementType.DISCREPANCY_SHORTAGE,
            ).count(),
            1,
        )
        unconfirmed = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product)
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("0.000"))

    def test_shortage_confirmation_validates_state_product_quantity_and_inventory(self):
        discrepancy = self.create_shortage_discrepancy()

        open_response = self.confirm_shortage(discrepancy, operation_id="bad-open")
        self.assertEqual(open_response.status_code, status.HTTP_400_BAD_REQUEST)

        self.print_report(discrepancy)
        wrong_product = self.confirm_shortage(discrepancy, product_code=self.unexpected_product.sku, operation_id="bad-product")
        zero = self.confirm_shortage(discrepancy, quantity="0", operation_id="bad-zero")
        above_remaining = self.confirm_shortage(discrepancy, quantity="2", operation_id="bad-quantity")
        InventoryItem.objects.filter(location=self.unconfirmed_location, product=self.product).update(quantity_on_hand=Decimal("0.000"))
        no_stock = self.confirm_shortage(discrepancy, quantity="1", operation_id="bad-stock")

        self.assertEqual(wrong_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(zero.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(above_remaining.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(no_stock.status_code, status.HTTP_400_BAD_REQUEST)
        line = discrepancy.items.get(product=self.product)
        self.assertEqual(line.confirmed_shortage_quantity, Decimal("0.000"))
        self.assertFalse(TransferDiscrepancyShortageConfirmation.objects.filter(discrepancy=discrepancy).exists())

    def test_recovery_remaining_calculation_includes_confirmed_shortage(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.complete(session_id)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy, quantity="2", operation_id="confirm-before-recovery")

        too_much = self.recover_item(discrepancy, quantity="2", operation_id="recover-too-much")
        valid = self.recover_item(discrepancy, quantity="1", operation_id="recover-remaining")

        self.assertEqual(too_much.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(valid.status_code, status.HTTP_200_OK)
        line = TransferDiscrepancyItem.objects.get(discrepancy=discrepancy, product=self.product)
        self.assertEqual(line.recovered_quantity, Decimal("1.000"))
        self.assertEqual(line.confirmed_shortage_quantity, Decimal("2.000"))
        self.assertEqual(valid.data["recovery"]["line_remaining_quantity"], "0.000")

    def test_source_review_begin_and_complete_records_finding_without_inventory_changes(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        movement_count = StockMovement.objects.count()
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand

        begin = self.begin_source_review(review)
        complete = self.complete_source_review(review)

        self.assertEqual(begin.status_code, status.HTTP_200_OK)
        self.assertEqual(complete.status_code, status.HTTP_200_OK)
        review.refresh_from_db()
        discrepancy.refresh_from_db()
        self.assertEqual(review.status, TransferDiscrepancySourceReview.Status.COMPLETED)
        self.assertEqual(review.finding, TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND)
        self.assertEqual(review.finding_note, "Picking evidence shows only 4 units confirmed.")
        self.assertEqual(review.started_by_worker_code, "WORKER-1")
        self.assertEqual(review.completed_by_worker_code, "WORKER-1")
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        unconfirmed_after = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
        self.assertEqual(unconfirmed_after, unconfirmed_before)
        self.assertTrue(AuditLog.objects.filter(message__icontains="began source review").exists())
        self.assertTrue(AuditLog.objects.filter(message__icontains="completed source review").exists())

    def test_source_review_begin_is_idempotent_and_completed_cannot_begin(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)

        first = self.begin_source_review(review)
        second = self.begin_source_review(review)
        self.complete_source_review(review)
        completed_begin = self.begin_source_review(review)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(completed_begin.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AuditLog.objects.filter(message__icontains="began source review").count(), 1)

    def test_source_review_complete_idempotency_and_validation(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)

        pending_complete = self.complete_source_review(review, operation_id="pending-complete")
        invalid = self.client.post(
            f"/api/transfer-discrepancy-source-reviews/{review.id}/complete/",
            {
                "finding": "bad_finding",
                "worker_code": "WORKER-1",
                "client_operation_id": "invalid-finding",
            },
            format="json",
        )
        self.begin_source_review(review)
        first = self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
            note="Manifest evidence matches expected quantity.",
            operation_id="review-retry",
        )
        retry = self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
            note="Manifest evidence matches expected quantity.",
            operation_id="review-retry",
        )
        overwrite = self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.INCONCLUSIVE,
            note="Trying to overwrite.",
            operation_id="review-overwrite",
        )

        self.assertEqual(pending_complete.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(retry.status_code, status.HTTP_200_OK)
        self.assertEqual(overwrite.status_code, status.HTTP_400_BAD_REQUEST)
        review.refresh_from_db()
        self.assertEqual(review.finding, TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES)
        self.assertEqual(AuditLog.objects.filter(message__icontains="completed source review").count(), 1)

    def test_source_review_detail_api_exposes_accounting_and_evidence(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)

        response = self.client.get(f"/api/transfer-discrepancy-source-reviews/{review.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reference"], review.reference)
        self.assertEqual(response.data["discrepancy_reference"], discrepancy.reference)
        self.assertEqual(response.data["source_branch_code"], self.source_branch.code)
        self.assertEqual(response.data["destination_branch_code"], self.destination_branch.code)
        self.assertEqual(response.data["pallet_code"], self.pallet.scan_code)
        self.assertEqual(response.data["total_confirmed_shortage_quantity"], "1.000")
        self.assertEqual(len(response.data["source_dispatch_evidence"]), 2)
        self.assertEqual(len(response.data["destination_receiving_evidence"]), 2)
        self.assertEqual(len(response.data["shortage_confirmations"]), 1)

    def test_source_review_completion_creates_reconciliation_for_each_route(self):
        self.assertEqual(
            reconciliation_route_for_finding(TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND),
            TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION,
        )
        self.assertEqual(
            reconciliation_route_for_finding(TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES),
            TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION,
        )
        self.assertEqual(
            reconciliation_route_for_finding(TransferDiscrepancySourceReview.Finding.INCONCLUSIVE),
            TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION,
        )

        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        movement_count = StockMovement.objects.count()

        response = self.complete_source_review(review)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.assertEqual(reconciliation.route, TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION)
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.PENDING_ACTION)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        detail = self.client.get(f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/")
        self.assertEqual(detail.data["route"], TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION)
        self.assertEqual(
            detail.data["next_action_label"],
            "Verify whether the confirmed shortage quantity still physically exists at the source branch.",
        )

    def test_reconciliation_creation_is_idempotent_on_source_review_retry(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)

        first = self.complete_source_review(review, operation_id="reconciliation-retry")
        second = self.complete_source_review(review, operation_id="reconciliation-retry")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancyReconciliation.objects.filter(discrepancy=discrepancy).count(), 1)
        self.assertEqual(AuditLog.objects.filter(message__icontains="Reconciliation case").count(), 1)

    def test_reconciliation_acknowledge_is_idempotent_and_does_not_change_inventory(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(review)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        movement_count = StockMovement.objects.count()
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand

        first = self.acknowledge_reconciliation(reconciliation)
        second = self.acknowledge_reconciliation(reconciliation)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.IN_PROGRESS)
        self.assertEqual(reconciliation.acknowledged_by_worker_code, "WORKER-1")
        self.assertIsNotNone(reconciliation.acknowledged_at)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        unconfirmed_after = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
        self.assertEqual(unconfirmed_after, unconfirmed_before)
        self.assertEqual(AuditLog.objects.filter(message__icontains="acknowledged reconciliation case").count(), 1)

    def test_reconciliation_list_filters_and_search_work(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(review)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)

        by_status = self.client.get("/api/transfer-discrepancy-reconciliations/", {"status": "pending_action"})
        by_route = self.client.get(
            "/api/transfer-discrepancy-reconciliations/",
            {"route": TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION},
        )
        by_search = self.client.get(
            "/api/transfer-discrepancy-reconciliations/",
            {"search": reconciliation.reference},
        )
        by_discrepancy = self.client.get(
            "/api/transfer-discrepancy-reconciliations/",
            {"search": discrepancy.reference},
        )

        self.assertEqual(by_status.data["count"], 1)
        self.assertEqual(by_route.data["count"], 1)
        self.assertEqual(by_search.data["count"], 1)
        self.assertEqual(by_discrepancy.data["count"], 1)

    def test_source_stock_verification_created_on_source_route_acknowledgement(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(review)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)

        first = self.acknowledge_reconciliation(reconciliation)
        second = self.acknowledge_reconciliation(reconciliation)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancySourceStockVerification.objects.filter(reconciliation=reconciliation).count(), 1)
        verification = TransferDiscrepancySourceStockVerification.objects.get(reconciliation=reconciliation)
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.PENDING_VERIFICATION)
        self.assertEqual(verification.items.count(), 1)
        item = verification.items.get(product=self.product)
        self.assertEqual(item.target_quantity, Decimal("1.000"))
        self.assertEqual(item.found_quantity, Decimal("0.000"))
        self.assertEqual(AuditLog.objects.filter(message__icontains="was created for reconciliation").count(), 1)

    def test_transit_and_manual_reconciliations_do_not_create_source_verification(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(review, finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.acknowledge_reconciliation(reconciliation)

        self.assertFalse(TransferDiscrepancySourceStockVerification.objects.filter(reconciliation=reconciliation).exists())

    def test_begin_source_stock_verification_is_idempotent_and_inventory_neutral(self):
        verification = self.create_source_stock_verification()
        movement_count = StockMovement.objects.count()

        first = self.begin_source_stock_verification(verification)
        second = self.begin_source_stock_verification(verification)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        verification.refresh_from_db()
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.INVESTIGATING)
        self.assertEqual(verification.started_by_worker_code, "WORKER-1")
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(AuditLog.objects.filter(message__icontains="began source stock verification").count(), 1)

    def create_source_stock_verification(self, operation_id="source-review-complete-1"):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(review, operation_id=operation_id)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.acknowledge_reconciliation(reconciliation)
        return TransferDiscrepancySourceStockVerification.objects.get(reconciliation=reconciliation)

    def test_full_source_stock_recovery_restores_source_inventory_and_completes(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        source_before = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        before_quantity = source_before.quantity_on_hand if source_before else Decimal("0")
        destination_before = InventoryItem.objects.filter(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
        ).get().quantity_on_hand
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand

        response = self.record_source_stock_found(verification)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        verification.refresh_from_db()
        verification.reconciliation.refresh_from_db()
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED)
        self.assertEqual(verification.reconciliation.status, TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertEqual(verification.items.get(product=self.product).found_quantity, Decimal("1.000"))
        source_after = InventoryItem.objects.get(branch=self.source_branch, location=self.wrong_branch_location, product=self.product)
        self.assertEqual(source_after.quantity_on_hand, before_quantity + Decimal("1.000"))
        self.assertEqual(
            InventoryItem.objects.get(branch=self.destination_branch, location=self.destination_location, product=self.product).quantity_on_hand,
            destination_before,
        )
        self.assertEqual(InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand, unconfirmed_before)
        self.assertEqual(TransferDiscrepancySourceStockRecovery.objects.filter(verification=verification).count(), 1)
        self.assertTrue(
            StockMovement.objects.filter(
                reference=verification.reference,
                movement_type=StockMovement.MovementType.SOURCE_DISCREPANCY_RECOVERY,
            ).exists()
        )
        discrepancy = verification.reconciliation.discrepancy
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE)

    def test_source_stock_recovery_retry_does_not_restore_twice(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)

        first = self.record_source_stock_found(verification, operation_id="source-retry")
        second = self.record_source_stock_found(verification, operation_id="source-retry")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancySourceStockRecovery.objects.filter(verification=verification).count(), 1)
        item = TransferDiscrepancySourceStockVerificationItem.objects.get(verification=verification, product=self.product)
        self.assertEqual(item.found_quantity, Decimal("1.000"))
        self.assertEqual(AuditLog.objects.filter(message__icontains="restored it to inventory").count(), 1)
        self.assertEqual(AuditLog.objects.filter(message__icontains="was completed after all target quantity").count(), 1)

    def test_source_stock_recovery_validates_product_quantity_location_and_state(self):
        verification = self.create_source_stock_verification()
        pending = self.record_source_stock_found(verification, operation_id="bad-pending")
        self.begin_source_stock_verification(verification)
        wrong_product = self.record_source_stock_found(verification, product_code=self.unexpected_product.sku, operation_id="bad-product")
        zero = self.record_source_stock_found(verification, quantity="0", operation_id="bad-zero")
        too_much = self.record_source_stock_found(verification, quantity="2", operation_id="bad-quantity")
        wrong_branch = self.record_source_stock_found(verification, location_code=self.destination_location.code, operation_id="bad-branch")
        unconfirmed = self.record_source_stock_found(verification, location_code=self.unconfirmed_location.code, operation_id="bad-unconfirmed")

        self.assertEqual(pending.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(zero.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(too_much.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_branch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(unconfirmed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TransferDiscrepancySourceStockRecovery.objects.filter(verification=verification).exists())

    def test_complete_source_search_with_zero_found_requires_manual_action_without_inventory_mutation(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        source_before = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        source_quantity_before = source_before.quantity_on_hand if source_before else Decimal("0")
        destination_before = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
        ).quantity_on_hand
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
        movement_count = StockMovement.objects.count()

        response = self.complete_source_search(verification)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        verification.refresh_from_db()
        verification.reconciliation.refresh_from_db()
        item = verification.items.get(product=self.product)
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED)
        self.assertEqual(verification.search_completed_by_worker_code, "WORKER-1")
        self.assertEqual(verification.search_completion_note, "Checked picking, staging and loading areas. Remaining stock was not found.")
        self.assertEqual(item.found_quantity, Decimal("0.000"))
        self.assertEqual(item.target_quantity - item.found_quantity, Decimal("1.000"))
        self.assertEqual(verification.reconciliation.route, TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION)
        self.assertEqual(verification.reconciliation.status, TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED)
        source_after = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        self.assertEqual(source_after.quantity_on_hand if source_after else Decimal("0"), source_quantity_before)
        self.assertEqual(
            InventoryItem.objects.get(branch=self.destination_branch, location=self.destination_location, product=self.product).quantity_on_hand,
            destination_before,
        )
        self.assertEqual(InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand, unconfirmed_before)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(AuditLog.objects.filter(message__icontains="completed source stock verification").count(), 1)
        self.assertEqual(AuditLog.objects.filter(message__icontains="now requires manual action").count(), 1)

    def test_complete_source_search_after_partial_found_preserves_found_quantity_only(self):
        verification = self.create_source_stock_verification()
        item = verification.items.get(product=self.product)
        item.target_quantity = Decimal("5.000")
        item.save(update_fields=["target_quantity", "updated_at"])
        self.begin_source_stock_verification(verification)
        source_before = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        source_quantity_before = source_before.quantity_on_hand if source_before else Decimal("0")

        found = self.record_source_stock_found(verification, quantity="2")
        movement_count_after_found = StockMovement.objects.count()
        response = self.complete_source_search(verification)

        self.assertEqual(found.status_code, status.HTTP_200_OK)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        verification.refresh_from_db()
        verification.reconciliation.refresh_from_db()
        item.refresh_from_db()
        source_after = InventoryItem.objects.get(branch=self.source_branch, location=self.wrong_branch_location, product=self.product)
        self.assertEqual(item.found_quantity, Decimal("2.000"))
        self.assertEqual(item.target_quantity - item.found_quantity, Decimal("3.000"))
        self.assertEqual(source_after.quantity_on_hand, source_quantity_before + Decimal("2.000"))
        self.assertEqual(StockMovement.objects.count(), movement_count_after_found)
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED)
        self.assertEqual(verification.reconciliation.status, TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED)

    def test_complete_source_search_is_idempotent_and_does_not_overwrite_closure(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)

        first = self.complete_source_search(verification, note="First note.", operation_id="same-source-search")
        second = self.complete_source_search(verification, note="Different note.", operation_id="same-source-search")
        third = self.complete_source_search(verification, note="Another note.", operation_id="different-source-search")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_400_BAD_REQUEST)
        verification.refresh_from_db()
        self.assertEqual(verification.search_completion_note, "First note.")
        self.assertEqual(AuditLog.objects.filter(message__icontains="completed source stock verification").count(), 1)
        self.assertEqual(AuditLog.objects.filter(message__icontains="now requires manual action").count(), 1)

    def test_complete_source_search_validates_state_route_status_and_remaining(self):
        pending = self.create_source_stock_verification()
        pending_response = self.complete_source_search(pending, operation_id="bad-pending")
        self.begin_source_stock_verification(pending)
        self.record_source_stock_found(pending, operation_id="complete-all")
        zero_remaining = self.complete_source_search(pending, operation_id="bad-zero-remaining")

        self.assertEqual(pending_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(zero_remaining.status_code, status.HTTP_400_BAD_REQUEST)

    def test_record_found_rejected_after_completed_unresolved(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        self.complete_source_search(verification)
        movement_count = StockMovement.objects.count()

        response = self.record_source_stock_found(verification, operation_id="after-unresolved")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "This source stock verification has already been completed.")
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_discrepancy_audit_quantities_are_whole_pieces(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)

        event_messages = list(AuditLog.objects.values_list("message", flat=True))
        self.assertTrue(any("1 missing unit" in message for message in event_messages))
        self.assertTrue(any("1 unit of FILTR-001 as missing" in message for message in event_messages))
        self.assertTrue(any("0 recovered, 1 confirmed missing" in message for message in event_messages))
        self.assertFalse(any(".000" in message for message in event_messages))

    def test_source_verification_unresolved_audit_quantities_are_whole_pieces(self):
        verification = self.create_source_stock_verification()
        item = verification.items.get(product=self.product)
        item.target_quantity = Decimal("5.000")
        item.save(update_fields=["target_quantity", "updated_at"])
        self.begin_source_stock_verification(verification)
        self.record_source_stock_found(verification, quantity="2")
        self.complete_source_search(verification)

        event_messages = list(AuditLog.objects.values_list("message", flat=True))
        self.assertTrue(any("found 2 units of FILTR-001" in message for message in event_messages))
        self.assertTrue(any("with 3 units unresolved" in message for message in event_messages))
        self.assertTrue(any("3 source-verification units remain unresolved" in message for message in event_messages))
        self.assertFalse(any(".000" in message for message in event_messages))

    def test_manual_decision_completes_source_verification_escalation_without_inventory_mutation(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        self.complete_source_search(verification)
        verification.refresh_from_db()
        reconciliation = verification.reconciliation
        source_before = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        source_quantity_before = source_before.quantity_on_hand if source_before else Decimal("0")
        destination_before = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
        ).quantity_on_hand
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
        movement_count = StockMovement.objects.count()

        response = self.complete_manual_reconciliation(reconciliation)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reconciliation.refresh_from_db()
        verification.refresh_from_db()
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation)
        self.assertEqual(decision.outcome, TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED)
        self.assertEqual(decision.decision_note, "Source search was completed and the remaining unit could not be located.")
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertIsNotNone(reconciliation.completed_at)
        self.assertEqual(reconciliation.completed_by_worker_code, "WORKER-1")
        self.assertEqual(reconciliation.route, TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION)
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED)
        source_after = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        self.assertEqual(source_after.quantity_on_hand if source_after else Decimal("0"), source_quantity_before)
        self.assertEqual(
            InventoryItem.objects.get(branch=self.destination_branch, location=self.destination_location, product=self.product).quantity_on_hand,
            destination_before,
        )
        self.assertEqual(InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand, unconfirmed_before)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(AuditLog.objects.filter(message__icontains="with final outcome: Source loss confirmed").count(), 1)

    def test_manual_decision_supports_original_manual_route(self):
        reconciliation = self.create_acknowledged_manual_reconciliation()
        movement_count = StockMovement.objects.count()

        response = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED,
            note="Available evidence does not establish where the missing unit was lost.",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reconciliation.refresh_from_db()
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation)
        self.assertEqual(reconciliation.route, TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION)
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertEqual(decision.outcome, TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED)
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_manual_decision_can_record_administrative_error(self):
        reconciliation = self.create_acknowledged_manual_reconciliation()

        response = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
            note="Evidence indicates a process discrepancy requiring no automatic inventory action.",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation)
        self.assertEqual(decision.outcome, TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR)

    def test_manual_decision_validates_route_status_outcome_and_note(self):
        verification = self.create_source_stock_verification()
        pending_reconciliation = verification.reconciliation
        pending_response = self.complete_manual_reconciliation(pending_reconciliation, operation_id="bad-pending")
        invalid_outcome = self.complete_manual_reconciliation(
            pending_reconciliation,
            outcome="transit_loss_confirmed",
            operation_id="bad-outcome",
        )
        empty_note = self.complete_manual_reconciliation(
            pending_reconciliation,
            note="",
            operation_id="bad-note",
        )

        self.assertEqual(pending_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_outcome.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(empty_note.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TransferDiscrepancyManualReconciliationDecision.objects.exists())

    def test_manual_decision_rejects_transit_route_before_completed_investigation(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
            operation_id="transit-route-review",
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.acknowledge_reconciliation(reconciliation)

        response = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "Transit reconciliation must require manual action.")
        self.assertFalse(TransferDiscrepancyManualReconciliationDecision.objects.exists())

    def test_source_route_rejects_transit_loss_outcome(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        self.complete_source_search(verification)
        reconciliation = verification.reconciliation

        response = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            operation_id="bad-source-transit-outcome",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "This outcome is not allowed for source stock verification.")
        self.assertFalse(TransferDiscrepancyManualReconciliationDecision.objects.exists())

    def test_manual_decision_retry_does_not_overwrite_or_duplicate_audit(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        self.complete_source_search(verification)
        reconciliation = verification.reconciliation

        first = self.complete_manual_reconciliation(reconciliation, note="Original decision.", operation_id="same-manual")
        second = self.complete_manual_reconciliation(reconciliation, note="Changed decision.", operation_id="same-manual")
        third = self.complete_manual_reconciliation(reconciliation, note="Another decision.", operation_id="different-manual")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TransferDiscrepancyManualReconciliationDecision.objects.count(), 1)
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get()
        self.assertEqual(decision.decision_note, "Original decision.")
        self.assertEqual(AuditLog.objects.filter(message__icontains="with final outcome").count(), 1)

    def test_stage_eight_full_source_recovery_still_needs_no_manual_decision(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)
        self.record_source_stock_found(verification)

        verification.refresh_from_db()
        verification.reconciliation.refresh_from_db()
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED)
        self.assertEqual(verification.reconciliation.status, TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertFalse(
            TransferDiscrepancyManualReconciliationDecision.objects.filter(reconciliation=verification.reconciliation).exists()
        )

    def test_transit_investigation_created_once_on_transit_acknowledgement(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.begin_source_review(review)
        self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
            operation_id="transit-create-review",
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.assertFalse(TransferDiscrepancyTransitInvestigation.objects.filter(reconciliation=reconciliation).exists())

        first = self.acknowledge_reconciliation(reconciliation)
        second = self.acknowledge_reconciliation(reconciliation)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancyTransitInvestigation.objects.filter(reconciliation=reconciliation).count(), 1)
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        self.assertEqual(investigation.status, TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION)
        self.assertEqual(investigation.finding, "")
        self.assertEqual(
            AuditLog.objects.filter(
                entity_name="TransferDiscrepancyTransitInvestigation",
                message__icontains="was created",
            ).count(),
            1,
        )

    def test_source_and_manual_routes_do_not_create_transit_investigation(self):
        source_verification = self.create_source_stock_verification()
        self.assertFalse(
            TransferDiscrepancyTransitInvestigation.objects.filter(reconciliation=source_verification.reconciliation).exists()
        )

    def test_begin_transit_investigation_is_idempotent_and_inventory_neutral(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        movement_count = StockMovement.objects.count()

        first = self.begin_transit_investigation(investigation)
        second = self.begin_transit_investigation(investigation)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        investigation.refresh_from_db()
        self.assertEqual(investigation.status, TransferDiscrepancyTransitInvestigation.Status.INVESTIGATING)
        self.assertEqual(investigation.started_by_worker_code, "WORKER-1")
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(AuditLog.objects.filter(message__icontains="began transit investigation").count(), 1)

    def test_complete_transit_investigation_requires_manual_action_without_inventory_mutation(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        self.begin_transit_investigation(investigation)
        source_before = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        source_quantity_before = source_before.quantity_on_hand if source_before else Decimal("0")
        destination_before = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
        ).quantity_on_hand
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
        movement_count = StockMovement.objects.count()

        response = self.complete_transit_investigation(investigation)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        investigation.refresh_from_db()
        reconciliation.refresh_from_db()
        self.assertEqual(investigation.status, TransferDiscrepancyTransitInvestigation.Status.COMPLETED)
        self.assertEqual(investigation.finding, TransferDiscrepancyTransitInvestigation.Finding.TRANSIT_IRREGULARITY_FOUND)
        self.assertEqual(reconciliation.route, TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION)
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED)
        source_after = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        self.assertEqual(source_after.quantity_on_hand if source_after else Decimal("0"), source_quantity_before)
        self.assertEqual(
            InventoryItem.objects.get(branch=self.destination_branch, location=self.destination_location, product=self.product).quantity_on_hand,
            destination_before,
        )
        self.assertEqual(InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand, unconfirmed_before)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(AuditLog.objects.filter(message__icontains="completed transit investigation").count(), 1)
        self.assertEqual(AuditLog.objects.filter(message__icontains="now requires manual action after transit investigation").count(), 1)

    def test_complete_transit_investigation_supports_all_findings_and_idempotency(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        self.begin_transit_investigation(investigation)

        first = self.complete_transit_investigation(
            investigation,
            finding=TransferDiscrepancyTransitInvestigation.Finding.NO_TRANSIT_IRREGULARITY_IDENTIFIED,
            note="No irregularity was identified in available transfer evidence.",
            operation_id="same-transit",
        )
        second = self.complete_transit_investigation(
            investigation,
            finding=TransferDiscrepancyTransitInvestigation.Finding.INCONCLUSIVE,
            note="Changed note.",
            operation_id="same-transit",
        )
        third = self.complete_transit_investigation(investigation, operation_id="different-transit")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_400_BAD_REQUEST)
        investigation.refresh_from_db()
        self.assertEqual(investigation.finding, TransferDiscrepancyTransitInvestigation.Finding.NO_TRANSIT_IRREGULARITY_IDENTIFIED)
        self.assertEqual(investigation.finding_note, "No irregularity was identified in available transfer evidence.")
        self.assertEqual(AuditLog.objects.filter(message__icontains="completed transit investigation").count(), 1)

    def test_complete_transit_investigation_validates_state_finding_and_note(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)

        pending = self.complete_transit_investigation(investigation, operation_id="bad-pending")
        invalid_finding = self.complete_transit_investigation(investigation, finding="transit_loss_confirmed", operation_id="bad-finding")
        empty_note = self.complete_transit_investigation(investigation, note="", operation_id="bad-note")

        self.assertEqual(pending.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_finding.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(empty_note.status_code, status.HTTP_400_BAD_REQUEST)
        investigation.refresh_from_db()
        reconciliation.refresh_from_db()
        self.assertEqual(investigation.status, TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION)
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.IN_PROGRESS)

    def test_completed_transit_investigation_allows_final_manual_transit_decision(self):
        reconciliation, investigation = self.create_completed_transit_investigation()
        source_before = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        source_quantity_before = source_before.quantity_on_hand if source_before else Decimal("0")
        destination_before = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
        ).quantity_on_hand
        unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
        movement_count = StockMovement.objects.count()

        response = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            note="Transit investigation identified an irregularity and the missing quantity was not recovered.",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reconciliation.refresh_from_db()
        investigation.refresh_from_db()
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation)
        self.assertEqual(decision.outcome, TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED)
        self.assertEqual(decision.get_outcome_display(), "Transit loss confirmed")
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertEqual(reconciliation.route, TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION)
        self.assertEqual(investigation.status, TransferDiscrepancyTransitInvestigation.Status.COMPLETED)
        self.assertEqual(investigation.finding, TransferDiscrepancyTransitInvestigation.Finding.TRANSIT_IRREGULARITY_FOUND)
        source_after = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
        ).first()
        self.assertEqual(source_after.quantity_on_hand if source_after else Decimal("0"), source_quantity_before)
        self.assertEqual(
            InventoryItem.objects.get(branch=self.destination_branch, location=self.destination_location, product=self.product).quantity_on_hand,
            destination_before,
        )
        self.assertEqual(InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand, unconfirmed_before)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(AuditLog.objects.filter(message__icontains="with final outcome: Transit loss confirmed").count(), 1)
        self.assertEqual(response.data["reconciliation"]["manual_decision_required"], False)
        self.assertEqual(response.data["reconciliation"]["manual_decision"]["outcome_label"], "Transit loss confirmed")
        self.assertEqual(response.data["reconciliation"]["status_label"], "Completed")

    def test_transit_final_decision_validates_investigation_state_and_outcome(self):
        in_progress_reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=in_progress_reconciliation)
        self.begin_transit_investigation(investigation)

        in_progress_response = self.complete_manual_reconciliation(
            in_progress_reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            operation_id="bad-transit-in-progress",
        )
        self.assertEqual(in_progress_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(in_progress_response.data["detail"], "Transit reconciliation must require manual action.")

        reconciliation, completed_investigation = self.create_completed_transit_investigation()
        invalid_outcome = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED,
            operation_id="bad-transit-source-outcome",
        )
        self.assertEqual(invalid_outcome.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_outcome.data["detail"], "This outcome is not allowed for transit investigation.")

        completed_investigation.finding_note = ""
        completed_investigation.save(update_fields=["finding_note", "updated_at"])
        missing_note = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            operation_id="bad-transit-missing-note",
        )
        self.assertEqual(missing_note.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing_note.data["detail"], "Transit investigation finding and note are required before manual completion.")
        self.assertFalse(TransferDiscrepancyManualReconciliationDecision.objects.exists())

    def test_transit_final_decision_supports_allowed_outcomes_without_inventory_mutation(self):
        for index, outcome in enumerate(
            [
                TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
                TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED,
                TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
            ],
            start=1,
        ):
            reconciliation, _investigation = self.create_completed_transit_investigation()
            unconfirmed_before = InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand
            movement_count = StockMovement.objects.count()

            response = self.complete_manual_reconciliation(
                reconciliation,
                outcome=outcome,
                note=f"Final transit decision note {index}.",
                operation_id=f"allowed-transit-outcome-{index}",
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(
                TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation).outcome,
                outcome,
            )
            self.assertEqual(InventoryItem.objects.get(location=self.unconfirmed_location, product=self.product).quantity_on_hand, unconfirmed_before)
            self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_transit_final_decision_idempotency_does_not_overwrite_or_duplicate_audit(self):
        reconciliation, _investigation = self.create_completed_transit_investigation()

        first = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            note="Original transit final decision.",
            operation_id="same-transit-final",
        )
        second = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
            note="Changed transit final decision.",
            operation_id="same-transit-final",
        )
        third = self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED,
            note="Different retry.",
            operation_id="different-transit-final",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TransferDiscrepancyManualReconciliationDecision.objects.filter(reconciliation=reconciliation).count(), 1)
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation)
        self.assertEqual(decision.outcome, TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED)
        self.assertEqual(decision.decision_note, "Original transit final decision.")
        self.assertEqual(AuditLog.objects.filter(message__icontains="with final outcome: Transit loss confirmed").count(), 1)

    def test_seed_demo_pallet_exists_and_seed_is_idempotent(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        call_command("seed_demo_data", stdout=output)

        pallet = TransferPallet.objects.get(scan_code="PAL-GDA-GDY-001")
        discrepancy_pallet = TransferPallet.objects.get(scan_code="PAL-GDA-GDY-DISC-001")
        self.assertEqual(pallet.status, TransferPallet.Status.IN_TRANSIT)
        self.assertEqual(discrepancy_pallet.status, TransferPallet.Status.IN_TRANSIT)
        self.assertEqual(pallet.items.count(), 2)
        self.assertEqual(discrepancy_pallet.items.count(), 2)
        self.assertFalse(PalletReceivingSession.objects.filter(pallet=pallet).exists())
        self.assertFalse(TransferDiscrepancy.objects.filter(pallet__in=[pallet, discrepancy_pallet]).exists())

    def test_seed_after_demo_report_posting_resets_unconfirmed_stock(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        start = self.client.post(
            "/api/scanner/receiving/start/",
            {"pallet_code": "PAL-GDA-GDY-DISC-001", "worker_code": "DEMO"},
            format="json",
        )
        session_id = start.data["receiving_session"]["id"]
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "FILTR-001", "quantity": "4"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "OLEJ-001", "quantity": "2"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        close = self.client.post(
            "/api/scanner/receiving/close/",
            {"receiving_session_id": session_id},
            format="json",
        )
        discrepancy_id = close.data["receiving_session"]["discrepancy"]["id"]
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/print-report/",
            {"printer_code": "ZEBRA-01", "worker_code": "DEMO"},
            format="json",
        )
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/recover-item/",
            {
                "product_code": "FILTR-001",
                "destination_location_code": "A-03-01",
                "quantity": "1",
                "worker_code": "DEMO",
                "client_operation_id": "seed-recovery-test",
            },
            format="json",
        )
        self.assertTrue(InventoryItem.objects.filter(location__code="A-03-01", quantity_on_hand__gt=0).exists())

        call_command("seed_demo_data", stdout=output)

        self.assertFalse(InventoryItem.objects.filter(location__code="UNCONFIRMED", quantity_on_hand__gt=0).exists())
        self.assertFalse(
            InventoryItem.objects.filter(
                location__code="A-03-01",
                location__branch__code="GDY",
                quantity_on_hand__gt=0,
            ).exists()
        )

    def test_seed_after_demo_shortage_confirmation_resets_stage_five_state(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        start = self.client.post(
            "/api/scanner/receiving/start/",
            {"pallet_code": "PAL-GDA-GDY-DISC-001", "worker_code": "DEMO"},
            format="json",
        )
        session_id = start.data["receiving_session"]["id"]
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "FILTR-001", "quantity": "4"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "OLEJ-001", "quantity": "2"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        close = self.client.post(
            "/api/scanner/receiving/close/",
            {"receiving_session_id": session_id},
            format="json",
        )
        discrepancy_id = close.data["receiving_session"]["discrepancy"]["id"]
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/print-report/",
            {"printer_code": "ZEBRA-01", "worker_code": "DEMO"},
            format="json",
        )
        confirm = self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/confirm-shortage/",
            {
                "product_code": "FILTR-001",
                "quantity": "1",
                "worker_code": "DEMO",
                "client_operation_id": "seed-shortage-test",
            },
            format="json",
        )
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        self.assertTrue(TransferDiscrepancyShortageConfirmation.objects.exists())
        self.assertTrue(StockMovement.objects.filter(movement_type=StockMovement.MovementType.DISCREPANCY_SHORTAGE).exists())

        call_command("seed_demo_data", stdout=output)

        self.assertFalse(TransferDiscrepancyShortageConfirmation.objects.exists())
        self.assertFalse(TransferDiscrepancySourceReview.objects.exists())
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.DISCREPANCY_SHORTAGE).exists())
        self.assertFalse(InventoryItem.objects.filter(location__code="UNCONFIRMED", quantity_on_hand__gt=0).exists())

    def test_seed_after_completed_source_review_resets_stage_six_state(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        start = self.client.post(
            "/api/scanner/receiving/start/",
            {"pallet_code": "PAL-GDA-GDY-DISC-001", "worker_code": "DEMO"},
            format="json",
        )
        session_id = start.data["receiving_session"]["id"]
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "FILTR-001", "quantity": "4"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "OLEJ-001", "quantity": "2"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        close = self.client.post("/api/scanner/receiving/close/", {"receiving_session_id": session_id}, format="json")
        discrepancy_id = close.data["receiving_session"]["discrepancy"]["id"]
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/print-report/",
            {"printer_code": "ZEBRA-01", "worker_code": "DEMO"},
            format="json",
        )
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/confirm-shortage/",
            {
                "product_code": "FILTR-001",
                "quantity": "1",
                "worker_code": "DEMO",
                "client_operation_id": "seed-source-review-shortage",
            },
            format="json",
        )
        review = TransferDiscrepancySourceReview.objects.get()
        self.begin_source_review(review, worker_code="DEMO")
        self.complete_source_review(review, operation_id="seed-source-review-complete")
        self.assertEqual(TransferDiscrepancySourceReview.objects.count(), 1)

        call_command("seed_demo_data", stdout=output)

        self.assertFalse(TransferDiscrepancySourceReview.objects.exists())
        self.assertFalse(TransferDiscrepancyReconciliation.objects.exists())
        self.assertFalse(TransferDiscrepancy.objects.filter(pallet__scan_code="PAL-GDA-GDY-DISC-001").exists())

    def test_seed_after_acknowledged_reconciliation_resets_stage_seven_state(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        start = self.client.post(
            "/api/scanner/receiving/start/",
            {"pallet_code": "PAL-GDA-GDY-DISC-001", "worker_code": "DEMO"},
            format="json",
        )
        session_id = start.data["receiving_session"]["id"]
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "FILTR-001", "quantity": "4"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "OLEJ-001", "quantity": "2"},
            format="json",
        )
        self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "A-01-01"},
            format="json",
        )
        close = self.client.post("/api/scanner/receiving/close/", {"receiving_session_id": session_id}, format="json")
        discrepancy_id = close.data["receiving_session"]["discrepancy"]["id"]
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/print-report/",
            {"printer_code": "ZEBRA-01", "worker_code": "DEMO"},
            format="json",
        )
        self.client.post(
            f"/api/transfer-discrepancies/{discrepancy_id}/confirm-shortage/",
            {
                "product_code": "FILTR-001",
                "quantity": "1",
                "worker_code": "DEMO",
                "client_operation_id": "seed-reconciliation-shortage",
            },
            format="json",
        )
        review = TransferDiscrepancySourceReview.objects.get()
        self.begin_source_review(review, worker_code="DEMO")
        self.complete_source_review(review, operation_id="seed-reconciliation-review")
        reconciliation = TransferDiscrepancyReconciliation.objects.get()
        self.acknowledge_reconciliation(reconciliation, worker_code="DEMO")
        self.assertEqual(TransferDiscrepancyReconciliation.objects.count(), 1)

        call_command("seed_demo_data", stdout=output)

        self.assertFalse(TransferDiscrepancyReconciliation.objects.exists())
        self.assertFalse(TransferDiscrepancySourceReview.objects.exists())


class ScannerLookupAndQuickTransferTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="LKP", name="Lookup Branch", city="Gdynia", country="Poland")
        self.source_location = Location.objects.create(
            branch=self.branch,
            code="L-01-01",
            name="Source shelf",
            location_type=Location.LocationType.STORAGE,
        )
        self.target_location = Location.objects.create(
            branch=self.branch,
            code="L-02-01",
            name="Target shelf",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(
            sku="LOOK-001",
            name="Lookup Product",
            barcode="770000000001",
            unit_of_measure="pcs",
        )
        self.source_item = InventoryItem.objects.create(
            branch=self.branch,
            location=self.source_location,
            product=self.product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )

    def transfer(self, **overrides):
        payload = {
            "source_location_code": "L-01-01",
            "product_code": "LOOK-001",
            "target_location_code": "L-02-01",
            "quantity": "1",
        }
        payload.update(overrides)
        return self.client.post("/api/scanner/quick-transfer/", payload, format="json")

    def test_product_lookup_returns_product_and_inventory_positions(self):
        response = self.client.get("/api/scanner/products/lookup/", {"code": "770000000001"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["product"]["sku"], "LOOK-001")
        self.assertEqual(len(response.data["inventory_positions"]), 1)
        self.assertEqual(response.data["inventory_positions"][0]["location_code"], "L-01-01")

    def test_product_lookup_not_found_returns_404(self):
        response = self.client.get("/api/scanner/products/lookup/", {"code": "MISSING"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_location_contents_returns_inventory_items(self):
        response = self.client.get("/api/scanner/locations/contents/", {"code": "L-01-01"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["location"]["code"], "L-01-01")
        self.assertEqual(response.data["inventory_items"][0]["product_sku"], "LOOK-001")

    def test_quick_transfer_moves_quantity_and_creates_history(self):
        response = self.transfer(quantity="2")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.source_item.refresh_from_db()
        target_item = InventoryItem.objects.get(location=self.target_location, product=self.product)
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("3.000"))
        self.assertEqual(target_item.quantity_on_hand, Decimal("2.000"))
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.TRANSFER,
                source_location=self.source_location,
                destination_location=self.target_location,
                quantity=Decimal("2.000"),
            ).exists()
        )
        self.assertTrue(AuditLog.objects.filter(message__icontains="Scanner quick transfer").exists())

    def test_quick_transfer_rejects_same_source_and_target(self):
        response = self.transfer(target_location_code="L-01-01")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("same", response.data["detail"])

    def test_quick_transfer_rejects_insufficient_quantity(self):
        response = self.transfer(quantity="10")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient", response.data["detail"])


class ScannerPickingJobWorkflowTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="JOB", name="Job Branch", city="Gdynia", country="Poland")
        self.location = Location.objects.create(
            branch=self.branch,
            code="J-01-01",
            name="J-01-01",
            location_type=Location.LocationType.PICKING,
        )
        self.other_location = Location.objects.create(
            branch=self.branch,
            code="J-99-01",
            name="J-99-01",
            location_type=Location.LocationType.PICKING,
        )
        self.product_a = Product.objects.create(
            sku="JOB-A",
            name="Job Product A",
            barcode="550000000001",
            unit_of_measure="pcs",
        )
        self.product_b = Product.objects.create(
            sku="JOB-B",
            name="Job Product B",
            barcode="550000000002",
            unit_of_measure="pcs",
        )
        self.product_a_inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product_a,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        self.other_product_a_inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.other_location,
            product=self.product_a,
            quantity_on_hand=Decimal("7"),
            quantity_reserved=Decimal("0"),
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product_b,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        self.route_1 = DeliveryRoute.objects.create(branch=self.branch, code="JOB-R1", name="Job Route 1")
        self.route_2 = DeliveryRoute.objects.create(branch=self.branch, code="JOB-R2", name="Job Route 2")
        self.run_1 = self.create_run(self.route_1, 1)
        self.run_2 = self.create_run(self.route_2, 1)
        self.order_1 = self.create_order("JOB-ORDER-1", self.run_1, self.product_a)
        self.order_2 = self.create_order("JOB-ORDER-2", self.run_2, self.product_b)
        self.task_1 = self.create_task(self.order_1.lines.first())
        self.task_2 = self.create_task(self.order_2.lines.first())

    def create_run(self, route, run_number):
        return RouteRun.objects.create(
            route=route,
            service_date=timezone.localdate(),
            run_number=run_number,
            order_cutoff_time=time(8, 50),
            sync_time=time(8, 55),
            departure_time=time(12, 0),
            status=RouteRun.Status.OPEN,
        )

    def create_order(self, reference, route_run, product):
        order = Order.objects.create(
            branch=self.branch,
            route_run=route_run,
            external_reference=reference,
            customer_name="Job Customer",
            status=Order.Status.IMPORTED,
        )
        OrderLine.objects.create(
            order=order,
            product=product,
            line_number=1,
            quantity_ordered=Decimal("1"),
            quantity_picked=Decimal("0"),
        )
        return order

    def create_task(self, order_line):
        return PickingTask.objects.create(
            branch=self.branch,
            order_line=order_line,
            source_location=self.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("1"),
            quantity_picked=Decimal("0"),
            quantity_prepared=Decimal("0"),
        )

    def create_jobs(self, route_run_ids=None, mode="merged"):
        return self.client.post(
            "/api/scanner/proformas/create-jobs/",
            {
                "route_run_ids": route_run_ids or [self.run_1.id, self.run_2.id],
                "mode": mode,
                "worker_code": "DEMO",
            },
            format="json",
        )

    def start_job(self, job, cart_code="WOZEK-01", worker_code="DEMO"):
        return self.client.post(
            f"/api/scanner/tasks/{job.id}/start/",
            {"cart_code": cart_code, "worker_code": worker_code},
            format="json",
        )

    def confirm_location(self, cart_work_session_id, location_code="J-01-01"):
        return self.client.post(
            "/api/scanner/picking/confirm-location/",
            {"cart_work_session_id": cart_work_session_id, "location_code": location_code},
            format="json",
        )

    def set_task_1_quantity(self, quantity):
        quantity = Decimal(str(quantity))
        order_line = self.task_1.order_line
        order_line.quantity_ordered = quantity
        order_line.quantity_picked = Decimal("0")
        order_line.save(update_fields=["quantity_ordered", "quantity_picked", "updated_at"])
        self.task_1.quantity_to_pick = quantity
        self.task_1.quantity_picked = Decimal("0")
        self.task_1.quantity_prepared = Decimal("0")
        self.task_1.status = PickingTask.Status.OPEN
        self.task_1.save(update_fields=["quantity_to_pick", "quantity_picked", "quantity_prepared", "status", "updated_at"])

    def test_merged_mode_creates_one_picking_job(self):
        response = self.create_jobs(mode="merged")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PickingJob.objects.count(), 1)
        job = PickingJob.objects.get()
        self.assertEqual(job.mode, PickingJob.Mode.MERGED)
        self.assertEqual(job.tasks.count(), 2)

    def test_separate_mode_creates_one_job_per_route(self):
        response = self.create_jobs(mode="separate")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PickingJob.objects.count(), 2)
        self.assertEqual(PickingJob.objects.first().tasks.count(), 1)

    def test_same_picking_task_cannot_belong_to_two_active_jobs(self):
        first_response = self.create_jobs(route_run_ids=[self.run_1.id])
        second_response = self.create_jobs(route_run_ids=[self.run_1.id])

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_409_CONFLICT)

    def test_available_counters_decrease_after_job_creation(self):
        before = self.client.get("/api/scanner/proformas/", {"branch": self.branch.id})
        self.create_jobs(route_run_ids=[self.run_1.id])
        after = self.client.get("/api/scanner/proformas/", {"branch": self.branch.id})

        self.assertEqual(before.status_code, status.HTTP_200_OK)
        self.assertEqual(after.status_code, status.HTTP_200_OK)
        before_run = next(row for row in before.data["results"] if row["id"] == self.run_1.id)
        after_run = next(row for row in after.data["results"] if row["id"] == self.run_1.id)
        self.assertEqual(before_run["akt"], 1)
        self.assertEqual(after_run["akt"], 0)

    def test_invalid_closed_route_cannot_create_picking_job(self):
        self.run_1.status = RouteRun.Status.CLOSED
        self.run_1.save(update_fields=["status", "updated_at"])

        response = self.create_jobs(route_run_ids=[self.run_1.id])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_available_job_can_be_assigned_to_free_cart(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()

        response = self.start_job(job)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, PickingJob.Status.IN_PROGRESS)
        self.assertTrue(CartWorkSession.objects.filter(picking_job=job, cart__code="WOZEK-01").exists())
        self.assertEqual(response.data["session"]["cart_work_session"], response.data["cart_work_session"]["id"])
        self.assertEqual(response.data["session"]["picking_job"], job.id)

    def test_started_job_remains_visible_in_tasks_with_assigned_cart(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        self.start_job(job, cart_code="WOZEK-01")

        response = self.client.get("/api/scanner/tasks/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = next(item for item in response.data["results"] if item["id"] == job.id)
        self.assertEqual(row["status"], PickingJob.Status.IN_PROGRESS)
        self.assertEqual(row["assigned_cart_code"], "WOZEK-01")

    def test_active_cart_work_can_be_recovered_without_creating_second_session(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job, cart_code="WOZEK-01")

        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["cart_work_session"]["picking_job"]["id"], job.id)
        self.assertEqual(response.data["cart_work_session"]["cart_code"], "WOZEK-01")
        self.assertEqual(CartWorkSession.objects.filter(picking_job=job).count(), 1)

    def test_cart_work_returns_current_location_instruction(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"session_id": start.data["session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["state"], "waiting_for_location")
        self.assertEqual(response.data["current_instruction"]["location"]["code"], "J-01-01")
        self.assertEqual(response.data["current_instruction"]["product"]["sku"], "JOB-A")

    def test_correct_location_confirmation_succeeds(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.post(
            "/api/scanner/picking/confirm-location/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"], "location_code": "J-01-01"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["state"], "waiting_for_product")
        self.assertEqual(response.data["confirmed_location_code"], "J-01-01")
        self.assertEqual(response.data["current_instruction"]["product"]["sku"], "JOB-A")

    def test_wrong_location_confirmation_fails(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.post(
            "/api/scanner/picking/confirm-location/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"], "location_code": "J-99-01"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Wrong location", response.data["detail"])

    def test_product_cannot_be_picked_without_location_confirmation(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": start.data["cart_work_session"]["id"],
                "product_code": "JOB-A",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("location", response.data["detail"])

    def test_wrong_product_is_rejected_after_location_confirmation(self):
        self.create_jobs()
        job = PickingJob.objects.get()
        start = self.start_job(job)
        self.confirm_location(start.data["cart_work_session"]["id"])

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": start.data["cart_work_session"]["id"],
                "location_code": "J-01-01",
                "product_code": "JOB-B",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Wrong product", response.data["detail"])

    def test_picking_accepts_whole_piece_quantity(self):
        self.set_task_1_quantity("3")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task_1.refresh_from_db()
        self.product_a_inventory.refresh_from_db()
        self.assertEqual(self.task_1.quantity_picked, Decimal("2.000"))
        self.assertEqual(self.product_a_inventory.quantity_on_hand, Decimal("3.000"))
        self.assertEqual(response.data["current_instruction"]["remaining_quantity"], "1.000")

    def test_picking_rejects_decimal_quantity(self):
        self.set_task_1_quantity("3")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2.000",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("whole number", response.data["detail"])
        cart_work_session = CartWorkSession.objects.get(pk=cart_work_session_id)
        self.assertEqual(cart_work_session.confirmed_location_id, self.location.id)

    def test_picking_rejects_zero_quantity(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "0",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("at least 1", response.data["detail"])

    def test_picking_rejects_quantity_above_remaining_work(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("remaining", response.data["detail"])

    def test_picking_rejects_quantity_above_confirmed_location_stock(self):
        self.set_task_1_quantity("3")
        self.product_a_inventory.quantity_on_hand = Decimal("1")
        self.product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirmed location", response.data["detail"])

    def test_picking_accepts_product_barcode(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "550000000001",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_same_job_cannot_be_assigned_to_two_carts(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        self.start_job(job, cart_code="WOZEK-01")

        response = self.start_job(job, cart_code="WOZEK-02")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(CartWorkSession.objects.filter(picking_job=job).count(), 1)

    def test_same_cart_cannot_start_two_active_jobs(self):
        self.create_jobs(mode="separate")
        jobs = list(PickingJob.objects.order_by("id"))
        self.start_job(jobs[0], cart_code="WOZEK-01")

        response = self.start_job(jobs[1], cart_code="WOZEK-01")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertNotIn("Join", response.data["detail"])
        self.assertEqual(CartWorkSession.objects.filter(cart__code="WOZEK-01").count(), 1)

    def test_control_worker_can_open_cart_picked_by_another_worker(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        self.start_job(job, cart_code="WOZEK-01", worker_code="DEMO")
        cart_work_session = CartWorkSession.objects.get(picking_job=job)
        self.confirm_location(cart_work_session.id)
        self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session.id,
                "location_code": "J-01-01",
                "product_code": "JOB-A",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )

        response = self.client.get("/api/scanner/control/cart/", {"cart_code": "WOZEK-01"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["session"]["cart_code"], "WOZEK-01")
        self.assertEqual(len(response.data["items"]), 1)

    def test_picking_updates_shared_progress_and_stock_once(self):
        self.create_jobs()
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "location_code": "J-01-01",
                "product_code": "JOB-A",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task_1.refresh_from_db()
        self.other_product_a_inventory.refresh_from_db()
        self.assertEqual(self.task_1.quantity_picked, Decimal("1.000"))
        self.assertEqual(self.other_product_a_inventory.quantity_on_hand, Decimal("7.000"))
        self.assertEqual(response.data["current_instruction"]["location"]["code"], "J-01-01")
        self.assertEqual(response.data["current_instruction"]["product"]["sku"], "JOB-B")
        self.assertTrue(CartPickedItem.objects.filter(cart_work_session_id=cart_work_session_id).exists())

    def test_final_quantity_cannot_be_picked_twice(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        payload = {
            "cart_work_session_id": cart_work_session_id,
            "location_code": "J-01-01",
            "product_code": "JOB-A",
            "quantity": "1",
            "worker_code": "DEMO",
        }

        first = self.client.post("/api/scanner/picking/pick/", payload, format="json")
        second = self.client.post("/api/scanner/picking/pick/", {**payload, "worker_code": "WORKER-02"}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.task_1.refresh_from_db()
        self.assertEqual(self.task_1.quantity_picked, Decimal("1.000"))

    def test_prepared_quantity_cannot_exceed_picked_quantity(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        session_id = start.data["session"]["id"]
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "location_code": "J-01-01",
                "product_code": "JOB-A",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )
        self.client.post(
            "/api/scanner/control/print-label/",
            {"session_id": session_id, "order_reference": "JOB-ORDER-1", "printer_code": "ZEBRA-01"},
            format="json",
        )

        first = self.client.post(
            "/api/scanner/picking/prepare/",
            {"session_id": session_id, "order_reference": "JOB-ORDER-1", "product_code": "JOB-A", "quantity": "1"},
            format="json",
        )
        second = self.client.post(
            "/api/scanner/picking/prepare/",
            {"session_id": session_id, "order_reference": "JOB-ORDER-1", "product_code": "JOB-A", "quantity": "1"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.task_1.refresh_from_db()
        self.assertEqual(self.task_1.quantity_prepared, Decimal("1.000"))

    def test_control_sees_items_picked_by_all_workers_and_finish_releases_cart(self):
        self.create_jobs()
        job = PickingJob.objects.get()
        start = self.start_job(job)
        session_id = start.data["session"]["id"]
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "location_code": "J-01-01",
                "product_code": "JOB-A",
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )
        self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "location_code": "J-01-01",
                "product_code": "JOB-B",
                "quantity": "1",
                "worker_code": "WORKER-02",
            },
            format="json",
        )

        items = self.client.get("/api/scanner/control/cart-items/", {"session_id": session_id})

        self.assertEqual(items.status_code, status.HTTP_200_OK)
        self.assertEqual(len(items.data["items"]), 2)

        for product in ["JOB-A", "JOB-B"]:
            order = "JOB-ORDER-1" if product == "JOB-A" else "JOB-ORDER-2"
            self.client.post(
                "/api/scanner/control/print-label/",
                {"session_id": session_id, "order_reference": order, "printer_code": "ZEBRA-01"},
                format="json",
            )
            self.client.post(
                "/api/scanner/picking/prepare/",
                {"session_id": session_id, "order_reference": order, "product_code": product, "quantity": "1"},
                format="json",
            )

        finish = self.client.post("/api/scanner/control/finish/", {"session_id": session_id}, format="json")

        self.assertEqual(finish.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        cart_work = CartWorkSession.objects.get(id=cart_work_session_id)
        self.assertEqual(job.status, PickingJob.Status.COMPLETED)
        self.assertEqual(cart_work.status, CartWorkSession.Status.COMPLETED)
        self.assertEqual(cart_work.cart.status, ScannerCart.Status.AVAILABLE)


class SeedDemoDataCommandTests(APITestCase):
    def run_seed(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        return output.getvalue()

    def available_route_ids(self):
        response = self.client.get("/api/scanner/proformas/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [row["id"] for row in response.data["results"] if row["is_selectable"]]

    def start_demo_job(self):
        route_ids = self.available_route_ids()[:2]
        response = self.client.post(
            "/api/scanner/proformas/create-jobs/",
            {"route_run_ids": route_ids, "mode": "merged", "worker_code": "DEMO"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        job_id = response.data["jobs"][0]["id"]
        start_response = self.client.post(
            f"/api/scanner/tasks/{job_id}/start/",
            {"cart_code": "WOZEK-01", "worker_code": "DEMO"},
            format="json",
        )
        self.assertEqual(start_response.status_code, status.HTTP_200_OK)
        return start_response.data

    def test_seed_can_run_on_clean_database_and_again(self):
        first_output = self.run_seed()
        second_output = self.run_seed()

        self.assertIn("Demo warehouse data seeded successfully.", first_output)
        self.assertIn("Demo warehouse data seeded successfully.", second_output)
        self.assertGreaterEqual(len(self.available_route_ids()), 4)
        self.assertFalse(CartWorkSession.objects.exists())
        self.assertFalse(PickingJob.objects.exists())
        self.assertTrue(ScannerCart.objects.filter(code="WOZEK-01", status=ScannerCart.Status.AVAILABLE).exists())

    def test_seed_creates_customer_label_reuse_scenario(self):
        self.run_seed()

        order = Order.objects.get(external_reference="AX-ORDER-LABEL-TEST")
        lines = list(order.lines.select_related("product").order_by("line_number"))

        self.assertEqual(order.customer_name, "Demo Client Label Test")
        self.assertEqual(order.route_run.status, RouteRun.Status.OPEN)
        self.assertEqual(len(lines), 2)
        self.assertEqual({line.product.sku for line in lines}, {"FILTR-001", "OLEJ-001"})
        self.assertEqual([line.quantity_ordered for line in lines], [Decimal("3.000"), Decimal("2.000")])
        self.assertTrue(all(line.order_id == order.id for line in lines))
        self.assertEqual(PickingTask.objects.filter(order_line__order=order).count(), 2)
        self.assertTrue(
            InventoryItem.objects.filter(
                product__sku="FILTR-001",
                location__code="A-02-01",
                quantity_on_hand__gte=Decimal("3"),
            ).exists()
        )
        self.assertTrue(
            InventoryItem.objects.filter(
                product__sku="OLEJ-001",
                location__code="A-01-02",
                quantity_on_hand__gte=Decimal("2"),
            ).exists()
        )

        self.run_seed()
        self.assertEqual(Order.objects.filter(external_reference="AX-ORDER-LABEL-TEST").count(), 1)
        self.assertEqual(OrderLine.objects.filter(order__external_reference="AX-ORDER-LABEL-TEST").count(), 2)

    def test_seed_cleans_active_cart_work_and_stale_jobs(self):
        self.run_seed()
        self.start_demo_job()

        self.assertTrue(CartWorkSession.objects.filter(status=CartWorkSession.Status.ACTIVE).exists())
        self.assertTrue(PickingJob.objects.exists())

        self.run_seed()

        self.assertFalse(CartWorkSession.objects.exists())
        self.assertFalse(CartPickedItem.objects.exists())
        self.assertFalse(PickingJob.objects.exists())
        self.assertFalse(ScannerCustomerLabel.objects.exists())
        self.assertTrue(ScannerCart.objects.filter(code="WOZEK-01", status=ScannerCart.Status.AVAILABLE).exists())
        self.assertGreaterEqual(len(self.available_route_ids()), 4)

    def test_seed_cleans_partial_picking_state(self):
        self.run_seed()
        start_data = self.start_demo_job()
        cart_work_session_id = start_data["cart_work_session"]["id"]
        session_id = start_data["session"]["id"]
        current = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": cart_work_session_id})
        instruction = current.data["current_instruction"]
        self.client.post(
            "/api/scanner/picking/confirm-location/",
            {"cart_work_session_id": cart_work_session_id, "location_code": instruction["location"]["code"]},
            format="json",
        )
        pick_response = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "location_code": instruction["location"]["code"],
                "product_code": instruction["product"]["sku"],
                "quantity": "1",
                "worker_code": "DEMO",
            },
            format="json",
        )
        self.assertEqual(pick_response.status_code, status.HTTP_200_OK)
        self.client.post(
            "/api/scanner/control/print-label/",
            {
                "session_id": session_id,
                "order_reference": CartPickedItem.objects.first().picking_task.order_line.order.external_reference,
                "printer_code": "ZEBRA-01",
            },
            format="json",
        )

        self.assertTrue(CartPickedItem.objects.exists())
        self.assertTrue(ScannerCustomerLabel.objects.exists())

        self.run_seed()

        self.assertFalse(CartPickedItem.objects.exists())
        self.assertFalse(ScannerCustomerLabel.objects.exists())
        self.assertFalse(CartWorkSession.objects.exists())
        self.assertFalse(PickingJob.objects.exists())
        demo_task = PickingTask.objects.get(order_line__order__external_reference="AX-ORDER-0001", order_line__line_number=1)
        self.assertEqual(demo_task.quantity_picked, Decimal("0.000"))
        self.assertEqual(demo_task.quantity_prepared, Decimal("0.000"))


class RouteRunLifecycleTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="LFC", name="Lifecycle Branch", city="Gdynia", country="Poland")
        self.location = Location.objects.create(
            branch=self.branch,
            code="R-01-01",
            name="R-01-01",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(
            sku="LIFE-001",
            name="Lifecycle Product",
            barcode="660000000001",
            unit_of_measure="pcs",
        )
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="ROUTE-L", name="Lifecycle Route")
        self.route_run = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 50),
            sync_time=time(8, 51),
            departure_time=(timezone.localtime() + timezone.timedelta(hours=2)).time(),
            status=RouteRun.Status.OPEN,
        )
        self.order = Order.objects.create(
            branch=self.branch,
            route_run=self.route_run,
            external_reference="LIFE-ORDER-001",
            customer_name="Lifecycle Customer",
            status=Order.Status.IMPORTED,
        )
        self.order_line = OrderLine.objects.create(
            order=self.order,
            product=self.product,
            line_number=1,
            quantity_ordered=Decimal("1"),
            quantity_picked=Decimal("0"),
        )
        self.task = PickingTask.objects.create(
            branch=self.branch,
            order_line=self.order_line,
            source_location=self.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("1"),
            quantity_picked=Decimal("0"),
            quantity_prepared=Decimal("0"),
        )

    def mark_prepared(self):
        self.order_line.quantity_picked = Decimal("1")
        self.order_line.save(update_fields=["quantity_picked", "updated_at"])
        self.task.quantity_picked = Decimal("1")
        self.task.quantity_prepared = Decimal("1")
        self.task.status = PickingTask.Status.COMPLETED
        self.task.save(update_fields=["quantity_picked", "quantity_prepared", "status", "updated_at"])
        return recalculate_route_readiness(self.route_run)

    def print_documents(self):
        return self.client.post(f"/api/route-runs/{self.route_run.id}/print-documents/", {}, format="json")

    def close_route(self):
        return self.client.post(f"/api/route-runs/{self.route_run.id}/close/", {}, format="json")

    def test_route_does_not_become_ready_while_work_is_incomplete(self):
        is_ready = recalculate_route_readiness(self.route_run)

        self.route_run.refresh_from_db()
        self.assertFalse(is_ready)
        self.assertEqual(self.route_run.status, RouteRun.Status.OPEN)
        self.assertIsNone(self.route_run.ready_at)

    def test_route_becomes_ready_when_final_required_work_is_prepared(self):
        is_ready = self.mark_prepared()

        self.route_run.refresh_from_db()
        self.assertTrue(is_ready)
        self.assertEqual(self.route_run.status, RouteRun.Status.READY_TO_CLOSE)
        self.assertIsNotNone(self.route_run.ready_at)
        self.assertTrue(AuditLog.objects.filter(message__icontains="ready to close").exists())

    def test_ready_route_is_on_time_when_before_departure(self):
        future_departure = timezone.localtime() + timezone.timedelta(hours=2)
        self.route_run.service_date = future_departure.date()
        self.route_run.departure_time = future_departure.time()
        self.route_run.save(update_fields=["service_date", "departure_time", "updated_at"])
        self.mark_prepared()

        response = self.client.get(f"/api/route-runs/{self.route_run.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_ready_to_close"])
        self.assertFalse(response.data["is_late"])

    def test_ready_route_is_late_after_departure(self):
        self.route_run.departure_time = (timezone.localtime() - timezone.timedelta(hours=1)).time()
        self.route_run.save(update_fields=["departure_time", "updated_at"])
        self.mark_prepared()

        response = self.client.get(f"/api/route-runs/{self.route_run.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_late"])

    def test_print_documents_rejects_unfinished_route(self):
        response = self.print_documents()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not ready", response.data["detail"])

    def test_print_documents_succeeds_for_ready_route(self):
        self.mark_prepared()

        response = self.print_documents()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.route_run.refresh_from_db()
        self.assertIsNotNone(self.route_run.documents_printed_at)
        self.assertTrue(AuditLog.objects.filter(message__icontains="documents printed").exists())

    def test_close_rejects_unfinished_route(self):
        response = self.close_route()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not ready", response.data["detail"])

    def test_close_rejects_route_without_printed_documents(self):
        self.mark_prepared()

        response = self.close_route()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("printed", response.data["detail"])

    def test_close_succeeds_after_documents_are_printed(self):
        self.mark_prepared()
        self.print_documents()

        response = self.close_route()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.route_run.refresh_from_db()
        self.assertEqual(self.route_run.status, RouteRun.Status.CLOSED)
        self.assertIsNotNone(self.route_run.closed_at)
        self.assertTrue(AuditLog.objects.filter(message__icontains="closed").exists())

    def test_closed_route_is_excluded_from_active_monitor(self):
        self.mark_prepared()
        self.print_documents()
        self.close_route()

        response = self.client.get("/api/route-runs/", {"branch": self.branch.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.route_run.id, ids)

    def test_closed_route_appears_in_route_archive(self):
        self.mark_prepared()
        self.print_documents()
        self.close_route()

        response = self.client.get("/api/route-runs/archive/", {"branch": self.branch.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.route_run.id, ids)

    def test_closed_route_before_departure_is_on_time(self):
        departure_at = timezone.make_aware(
            datetime.combine(self.route_run.service_date, time(12, 0)),
            timezone.get_current_timezone(),
        )
        self.route_run.departure_time = departure_at.time()
        self.route_run.status = RouteRun.Status.CLOSED
        self.route_run.closed_at = departure_at - timezone.timedelta(minutes=5)
        self.route_run.save(update_fields=["departure_time", "status", "closed_at", "updated_at"])

        response = self.client.get(f"/api/route-runs/{self.route_run.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_late"])
        self.assertEqual(response.data["close_result"], "on_time")
        self.assertEqual(route_close_result(self.route_run), "on_time")

    def test_closed_route_after_departure_is_late(self):
        departure_at = timezone.make_aware(
            datetime.combine(self.route_run.service_date, time(12, 0)),
            timezone.get_current_timezone(),
        )
        self.route_run.departure_time = departure_at.time()
        self.route_run.status = RouteRun.Status.CLOSED
        self.route_run.closed_at = departure_at + timezone.timedelta(minutes=5)
        self.route_run.save(update_fields=["departure_time", "status", "closed_at", "updated_at"])

        response = self.client.get(f"/api/route-runs/{self.route_run.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_late"])
        self.assertEqual(response.data["close_result"], "late")
        self.assertEqual(route_close_result(self.route_run), "late")

    def test_legacy_closed_route_without_closed_at_has_unknown_result(self):
        self.route_run.departure_time = time(12, 0)
        self.route_run.status = RouteRun.Status.CLOSED
        self.route_run.closed_at = None
        self.route_run.save(update_fields=["departure_time", "status", "closed_at", "updated_at"])

        response = self.client.get(f"/api/route-runs/{self.route_run.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_late"])
        self.assertEqual(response.data["close_result"], "unknown")
        self.assertEqual(route_close_result(self.route_run), "unknown")
