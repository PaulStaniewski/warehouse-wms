from datetime import time
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from operations.models import AuditLog, DeliveryRoute, Order, OrderLine, PickingTask, RouteRun, StockMovement
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

    def scan(self, route_run_id=None, code="SCAN-001"):
        return self.client.post(
            "/api/scanner/picking/scan/",
            {
                "route_run_id": route_run_id if route_run_id is not None else self.route_run.id,
                "code": code,
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

    def test_scan_by_barcode_completes_after_required_quantity(self):
        first_response = self.scan(code="880000000001")
        second_response = self.scan(code="880000000001")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, PickingTask.Status.COMPLETED)
        self.assertEqual(self.task.quantity_picked, Decimal("2.000"))

    def test_scan_by_order_reference_matches_task(self):
        response = self.scan(code="SCAN-ORDER-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.quantity_picked, Decimal("1.000"))

    def test_completed_matching_task_returns_400(self):
        self.scan()
        self.scan()

        response = self.scan()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already completed", response.data["detail"])

    def test_scan_creates_audit_log(self):
        self.scan()

        self.assertTrue(
            AuditLog.objects.filter(
                entity_name="PickingTask",
                entity_id=str(self.task.id),
                message__icontains="Scanner picked",
            ).exists()
        )


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
