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
    Order,
    OrderLine,
    PickingJob,
    PickingTask,
    RouteRun,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    StockMovement,
)
from operations.services import recalculate_route_readiness, route_close_result
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
        self.assertTrue(ScannerCustomerLabel.objects.filter(session=self.session, order=self.order).exists())
        self.assertTrue(AuditLog.objects.filter(message__icontains="Customer label printed").exists())

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

    def test_same_cart_cannot_start_two_active_jobs(self):
        self.create_jobs(mode="separate")
        jobs = list(PickingJob.objects.order_by("id"))
        self.start_job(jobs[0], cart_code="WOZEK-01")

        response = self.start_job(jobs[1], cart_code="WOZEK-01")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

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
