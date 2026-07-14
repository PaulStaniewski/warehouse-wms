from datetime import datetime, time
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase


User = get_user_model()

from accounts.models import UserBranchMembership
from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkParticipant,
    CartWorkSession,
    DeliveryRoute,
    InterBranchTransfer,
    Order,
    OrderLine,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingShortage,
    PickingShortageAllocation,
    PickingTask,
    PickingTaskClaim,
    PickingTaskReallocation,
    ReplenishmentRequest,
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
    TransferPalletArrival,
    TransferPalletItem,
)
from operations.services import recalculate_route_readiness, reconciliation_route_for_finding, route_close_result
from warehouse.models import Branch, InventoryItem, Location, Product


class BranchMembershipAuthorizationTests(APITestCase):
    def setUp(self):
        self.source_branch = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.destination_branch = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.unrelated_branch = Branch.objects.create(code="WAW", name="Warsaw", city="Warsaw", country="Poland")
        self.destination_location = Location.objects.create(
            branch=self.destination_branch,
            code="A-01-01",
            name="A-01-01",
            location_type=Location.LocationType.STORAGE,
        )
        self.unrelated_location = Location.objects.create(
            branch=self.unrelated_branch,
            code="W-01-01",
            name="W-01-01",
            location_type=Location.LocationType.STORAGE,
        )
        User = get_user_model()
        self.gdy_worker = User.objects.create_user(username="GDY_WORKER", password="demo12345")
        self.gdy_leader = User.objects.create_user(username="GDY_LEADER", password="demo12345")
        self.waw_worker = User.objects.create_user(username="WAW_WORKER", password="demo12345")
        UserBranchMembership.objects.create(
            user=self.gdy_worker,
            branch=self.destination_branch,
            role=UserBranchMembership.Role.WORKER,
        )
        UserBranchMembership.objects.create(
            user=self.gdy_leader,
            branch=self.destination_branch,
            role=UserBranchMembership.Role.LEADER,
        )
        UserBranchMembership.objects.create(
            user=self.waw_worker,
            branch=self.unrelated_branch,
            role=UserBranchMembership.Role.WORKER,
        )

    def create_discrepancy_with_manual_reconciliation(self):
        transfer = InterBranchTransfer.objects.create(
            reference="IBT-AUTH-001",
            source_branch=self.source_branch,
            destination_branch=self.destination_branch,
            status=InterBranchTransfer.Status.CLOSED_WITH_DISCREPANCY,
        )
        pallet = TransferPallet.objects.create(
            transfer=transfer,
            scan_code="PAL-AUTH-001",
            status=TransferPallet.Status.CLOSED_WITH_DISCREPANCY,
        )
        discrepancy = TransferDiscrepancy.objects.create(
            reference="DISC-AUTH-001",
            pallet=pallet,
            transfer=transfer,
            status=TransferDiscrepancy.Status.CONFIRMED_SHORTAGE,
        )
        source_review = TransferDiscrepancySourceReview.objects.create(
            discrepancy=discrepancy,
            source_branch=self.source_branch,
            status=TransferDiscrepancySourceReview.Status.COMPLETED,
            finding=TransferDiscrepancySourceReview.Finding.INCONCLUSIVE,
            completed_at=timezone.now(),
            completed_by_worker_code="OTHER_USER",
        )
        return TransferDiscrepancyReconciliation.objects.create(
            discrepancy=discrepancy,
            source_review=source_review,
            route=TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION,
            status=TransferDiscrepancyReconciliation.Status.IN_PROGRESS,
            acknowledged_at=timezone.now(),
        )

    def test_me_branch_memberships_returns_current_user_allowed_branches(self):
        self.client.login(username="GDY_WORKER", password="demo12345")

        response = self.client.get("/api/me/branch-memberships/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["branch_code"], "GDY")
        self.assertEqual(response.data[0]["role"], "worker")

    def test_branch_scoped_locations_reject_unrelated_authenticated_branch(self):
        self.client.force_authenticate(self.gdy_worker)

        allowed = self.client.get("/api/locations/", {"branch": "GDY"})
        forbidden = self.client.get("/api/locations/", {"branch": "WAW"})

        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual([row["branch_code"] for row in allowed.data["results"]], ["GDY"])
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

    def test_action_queue_hides_leader_only_final_action_from_worker(self):
        self.create_discrepancy_with_manual_reconciliation()

        self.client.force_authenticate(self.gdy_worker)
        worker_response = self.client.get("/api/transfer-discrepancy-actions/", {"branch": "GDY"})
        self.client.force_authenticate(self.gdy_leader)
        leader_response = self.client.get("/api/transfer-discrepancy-actions/", {"branch": "GDY"})

        self.assertEqual(worker_response.status_code, status.HTTP_200_OK)
        self.assertEqual(leader_response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            any(row["action_type"] == "record_final_reconciliation_outcome" for row in worker_response.data["results"])
        )
        self.assertTrue(
            any(row["action_type"] == "record_final_reconciliation_outcome" for row in leader_response.data["results"])
        )

    def test_worker_cannot_complete_leader_only_manual_reconciliation(self):
        reconciliation = self.create_discrepancy_with_manual_reconciliation()
        payload = {
            "client_operation_id": "auth-final-1",
            "decision_note": "Leader decision note.",
            "outcome": TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
            "worker_code": "SPOOFED",
        }

        self.client.force_authenticate(self.gdy_worker)
        worker_response = self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/complete-manual/",
            payload,
            format="json",
        )
        self.client.force_authenticate(self.gdy_leader)
        leader_response = self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/complete-manual/",
            {**payload, "client_operation_id": "auth-final-2"},
            format="json",
        )

        self.assertEqual(worker_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(leader_response.status_code, status.HTTP_200_OK)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.completed_by_worker_code, "GDY_LEADER")
        audit_log = AuditLog.objects.filter(
            entity_name="TransferDiscrepancyReconciliation",
            entity_id=str(reconciliation.id),
        ).latest("created_at")
        self.assertEqual(audit_log.actor, self.gdy_leader)


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
                event_type="control",
                result="passed",
                message__icontains="verified",
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


class InterBranchPalletArrivalTests(APITestCase):
    def setUp(self):
        self.source = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.destination = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.other = Branch.objects.create(code="WAW", name="Warsaw", city="Warsaw", country="Poland")
        self.worker = User.objects.create_user(username="GDY_WORKER", password="demo12345")
        self.source_worker = User.objects.create_user(username="GDA_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.worker, branch=self.destination, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.source_worker, branch=self.source, role=UserBranchMembership.Role.WORKER)
        self.product = Product.objects.create(sku="ARR-001", name="Arrival item", unit_of_measure="pcs")
        self.location = Location.objects.create(
            branch=self.destination, code="ARR-01", name="Arrival storage", location_type=Location.LocationType.STORAGE
        )
        self.transfer = InterBranchTransfer.objects.create(
            reference="IBT-ARR-001", source_branch=self.source, destination_branch=self.destination,
            status=InterBranchTransfer.Status.IN_TRANSIT, released_at=timezone.now(),
        )
        self.pallet = TransferPallet.objects.create(
            transfer=self.transfer, scan_code="PAL-GDA-GDY-001", status=TransferPallet.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        TransferPalletItem.objects.create(pallet=self.pallet, product=self.product, expected_quantity=Decimal("5"))

    def scan(self):
        return self.client.post(
            "/api/scanner/inter-branch-arrivals/",
            {"pallet_code": self.pallet.scan_code, "client_operation_id": "arrival-1"}, format="json",
        )

    def test_arrival_is_destination_scoped_idempotent_and_changes_no_inventory(self):
        self.client.force_authenticate(self.worker)
        before = InventoryItem.objects.count()
        first = self.scan()
        second = self.scan()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["arrival"]["arrival_result"], "already_registered")
        self.assertEqual(TransferPalletArrival.objects.filter(pallet=self.pallet).count(), 1)
        self.assertEqual(InventoryItem.objects.count(), before)
        self.assertEqual(AuditLog.objects.filter(event_type="inter_branch_arrival", pallet=self.pallet).count(), 1)
        tasks = self.client.get("/api/mm-tasks/", {"branch": "GDY"})
        self.assertEqual(len(tasks.data["results"]), 1)
        self.assertEqual(tasks.data["results"][0]["remaining_units"], 5)

    def test_source_worker_and_unknown_pallet_are_rejected(self):
        self.client.force_authenticate(self.source_worker)
        forbidden = self.scan()
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.worker)
        missing = self.client.post("/api/scanner/inter-branch-arrivals/", {"pallet_code": "UNKNOWN"}, format="json")
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_receiving_requires_arrival_and_completion_removes_mm_task(self):
        blocked = self.client.post(
            "/api/scanner/receiving/start/", {"pallet_code": self.pallet.scan_code, "worker_code": "GDY_WORKER"}, format="json"
        )
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Register the pallet arrival", blocked.data["detail"])
        self.client.force_authenticate(self.worker)
        self.scan()
        started = self.client.post(
            "/api/scanner/receiving/start/", {"pallet_code": self.pallet.scan_code, "worker_code": "GDY_WORKER"}, format="json"
        )
        self.assertEqual(started.status_code, status.HTTP_200_OK)
        session_id = started.data["receiving_session"]["id"]
        closed = self.client.post("/api/scanner/receiving/close/", {"receiving_session_id": session_id}, format="json")
        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.assertEqual(closed.data["result"], "discrepancy")
        tasks = self.client.get("/api/mm-tasks/", {"branch": "GDY"})
        self.assertEqual(tasks.data["results"], [])
        self.assertEqual(AuditLog.objects.filter(event_type="mm_task_completed", pallet=self.pallet).count(), 1)

    def test_put_away_updates_progress_and_exact_close_completes_task(self):
        self.client.force_authenticate(self.worker)
        self.scan()
        started = self.client.post(
            "/api/scanner/receiving/start/", {"pallet_code": self.pallet.scan_code, "worker_code": "GDY_WORKER"}, format="json"
        )
        session_id = started.data["receiving_session"]["id"]
        self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": "ARR-001", "quantity": "5"}, format="json",
        )
        put_away = self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": "ARR-01"}, format="json",
        )
        self.assertEqual(put_away.status_code, status.HTTP_200_OK)
        tasks = self.client.get("/api/mm-tasks/", {"branch": "GDY"})
        self.assertEqual(tasks.data["results"][0]["put_away_units"], 5)
        self.assertEqual(tasks.data["results"][0]["remaining_units"], 0)
        closed = self.client.post("/api/scanner/receiving/close/", {"receiving_session_id": session_id}, format="json")
        self.assertEqual(closed.data["result"], "exact")
        self.assertEqual(self.client.get("/api/mm-tasks/", {"branch": "GDY"}).data["results"], [])


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
        self.source_unconfirmed_location = Location.objects.create(
            branch=self.source_branch,
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
        TransferPalletArrival.objects.create(pallet=self.pallet, scanned_by_worker_code="WORKER-1")

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
        TransferPalletArrival.objects.create(pallet=pallet, scanned_by_worker_code="WORKER-1")
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

    def get_discrepancy_actions(self, **params):
        return self.client.get("/api/transfer-discrepancy-actions/", params)

    def action_rows(self, **params):
        response = self.get_discrepancy_actions(**params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"]

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

    def test_location_codes_are_unique_per_branch(self):
        duplicate_code_location = Location.objects.create(
            branch=self.source_branch,
            code=self.destination_location.code,
            name="Source A-01-01",
            location_type=Location.LocationType.STORAGE,
        )

        self.assertEqual(duplicate_code_location.code, "A-01-01")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Location.objects.create(
                    branch=self.destination_branch,
                    code=self.destination_location.code,
                    name="Duplicate destination A-01-01",
                    location_type=Location.LocationType.STORAGE,
                )

    def test_inventory_location_must_match_inventory_branch(self):
        InventoryItem.objects.create(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
            quantity_on_hand=Decimal("1"),
            quantity_reserved=Decimal("0"),
        )

        with self.assertRaises(ValidationError):
            InventoryItem.objects.create(
                branch=self.destination_branch,
                location=self.wrong_branch_location,
                product=self.second_product,
                quantity_on_hand=Decimal("1"),
                quantity_reserved=Decimal("0"),
            )

    def test_branch_filters_for_locations_and_inventory(self):
        InventoryItem.objects.create(
            branch=self.destination_branch,
            location=self.destination_location,
            product=self.product,
            quantity_on_hand=Decimal("1"),
            quantity_reserved=Decimal("0"),
        )
        InventoryItem.objects.create(
            branch=self.source_branch,
            location=self.wrong_branch_location,
            product=self.product,
            quantity_on_hand=Decimal("1"),
            quantity_reserved=Decimal("0"),
        )

        locations_response = self.client.get("/api/locations/", {"branch": self.destination_branch.code})
        inventory_response = self.client.get("/api/inventory-items/", {"branch": self.destination_branch.code})

        self.assertEqual(locations_response.status_code, status.HTTP_200_OK)
        self.assertEqual(inventory_response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(row["branch_code"] == self.destination_branch.code for row in locations_response.data["results"]))
        self.assertTrue(all(row["branch_code"] == self.destination_branch.code for row in inventory_response.data["results"]))

    def test_orders_branch_filter_accepts_branch_code(self):
        gdy_order = Order.objects.create(
            branch=self.destination_branch,
            external_reference="GDY-ORDER-001",
            customer_name="GDY Customer",
            status=Order.Status.IMPORTED,
        )
        gda_order = Order.objects.create(
            branch=self.source_branch,
            external_reference="GDA-ORDER-001",
            customer_name="GDA Customer",
            status=Order.Status.IMPORTED,
        )

        gdy_response = self.client.get("/api/orders/", {"branch": self.destination_branch.code})
        gda_response = self.client.get("/api/orders/", {"branch": self.source_branch.code})

        self.assertEqual(gdy_response.status_code, status.HTTP_200_OK)
        self.assertEqual(gda_response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in gdy_response.data["results"]], [gdy_order.id])
        self.assertEqual([row["id"] for row in gda_response.data["results"]], [gda_order.id])

    def test_route_archive_branch_filter_accepts_branch_code(self):
        other_branch = Branch.objects.create(code="WAW", name="Warsaw", city="Warsaw", country="Poland")
        gdy_route = DeliveryRoute.objects.create(branch=self.destination_branch, code="GDY-ARCH", name="GDY Archive")
        gda_route = DeliveryRoute.objects.create(branch=self.source_branch, code="GDA-ARCH", name="GDA Archive")
        other_route = DeliveryRoute.objects.create(branch=other_branch, code="WAW-ARCH", name="WAW Archive")
        for index, route in enumerate([gdy_route, gda_route, other_route], start=1):
            RouteRun.objects.create(
                route=route,
                service_date=timezone.localdate(),
                run_number=index,
                order_cutoff_time=time(8, 0),
                sync_time=time(8, 15),
                departure_time=time(9, 0),
                status=RouteRun.Status.CLOSED,
                closed_at=timezone.now(),
            )

        gdy_response = self.client.get("/api/route-runs/archive/", {"branch_code": self.destination_branch.code})
        gda_response = self.client.get("/api/route-runs/archive/", {"branch_code": self.source_branch.code})

        self.assertEqual([row["route_code"] for row in gdy_response.data["results"]], ["GDY-ARCH"])
        self.assertEqual([row["route_code"] for row in gda_response.data["results"]], ["GDA-ARCH"])

    def test_unknown_pallet_returns_not_found(self):
        response = self.start_receiving("PAL-NOT-FOUND")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_receiving_rejects_source_branch_location(self):
        session_id = self.start_receiving().data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "1")

        response = self.put_away(session_id, location_code=self.wrong_branch_location.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Wrong branch", response.data["detail"])

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

    def test_destination_recovery_rejects_source_branch_location(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)

        response = self.recover_item(
            discrepancy,
            product_code=self.product.sku,
            location_code=self.wrong_branch_location.code,
            quantity="1",
            operation_id="wrong-destination-branch-recovery",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("another branch", response.data["detail"])

    def test_shortage_posting_uses_destination_branch_unconfirmed_location(self):
        discrepancy = self.create_shortage_discrepancy()
        first = self.print_report(discrepancy)
        second = self.print_report(discrepancy)

        destination_item = InventoryItem.objects.get(
            branch=self.destination_branch,
            location=self.unconfirmed_location,
            product=self.product,
        )
        source_item = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.source_unconfirmed_location,
            product=self.product,
        ).first()
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(destination_item.quantity_on_hand, Decimal("1.000"))
        self.assertIsNone(source_item)
        self.assertEqual(
            StockMovement.objects.filter(
                branch=self.destination_branch,
                destination_location=self.unconfirmed_location,
                movement_type=StockMovement.MovementType.RECEIVING_DISCREPANCY,
            ).count(),
            1,
        )

    def test_source_stock_verification_rejects_destination_branch_location(self):
        verification = self.create_source_stock_verification()
        self.begin_source_stock_verification(verification)

        response = self.record_source_stock_found(
            verification,
            product_code=self.product.sku,
            location_code=self.destination_location.code,
            quantity="1",
            operation_id="wrong-source-branch-recovery",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("another branch", response.data["detail"])

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

    def test_discrepancy_action_queue_tracks_source_review_to_reconciliation(self):
        discrepancy = self.create_shortage_discrepancy()
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy, operation_id="queue-source-shortage")
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)

        rows = self.action_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action_type"], "begin_source_review")
        self.assertEqual(rows[0]["action_label"], "Begin source review")
        self.assertEqual(rows[0]["target_reference"], review.reference)
        self.assertEqual(rows[0]["target_url"], f"/wms/source-discrepancy-reviews/{review.id}")
        self.assertEqual(rows[0]["current_status_label"], "Pending review")

        self.begin_source_review(review)
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "complete_source_review")

        self.complete_source_review(
            review,
            finding=TransferDiscrepancySourceReview.Finding.INCONCLUSIVE,
            operation_id="queue-manual-review",
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "acknowledge_reconciliation")
        self.assertEqual(rows[0]["target_reference"], reconciliation.reference)
        self.assertEqual(rows[0]["target_url"], f"/wms/discrepancy-reconciliations/{reconciliation.id}")

        self.acknowledge_reconciliation(reconciliation)
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "record_final_reconciliation_outcome")
        self.assertEqual(rows[0]["target_type"], "reconciliation")

    def test_discrepancy_action_queue_tracks_source_stock_verification_and_final_decision(self):
        verification = self.create_source_stock_verification()
        reconciliation = verification.reconciliation
        item = verification.items.get(product=self.product)
        item.target_quantity = Decimal("3")
        item.save(update_fields=["target_quantity", "updated_at"])

        rows = self.action_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action_type"], "begin_source_stock_verification")
        self.assertEqual(rows[0]["target_reference"], verification.reference)

        self.begin_source_stock_verification(verification)
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "continue_source_stock_verification")

        self.record_source_stock_found(verification, quantity="1", operation_id="queue-source-found")
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "complete_source_search")

        self.complete_source_search(verification, operation_id="queue-source-search")
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "record_final_reconciliation_outcome")
        self.assertEqual(rows[0]["target_reference"], reconciliation.reference)

    def test_discrepancy_action_queue_tracks_transit_investigation_to_completed_exclusion(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)

        rows = self.action_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action_type"], "begin_transit_investigation")
        self.assertEqual(rows[0]["target_reference"], investigation.reference)
        self.assertEqual(rows[0]["target_url"], f"/wms/transit-investigations/{investigation.id}")

        self.begin_transit_investigation(investigation)
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "complete_transit_investigation")

        self.complete_transit_investigation(investigation, operation_id="queue-transit-complete")
        rows = self.action_rows()
        self.assertEqual(rows[0]["action_type"], "record_final_reconciliation_outcome")
        self.assertEqual(rows[0]["target_reference"], reconciliation.reference)

        self.complete_manual_reconciliation(
            reconciliation,
            outcome=TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            note="Final transit evidence supports operational transit loss.",
            operation_id="queue-transit-final",
        )
        self.assertEqual(self.action_rows(), [])

    def test_discrepancy_action_queue_filters_searches_and_does_not_mutate_state(self):
        verification = self.create_source_stock_verification()
        reconciliation = verification.reconciliation
        status_before = reconciliation.status
        movement_count = StockMovement.objects.count()
        inventory_snapshot = list(InventoryItem.objects.order_by("id").values_list("id", "quantity_on_hand"))

        by_action = self.action_rows(action_type="begin_source_stock_verification")
        by_search = self.action_rows(search=verification.reference)
        by_branch = self.action_rows(branch=self.source_branch.code)

        reconciliation.refresh_from_db()
        self.assertEqual(len(by_action), 1)
        self.assertEqual(len(by_search), 1)
        self.assertEqual(len(by_branch), 1)
        self.assertEqual(by_search[0]["discrepancy_reference"], verification.reconciliation.discrepancy.reference)
        self.assertEqual(reconciliation.status, status_before)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(list(InventoryItem.objects.order_by("id").values_list("id", "quantity_on_hand")), inventory_snapshot)

    def test_discrepancy_action_queue_respects_branch_responsibility(self):
        destination_discrepancy = self.create_shortage_discrepancy()
        self.print_report(destination_discrepancy)
        source_discrepancy = self.create_shortage_discrepancy()
        self.print_report(source_discrepancy)
        self.confirm_shortage(source_discrepancy, operation_id="queue-branch-source-shortage")
        source_review = TransferDiscrepancySourceReview.objects.get(discrepancy=source_discrepancy)

        gdy_rows = self.action_rows(branch=self.destination_branch.code)
        gda_rows = self.action_rows(branch=self.source_branch.code)

        self.assertTrue(any(row["action_type"] == "review_destination_shortage" for row in gdy_rows))
        self.assertFalse(any(row["target_reference"] == source_review.reference for row in gdy_rows))
        self.assertTrue(any(row["target_reference"] == source_review.reference for row in gda_rows))

        self.begin_source_review(source_review)
        self.complete_source_review(
            source_review,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
            operation_id="queue-branch-transit-review",
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=source_discrepancy)
        self.acknowledge_reconciliation(reconciliation)
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        self.begin_transit_investigation(investigation)
        self.complete_transit_investigation(investigation, operation_id="queue-branch-transit-complete")

        self.assertTrue(
            any(row["target_reference"] == reconciliation.reference for row in self.action_rows(branch=self.destination_branch.code))
        )
        self.assertTrue(
            any(row["target_reference"] == reconciliation.reference for row in self.action_rows(branch=self.source_branch.code))
        )

    def test_current_events_actor_display_infers_worker_for_manual_events(self):
        review_discrepancy = self.create_shortage_discrepancy()
        self.print_report(review_discrepancy)
        self.confirm_shortage(review_discrepancy, operation_id="queue-actor-shortage")
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=review_discrepancy)

        self.begin_source_review(review, worker_code="DEMO")
        response = self.client.get("/api/audit-logs/current/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        manual_event = next(event for event in response.data["results"] if "began source review" in event["message"])
        automatic_event = next(event for event in response.data["results"] if "Source review" in event["message"] and "was created" in event["message"])
        self.assertEqual(manual_event["actor_display"], "DEMO")
        self.assertEqual(automatic_event["actor_display"], "System")

    def test_current_events_are_filtered_by_relevant_branch(self):
        start_response = self.start_receiving()
        session_id = start_response.data["receiving_session"]["id"]
        self.scan_product(session_id, self.product.sku, "2")
        self.put_away(session_id)
        self.scan_product(session_id, self.second_product.sku, "2")
        self.put_away(session_id)
        self.complete(session_id)
        discrepancy = TransferDiscrepancy.objects.get(pallet=self.pallet)
        self.print_report(discrepancy)
        self.confirm_shortage(discrepancy, operation_id="event-branch-shortage")

        source_events = self.client.get("/api/audit-logs/current/", {"branch": self.source_branch.code})
        destination_events = self.client.get("/api/audit-logs/current/", {"branch": self.destination_branch.code})

        self.assertEqual(source_events.status_code, status.HTTP_200_OK)
        self.assertEqual(destination_events.status_code, status.HTTP_200_OK)
        self.assertTrue(any("Source review" in event["message"] for event in source_events.data["results"]))
        self.assertFalse(any("Receiving started" in event["message"] for event in source_events.data["results"]))
        self.assertTrue(any("Receiving started" in event["message"] for event in destination_events.data["results"]))
        receive_event = AuditLog.objects.get(event_type="receive", product=self.product)
        self.assertEqual(receive_event.quantity, Decimal("2.000"))
        self.assertEqual(receive_event.pallet, self.pallet)
        self.assertEqual(receive_event.transfer, self.transfer)
        self.assertEqual(receive_event.destination_location, self.destination_location)
        self.assertEqual(receive_event.branch, self.destination_branch)
        search_by_pallet = self.client.get("/api/current-events/", {"branch": "GDY", "search": self.pallet.scan_code})
        search_by_transfer = self.client.get("/api/current-events/", {"branch": "GDY", "search": self.transfer.reference})
        self.assertTrue(any(event["event_type"] == "receive" for event in search_by_pallet.data["results"]))
        self.assertTrue(any(event["event_type"] == "receive" for event in search_by_transfer.data["results"]))

    def test_reconciliation_and_transit_events_are_visible_to_both_branches(self):
        reconciliation = self.create_acknowledged_transit_reconciliation()
        investigation = TransferDiscrepancyTransitInvestigation.objects.get(reconciliation=reconciliation)
        self.begin_transit_investigation(investigation)

        source_events = self.client.get("/api/audit-logs/current/", {"branch": self.source_branch.code})
        destination_events = self.client.get("/api/audit-logs/current/", {"branch": self.destination_branch.code})

        self.assertEqual(source_events.status_code, status.HTTP_200_OK)
        self.assertEqual(destination_events.status_code, status.HTTP_200_OK)
        self.assertTrue(any("Reconciliation case" in event["message"] for event in source_events.data["results"]))
        self.assertTrue(any("Reconciliation case" in event["message"] for event in destination_events.data["results"]))
        self.assertTrue(any("began transit investigation" in event["message"] for event in source_events.data["results"]))
        self.assertTrue(any("began transit investigation" in event["message"] for event in destination_events.data["results"]))

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
        self.demo_user = User.objects.create_user(username="DEMO", password="demo12345")
        self.gdy_worker = User.objects.create_user(username="GDY_WORKER", password="demo12345")
        self.gdy_leader = User.objects.create_user(username="GDY_LEADER", password="demo12345")
        self.gda_worker = User.objects.create_user(username="GDA_WORKER", password="demo12345")
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
        self.third_location = Location.objects.create(
            branch=self.branch,
            code="K-01-01",
            name="K-01-01",
            location_type=Location.LocationType.PICKING,
        )
        self.unconfirmed_location = Location.objects.create(
            branch=self.branch,
            code="UNCONFIRMED",
            name="UNCONFIRMED",
            location_type=Location.LocationType.RECEIVING,
        )
        self.product_a = Product.objects.create(
            sku="JOB-A",
            name="Job Product A",
            brand="Test Brand",
            description="Presentation details for the active picking product.",
            image_url="/products/oil-filter.svg",
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
        self.third_product_a_inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.third_location,
            product=self.product_a,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product_b,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.unconfirmed_location,
            product=self.product_a,
            quantity_on_hand=Decimal("0"),
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
            customer_alias="JOB-CUST",
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
        self.task_1.shortage_quantity = Decimal("0")
        self.task_1.quantity_prepared = Decimal("0")
        self.task_1.status = PickingTask.Status.OPEN
        self.task_1.save(update_fields=["quantity_to_pick", "quantity_picked", "shortage_quantity", "quantity_prepared", "status", "updated_at"])

    def create_shortage_challenge(self, cart_work_session_id, quantity="1", worker_code="DEMO"):
        return self.client.post(
            "/api/scanner/picking/shortage-challenge/",
            {"cart_work_session_id": cart_work_session_id, "quantity": quantity, "worker_code": worker_code},
            format="json",
        )

    def report_shortage(self, challenge, code=None, operation_id="shortage-op-1"):
        return self.client.post(
            "/api/scanner/picking/report-shortage/",
            {
                "challenge_token": challenge.data["challenge_token"],
                "confirmation_code": code or challenge.data["confirmation_code"],
                "client_operation_id": operation_id,
            },
            format="json",
        )

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

    def test_authenticated_worker_lists_proformas_for_allowed_branch(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        other_branch = Branch.objects.create(code="OTH", name="Other Branch", city="Gdansk", country="Poland")
        other_route = DeliveryRoute.objects.create(branch=other_branch, code="OTH-R1", name="Other Route")
        self.create_run(other_route, 1)
        self.client.force_authenticate(self.gdy_worker)

        allowed = self.client.get("/api/scanner/proformas/", {"branch": self.branch.id})
        forbidden = self.client.get("/api/scanner/proformas/", {"branch": other_branch.id})

        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertTrue(all(row["branch_code"] == "JOB" for row in allowed.data["results"]))
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

    def test_authenticated_create_jobs_uses_request_user_and_ignores_spoofed_worker_code(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)

        response = self.client.post(
            "/api/scanner/proformas/create-jobs/",
            {
                "route_run_ids": [self.run_1.id],
                "mode": "merged",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        audit_log = AuditLog.objects.get(event_type="picking_job_created")
        self.assertEqual(audit_log.actor, self.gdy_worker)
        self.assertIn("Worker GDY_WORKER", audit_log.message)
        self.assertNotIn("DEMO", audit_log.message)

    def test_authenticated_worker_cannot_create_jobs_for_another_branch(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        other_branch = Branch.objects.create(code="OTH", name="Other Branch", city="Gdansk", country="Poland")
        other_location = Location.objects.create(
            branch=other_branch,
            code="O-01-01",
            name="O-01-01",
            location_type=Location.LocationType.PICKING,
        )
        other_product = Product.objects.create(sku="OTH-A", name="Other Product", barcode="559000000001", unit_of_measure="pcs")
        InventoryItem.objects.create(
            branch=other_branch,
            location=other_location,
            product=other_product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        other_route = DeliveryRoute.objects.create(branch=other_branch, code="OTH-R1", name="Other Route")
        other_run = self.create_run(other_route, 1)
        other_order = Order.objects.create(
            branch=other_branch,
            route_run=other_run,
            external_reference="OTH-ORDER-1",
            customer_name="Other Customer",
            customer_alias="OTH-CUST",
            status=Order.Status.IMPORTED,
        )
        other_line = OrderLine.objects.create(
            order=other_order,
            product=other_product,
            line_number=1,
            quantity_ordered=Decimal("1"),
            quantity_picked=Decimal("0"),
        )
        PickingTask.objects.create(
            branch=other_branch,
            order_line=other_line,
            source_location=other_location,
            quantity_to_pick=Decimal("1"),
        )
        self.client.force_authenticate(self.gdy_worker)

        response = self.client.post(
            "/api/scanner/proformas/create-jobs/",
            {"route_run_ids": [other_run.id], "mode": "merged"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

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

    def test_authenticated_start_creates_active_participant_and_claim(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()

        response = self.start_job(job, cart_code="WOZEK-01")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CartWorkSession.objects.count(), 1)
        participant = CartWorkParticipant.objects.get(user=self.gdy_worker)
        self.assertEqual(participant.cart_work_session.cart.code, "WOZEK-01")
        self.assertEqual(participant.status, CartWorkParticipant.Status.ACTIVE)
        self.assertIsNotNone(participant.current_picking_task)
        self.assertEqual(PickingTaskClaim.objects.filter(cart_work_participant=participant, status=PickingTaskClaim.Status.CLAIMED).count(), 1)
        self.assertEqual(response.data["participant"]["username"], "GDY_WORKER")

    def test_same_branch_leader_can_join_existing_cart_work_idempotently(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        self.start_job(job, cart_code="WOZEK-01")

        self.client.force_authenticate(self.gdy_leader)
        first_join = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")
        second_join = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")

        self.assertEqual(first_join.status_code, status.HTTP_200_OK)
        self.assertEqual(second_join.status_code, status.HTTP_200_OK)
        self.assertEqual(CartWorkSession.objects.filter(picking_job=job).count(), 1)
        self.assertEqual(CartWorkParticipant.objects.filter(cart_work_session__picking_job=job, status=CartWorkParticipant.Status.ACTIVE).count(), 2)
        self.assertEqual(CartWorkParticipant.objects.filter(user=self.gdy_leader, status=CartWorkParticipant.Status.ACTIVE).count(), 1)
        self.assertEqual(AuditLog.objects.filter(event_type="cart_work_joined", actor=self.gdy_leader).count(), 1)

    def test_joining_worker_receives_different_claimed_task(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        self.start_job(job, cart_code="WOZEK-01")

        self.client.force_authenticate(self.gdy_leader)
        response = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = list(PickingTaskClaim.objects.filter(status=PickingTaskClaim.Status.CLAIMED).order_by("id"))
        self.assertEqual(len(claims), 2)
        self.assertNotEqual(claims[0].picking_task_id, claims[1].picking_task_id)

    def test_joining_other_branch_cart_work_is_rejected(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        other_branch = Branch.objects.create(code="GDA", name="Gdansk Branch", city="Gdansk", country="Poland")
        UserBranchMembership.objects.create(user=self.gda_worker, branch=other_branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        self.start_job(job, cart_code="WOZEK-01")

        self.client.force_authenticate(self.gda_worker)
        response = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_cannot_join_second_active_cart_work(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id], mode="separate")
        first_job = PickingJob.objects.get()
        self.start_job(first_job, cart_code="WOZEK-01")
        self.client.force_authenticate(self.gdy_leader)
        self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")

        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_2.id], mode="separate")
        second_job = PickingJob.objects.exclude(id=first_job.id).get()
        start_second = self.start_job(second_job, cart_code="WOZEK-02")

        self.assertEqual(start_second.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already have active work", start_second.data["detail"])

    def test_missing_cart_work_session_returns_404(self):
        response = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": 999999})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("not found", response.data["detail"].lower())

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
        self.assertEqual(response.data["current_instruction"]["product"]["brand"], "Test Brand")
        self.assertEqual(
            response.data["current_instruction"]["product"]["description"],
            "Presentation details for the active picking product.",
        )
        self.assertEqual(response.data["current_instruction"]["product"]["image_url"], "/products/oil-filter.svg")
        task = response.data["tasks"][0]
        self.assertEqual(task["product_brand"], "Test Brand")
        self.assertEqual(task["product_image_url"], "/products/oil-filter.svg")

    def test_stale_zero_stock_task_is_reallocated_before_current_pick(self):
        self.set_task_1_quantity("2")
        self.product_a_inventory.quantity_on_hand = Decimal("0")
        self.product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["current_instruction"]["location"]["code"], "J-99-01")
        self.assertEqual(response.data["current_instruction"]["product"]["sku"], "JOB-A")
        self.assertEqual(response.data["current_instruction"]["remaining_quantity"], "2.000")
        self.assertEqual(PickingShortage.objects.count(), 0)
        self.assertEqual(ReplenishmentRequest.objects.count(), 0)
        self.assertEqual(PickingTaskReallocation.objects.count(), 1)
        self.task_1.refresh_from_db()
        self.other_product_a_inventory.refresh_from_db()
        self.assertEqual(self.task_1.status, PickingTask.Status.CANCELLED)
        self.assertEqual(self.other_product_a_inventory.quantity_reserved, Decimal("2.000"))
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.PICKING_SHORTAGE).exists())
        self.assertTrue(AuditLog.objects.filter(event_type="picking_task_reallocated").exists())

        second = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(PickingTaskReallocation.objects.count(), 1)
        self.assertEqual(PickingTask.objects.filter(order_line=self.task_1.order_line).exclude(status=PickingTask.Status.CANCELLED).count(), 1)
        self.other_product_a_inventory.refresh_from_db()
        self.assertEqual(self.other_product_a_inventory.quantity_reserved, Decimal("2.000"))

        confirm = self.confirm_location(start.data["cart_work_session"]["id"], "J-99-01")
        pick = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": start.data["cart_work_session"]["id"],
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        self.assertEqual(pick.status_code, status.HTTP_200_OK)
        self.other_product_a_inventory.refresh_from_db()
        replacement_task = PickingTask.objects.get(order_line=self.task_1.order_line, source_location=self.other_location)
        self.assertEqual(replacement_task.quantity_picked, Decimal("2.000"))
        self.assertEqual(self.other_product_a_inventory.quantity_on_hand, Decimal("5.000"))
        self.assertEqual(self.other_product_a_inventory.quantity_reserved, Decimal("0.000"))
        self.assertEqual(pick.data["picking_job"]["total_quantity"], "2.000")
        self.assertEqual(pick.data["picking_job"]["picked_quantity"], "2.000")

    def test_stale_partial_original_stock_splits_work_without_duplicate_demand(self):
        self.set_task_1_quantity("4")
        self.product_a_inventory.quantity_on_hand = Decimal("1")
        self.product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.other_product_a_inventory.quantity_on_hand = Decimal("2")
        self.other_product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.third_product_a_inventory.quantity_on_hand = Decimal("1")
        self.third_product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task_1.refresh_from_db()
        active_tasks = PickingTask.objects.filter(order_line=self.task_1.order_line).exclude(status=PickingTask.Status.CANCELLED)
        self.assertEqual(self.task_1.quantity_to_pick, Decimal("1.000"))
        self.assertEqual(active_tasks.count(), 3)
        self.assertEqual(sum((task.quantity_to_pick for task in active_tasks), Decimal("0")), Decimal("4.000"))
        self.assertEqual(PickingTaskReallocation.objects.count(), 2)
        self.assertEqual(ReplenishmentRequest.objects.count(), 0)
        self.assertEqual(response.data["cart_work_session"]["picking_job"]["total_quantity"], "4.000")
        self.assertEqual(response.data["cart_work_session"]["picking_job"]["picked_quantity"], "0.000")

    def test_stale_task_creates_replenishment_only_for_uncovered_system_stock(self):
        self.set_task_1_quantity("4")
        self.product_a_inventory.quantity_on_hand = Decimal("0")
        self.product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.other_product_a_inventory.quantity_on_hand = Decimal("2")
        self.other_product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        replenishment = ReplenishmentRequest.objects.get()
        self.assertEqual(PickingShortage.objects.count(), 0)
        self.assertEqual(PickingTaskReallocation.objects.count(), 1)
        self.assertEqual(replenishment.quantity, Decimal("2.000"))
        self.assertEqual(replenishment.reason, ReplenishmentRequest.Reason.SYSTEM_STOCK_UNAVAILABLE)
        self.assertEqual(replenishment.picking_task, self.task_1)

    def test_stale_task_with_no_branch_stock_creates_replenishment_and_no_current_pick(self):
        self.set_task_1_quantity("4")
        self.product_a_inventory.quantity_on_hand = Decimal("0")
        self.product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.other_product_a_inventory.quantity_on_hand = Decimal("0")
        self.other_product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)

        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["current_instruction"])
        replenishment = ReplenishmentRequest.objects.get()
        self.assertEqual(replenishment.quantity, Decimal("4.000"))
        self.assertEqual(PickingShortage.objects.count(), 0)
        self.assertEqual(PickingTaskReallocation.objects.count(), 0)

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
        self.create_jobs(route_run_ids=[self.run_1.id])
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
        self.assertIn("remaining", response.data["detail"])

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

    def test_shortage_challenge_generates_four_digit_code(self):
        self.set_task_1_quantity("3")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)

        response = self.create_shortage_challenge(cart_work_session_id, quantity="2", worker_code="DEMO")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertRegex(response.data["confirmation_code"], r"^\d{4}$")
        self.assertEqual(response.data["summary"]["product_sku"], "JOB-A")
        self.assertEqual(response.data["summary"]["customer_alias"], "JOB-CUST")

    def test_shortage_wrong_confirmation_code_is_rejected(self):
        self.set_task_1_quantity("3")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="2")

        wrong_code = "0000" if challenge.data["confirmation_code"] != "0000" else "0001"
        response = self.report_shortage(challenge, code=wrong_code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PickingShortage.objects.exists())

    def test_location_shortage_allocates_alternative_stock_before_replenishment(self):
        self.set_task_1_quantity("4")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        pick = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )
        self.assertEqual(pick.status_code, status.HTTP_200_OK)
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="2")

        response = self.report_shortage(challenge)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.task_1.refresh_from_db()
        self.product_a_inventory.refresh_from_db()
        unconfirmed = InventoryItem.objects.get(branch=self.branch, location=self.unconfirmed_location, product=self.product_a)
        self.assertEqual(self.task_1.quantity_picked, Decimal("2.000"))
        self.assertEqual(self.task_1.shortage_quantity, Decimal("2.000"))
        self.assertEqual(self.task_1.status, PickingTask.Status.PICKED)
        self.assertEqual(self.product_a_inventory.quantity_on_hand, Decimal("1.000"))
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("2.000"))
        self.assertEqual(PickingShortage.objects.count(), 1)
        shortage = PickingShortage.objects.get()
        self.assertEqual(shortage.location_missing_quantity, Decimal("2.000"))
        self.assertEqual(shortage.alternative_allocated_quantity, Decimal("2.000"))
        self.assertEqual(shortage.customer_unfulfilled_quantity, Decimal("0.000"))
        self.assertEqual(PickingShortageAllocation.objects.count(), 1)
        allocation = PickingShortageAllocation.objects.get()
        replacement_task = allocation.replacement_picking_task
        self.assertEqual(replacement_task.source_location, self.other_location)
        self.assertEqual(replacement_task.quantity_to_pick, Decimal("2.000"))
        self.other_product_a_inventory.refresh_from_db()
        self.assertEqual(self.other_product_a_inventory.quantity_reserved, Decimal("2.000"))
        self.assertEqual(ReplenishmentRequest.objects.count(), 0)
        self.assertEqual(response.data["current_instruction"]["location"]["code"], "J-99-01")
        self.assertEqual(response.data["current_instruction"]["product"]["sku"], "JOB-A")
        self.assertEqual(response.data["replenishment_request"], None)
        self.assertEqual(response.data["alternative_allocations"][0]["location_code"], "J-99-01")
        self.assertTrue(AuditLog.objects.filter(event_type="picking_location_shortage", product=self.product_a).exists())
        self.assertTrue(AuditLog.objects.filter(event_type="alternative_stock_allocated", product=self.product_a).exists())

    def test_replacement_picking_updates_allocation_without_expression_crash(self):
        self.set_task_1_quantity("4")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="2")
        report = self.report_shortage(challenge)
        self.assertEqual(report.status_code, status.HTTP_201_CREATED)

        location = self.confirm_location(cart_work_session_id, "J-99-01")
        pick = self.client.post(
            "/api/scanner/picking/pick/",
            {
                "cart_work_session_id": cart_work_session_id,
                "product_code": "JOB-A",
                "quantity": "2",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(location.status_code, status.HTTP_200_OK)
        self.assertEqual(pick.status_code, status.HTTP_200_OK)
        allocation = PickingShortageAllocation.objects.get()
        replacement_task = allocation.replacement_picking_task
        self.other_product_a_inventory.refresh_from_db()
        self.assertEqual(allocation.picked_quantity, Decimal("2.000"))
        self.assertEqual(allocation.status, PickingShortageAllocation.Status.COMPLETED)
        self.assertEqual(replacement_task.quantity_picked, Decimal("2.000"))
        self.assertEqual(replacement_task.status, PickingTask.Status.PICKED)
        self.assertEqual(self.other_product_a_inventory.quantity_reserved, Decimal("0.000"))

    def test_location_shortage_creates_replenishment_only_for_residual_quantity(self):
        self.set_task_1_quantity("5")
        self.other_product_a_inventory.quantity_on_hand = Decimal("2")
        self.other_product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="5")

        response = self.report_shortage(challenge)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        shortage = PickingShortage.objects.get()
        replenishment = ReplenishmentRequest.objects.get()
        self.assertEqual(shortage.alternative_allocated_quantity, Decimal("2.000"))
        self.assertEqual(shortage.customer_unfulfilled_quantity, Decimal("3.000"))
        self.assertEqual(replenishment.quantity, Decimal("3.000"))
        self.task_1.refresh_from_db()
        self.assertEqual(self.task_1.status, PickingTask.Status.WAITING_REPLENISHMENT)
        self.assertEqual(response.data["replenishment_request"]["quantity"], "3.000")

    def test_location_shortage_without_alternative_stock_creates_full_replenishment(self):
        self.set_task_1_quantity("2")
        self.other_product_a_inventory.quantity_reserved = Decimal("7")
        self.other_product_a_inventory.save(update_fields=["quantity_reserved", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="2")

        response = self.report_shortage(challenge)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        shortage = PickingShortage.objects.get()
        replenishment = ReplenishmentRequest.objects.get()
        self.assertEqual(PickingShortageAllocation.objects.count(), 0)
        self.assertEqual(shortage.alternative_allocated_quantity, Decimal("0.000"))
        self.assertEqual(shortage.customer_unfulfilled_quantity, Decimal("2.000"))
        self.assertEqual(replenishment.quantity, Decimal("2.000"))

    def test_shortage_retry_does_not_duplicate_replenishment_or_movement(self):
        self.set_task_1_quantity("2")
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="2")

        first = self.report_shortage(challenge, operation_id="same-op")
        second = self.report_shortage(challenge, operation_id="same-op")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(PickingShortage.objects.count(), 1)
        self.assertEqual(ReplenishmentRequest.objects.count(), 0)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.MovementType.PICKING_SHORTAGE).count(), 1)

    def test_found_stock_moves_unconfirmed_to_real_location(self):
        self.set_task_1_quantity("2")
        self.other_product_a_inventory.quantity_reserved = Decimal("7")
        self.other_product_a_inventory.save(update_fields=["quantity_reserved", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        challenge = self.create_shortage_challenge(cart_work_session_id, quantity="2")
        self.report_shortage(challenge)
        shortage = PickingShortage.objects.get()
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)

        response = self.client.post(
            f"/api/picking-shortages/{shortage.id}/found-stock/",
            {"quantity": "2", "location_code": "J-99-01", "worker_code": "GDY_WORKER"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        shortage.refresh_from_db()
        unconfirmed = InventoryItem.objects.get(branch=self.branch, location=self.unconfirmed_location, product=self.product_a)
        found = InventoryItem.objects.get(branch=self.branch, location=self.other_location, product=self.product_a)
        self.assertEqual(shortage.status, PickingShortage.Status.FOUND)
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("0.000"))
        self.assertEqual(found.quantity_on_hand, Decimal("9.000"))
        self.assertEqual(ReplenishmentRequest.objects.count(), 1)

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
        event = AuditLog.objects.get(event_type="pick", product=self.product_a, order=self.order_1)
        self.assertEqual(event.quantity, Decimal("1.000"))
        self.assertEqual(event.source_location, self.location)
        self.assertEqual(event.cart.code, "WOZEK-01")
        self.assertEqual(event.reference, "JOB-ORDER-1")
        search = self.client.get("/api/current-events/", {"search": "JOB-A", "cart": "WOZEK-01"})
        self.assertTrue(any(row["event_type"] == "pick" and row["order_reference"] == "JOB-ORDER-1" for row in search.data["results"]))

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
        prepare_event = AuditLog.objects.get(event_type="control")
        mismatch_event = AuditLog.objects.get(event_type="control_mismatch")
        self.assertEqual(prepare_event.actor, self.demo_user)
        self.assertEqual(prepare_event.product, self.product_a)
        self.assertEqual(prepare_event.cart.code, "WOZEK-01")
        self.assertEqual(prepare_event.result, "passed")
        self.assertEqual(prepare_event.expected_quantity, Decimal("1.000"))
        self.assertEqual(prepare_event.checked_quantity, Decimal("1.000"))
        self.assertEqual(mismatch_event.product, self.product_a)
        self.assertEqual(mismatch_event.result, "mismatch")
        self.assertEqual(mismatch_event.actor, self.demo_user)
        self.assertEqual(mismatch_event.expected_quantity, Decimal("0.000"))
        self.assertEqual(mismatch_event.checked_quantity, Decimal("1.000"))
        filtered = self.client.get(
            "/api/current-events/",
            {"product": "JOB-A", "cart": "WOZEK-01", "order": "JOB-ORDER-1", "result": "passed"},
        )
        event_types = {row["event_type"] for row in filtered.data["results"]}
        self.assertEqual(event_types, {"control"})

    def test_authenticated_control_uses_request_user_as_actor(self):
        self.create_jobs(route_run_ids=[self.run_1.id])
        job = PickingJob.objects.get()
        start = self.start_job(job)
        session_id = start.data["session"]["id"]
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.confirm_location(cart_work_session_id)
        self.client.post(
            "/api/scanner/picking/pick/",
            {"cart_work_session_id": cart_work_session_id, "product_code": "JOB-A", "quantity": "1"},
            format="json",
        )
        self.client.post(
            "/api/scanner/control/print-label/",
            {"session_id": session_id, "order_reference": "JOB-ORDER-1", "printer_code": "ZEBRA-01"},
            format="json",
        )
        self.client.force_authenticate(self.gdy_worker)

        response = self.client.post(
            "/api/scanner/picking/prepare/",
            {"session_id": session_id, "order_reference": "JOB-ORDER-1", "product_code": "JOB-A", "quantity": "1"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = AuditLog.objects.get(event_type="control")
        self.assertEqual(event.actor, self.gdy_worker)
        self.assertIn("Worker GDY_WORKER verified", event.message)

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
