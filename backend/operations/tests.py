from datetime import datetime, time
from decimal import Decimal
from io import StringIO
import threading
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, models, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from django.test import TransactionTestCase


User = get_user_model()

from accounts.models import UserBranchMembership


def create_branch_user(username, branch, role=UserBranchMembership.Role.WORKER):
    user = User.objects.create_user(username=username, password="demo12345")
    UserBranchMembership.objects.create(user=user, branch=branch, role=role)
    return user


from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkParticipant,
    CartWorkSession,
    CycleCountLine,
    CycleCountLocation,
    CycleCountRecount,
    CycleCountSession,
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
    RouteRun,
    SalesCorrection,
    SalesCorrectionLine,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerQuickTransferOperation,
    ScannerSession,
    Shipment,
    ShipmentLine,
    ShipmentLineQuantityAdjustment,
    ShipmentRouteAssignment,
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


class ReturnDocumentWorkflowTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="RET", name="Returns Branch")
        self.other_branch = Branch.objects.create(code="OTH", name="Other Branch")
        self.worker = create_branch_user("RET_WORKER", self.branch)
        self.leader = create_branch_user("RET_LEADER", self.branch, UserBranchMembership.Role.LEADER)
        self.other_worker = create_branch_user("OTH_WORKER", self.other_branch)
        self.returns_area = Location.objects.create(branch=self.branch, code="RETURNS", name="Returns Area", location_type=Location.LocationType.RETURNS)
        Location.objects.create(branch=self.other_branch, code="RETURNS", name="Returns Area", location_type=Location.LocationType.RETURNS)
        self.product = Product.objects.create(sku="RET-PROD", name="Return Product", barcode="999000000001")
        self.document = ExternalReturnDocument.objects.create(
            branch=self.branch,
            external_reference="ZW1103872",
            source_system="AX",
            customer_name="Return Customer",
            source_sales_document_reference="AX-SALE-RET",
        )
        self.line = ExternalReturnDocumentLine.objects.create(
            document=self.document,
            product=self.product,
            line_number=1,
            expected_quantity=Decimal("5"),
        )

    def login(self, user):
        self.client.force_authenticate(user=user)

    def action_payload(self, **overrides):
        payload = {
            "action_type": ReturnAction.ActionType.ACCEPT_REMAINING,
            "quantity": "2",
            "note": "",
            "client_operation_id": str(uuid.uuid4()),
        }
        payload.update(overrides)
        return payload

    def post_action(self, payload):
        return self.client.post(f"/api/return-documents/{self.document.id}/lines/{self.line.id}/actions/", payload, format="json")

    def test_anonymous_return_documents_are_denied(self):
        response = self.client.get("/api/return-documents/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_same_branch_worker_accepts_partial_quantity_to_returns_area(self):
        self.login(self.worker)
        response = self.post_action(self.action_payload(quantity="2"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.line.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(self.line.accepted_quantity, Decimal("2"))
        self.assertEqual(self.line.remaining_quantity, Decimal("3"))
        self.assertEqual(self.document.status, ExternalReturnDocument.Status.IN_PROGRESS)
        inventory = InventoryItem.objects.get(branch=self.branch, location=self.returns_area, product=self.product)
        self.assertEqual(inventory.quantity_on_hand, Decimal("2"))
        movement = StockMovement.objects.get(movement_type=StockMovement.MovementType.RETURN_RECEIPT)
        self.assertEqual(movement.destination_location, self.returns_area)
        self.assertEqual(movement.performed_by, self.worker)
        self.assertEqual(ReturnAction.objects.get().performed_by, self.worker)

    def test_same_branch_leader_uses_same_return_action_without_approval(self):
        self.login(self.leader)
        response = self.post_action(self.action_payload(action_type=ReturnAction.ActionType.REJECT_REMAINING, quantity="1"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.line.refresh_from_db()
        self.assertEqual(self.line.rejected_quantity, Decimal("1"))
        self.assertFalse(StockMovement.objects.exists())

    def test_other_branch_user_cannot_access_return_document(self):
        self.login(self.other_worker)
        response = self.client.get(f"/api/return-documents/{self.document.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_on_hold_then_accept_on_hold_preserves_two_employee_actions(self):
        self.login(self.worker)
        hold_response = self.post_action(
            self.action_payload(action_type=ReturnAction.ActionType.PUT_ON_HOLD, quantity="1", note="Needs visual check")
        )
        self.assertEqual(hold_response.status_code, status.HTTP_200_OK)
        self.login(self.leader)
        accept_response = self.post_action(self.action_payload(action_type=ReturnAction.ActionType.ACCEPT_ON_HOLD, quantity="1"))
        self.assertEqual(accept_response.status_code, status.HTTP_200_OK)
        self.line.refresh_from_db()
        self.assertEqual(self.line.on_hold_quantity, Decimal("0"))
        self.assertEqual(self.line.accepted_quantity, Decimal("1"))
        self.assertEqual(
            list(ReturnAction.objects.order_by("created_at").values_list("performed_by__username", flat=True)),
            ["RET_WORKER", "RET_LEADER"],
        )

    def test_return_action_idempotency_replays_without_double_inventory(self):
        self.login(self.worker)
        operation_id = str(uuid.uuid4())
        payload = self.action_payload(client_operation_id=operation_id, quantity="2")
        first = self.post_action(payload)
        second = self.post_action(payload)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(ReturnAction.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)
        inventory = InventoryItem.objects.get(branch=self.branch, location=self.returns_area, product=self.product)
        self.assertEqual(inventory.quantity_on_hand, Decimal("2"))

    def test_return_action_operation_id_conflict_does_not_mutate(self):
        self.login(self.worker)
        operation_id = str(uuid.uuid4())
        self.assertEqual(self.post_action(self.action_payload(client_operation_id=operation_id, quantity="1")).status_code, status.HTTP_200_OK)
        response = self.post_action(self.action_payload(client_operation_id=operation_id, quantity="2"))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(ReturnAction.objects.count(), 1)

    def test_missing_returns_area_fails_without_inventory_mutation(self):
        self.returns_area.delete()
        self.login(self.worker)
        response = self.post_action(self.action_payload(quantity="1"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(InventoryItem.objects.exists())
        self.assertFalse(StockMovement.objects.exists())


class SalesCorrectionWorkflowTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="COR", name="Correction Branch")
        self.other_branch = Branch.objects.create(code="CO2", name="Other Branch")
        self.worker = create_branch_user("COR_WORKER", self.branch)
        self.leader = create_branch_user("COR_LEADER", self.branch, UserBranchMembership.Role.LEADER)
        self.other_worker = create_branch_user("CO2_WORKER", self.other_branch)
        self.returns_area = Location.objects.create(branch=self.branch, code="RETURNS", name="Returns Area", location_type=Location.LocationType.RETURNS)
        Location.objects.create(branch=self.other_branch, code="RETURNS", name="Returns Area", location_type=Location.LocationType.RETURNS)
        self.product = Product.objects.create(sku="COR-PROD", name="Corrected Product", barcode="999000000002")
        self.order = Order.objects.create(
            branch=self.branch,
            external_reference="AX-SALE-COR-001",
            customer_name="Correction Customer One",
            customer_alias="COR-C1",
            status=Order.Status.COMPLETED,
        )
        self.order_line = OrderLine.objects.create(order=self.order, product=self.product, line_number=1, quantity_ordered=Decimal("4"))
        second_order = Order.objects.create(
            branch=self.branch,
            external_reference="AX-SALE-COR-002",
            customer_name="Correction Customer Two",
            customer_alias="COR-C2",
            status=Order.Status.COMPLETED,
        )
        OrderLine.objects.create(order=second_order, product=self.product, line_number=1, quantity_ordered=Decimal("2"))
        cancelled_order = Order.objects.create(
            branch=self.branch,
            external_reference="AX-SALE-CANCELLED",
            customer_name="Cancelled Customer",
            status=Order.Status.CANCELLED,
        )
        OrderLine.objects.create(order=cancelled_order, product=self.product, line_number=1, quantity_ordered=Decimal("10"))

    def login(self, user):
        self.client.force_authenticate(user=user)

    def create_correction(self, user=None):
        self.login(user or self.worker)
        response = self.client.post("/api/sales-corrections/", {"branch": self.branch.code}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return SalesCorrection.objects.get(id=response.data["id"])

    def test_sales_history_search_returns_completed_same_branch_sales(self):
        self.login(self.worker)
        response = self.client.get("/api/sales-corrections/sales-history/", {"branch": "COR", "product": self.product.barcode})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        references = {row["source_sales_document_reference"] for row in response.data}
        self.assertIn("AX-SALE-COR-001", references)
        self.assertIn("AX-SALE-COR-002", references)
        self.assertNotIn("AX-SALE-CANCELLED", references)

    def test_worker_confirms_sales_correction_to_returns_area(self):
        correction = self.create_correction()
        add_response = self.client.post(
            f"/api/sales-corrections/{correction.id}/add-line/",
            {"source_order_line": self.order_line.id, "quantity": "2"},
            format="json",
        )
        self.assertEqual(add_response.status_code, status.HTTP_200_OK)
        confirm_response = self.client.post(
            f"/api/sales-corrections/{correction.id}/confirm/",
            {"client_operation_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK)
        correction.refresh_from_db()
        self.assertEqual(correction.status, SalesCorrection.Status.COMPLETED)
        self.assertEqual(correction.confirmed_by, self.worker)
        inventory = InventoryItem.objects.get(branch=self.branch, location=self.returns_area, product=self.product)
        self.assertEqual(inventory.quantity_on_hand, Decimal("2"))
        movement = StockMovement.objects.get(movement_type=StockMovement.MovementType.SALES_CORRECTION_RECEIPT)
        self.assertEqual(movement.performed_by, self.worker)

    def test_leader_confirms_without_approval_step(self):
        correction = self.create_correction(user=self.leader)
        response = self.client.post(
            f"/api/sales-corrections/{correction.id}/add-line/",
            {"source_order_line": self.order_line.id, "quantity": "1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        confirm = self.client.post(
            f"/api/sales-corrections/{correction.id}/confirm/",
            {"client_operation_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        correction.refresh_from_db()
        self.assertEqual(correction.confirmed_by, self.leader)

    def test_correction_cannot_exceed_remaining_correctable_quantity(self):
        correction = self.create_correction()
        response = self.client.post(
            f"/api/sales-corrections/{correction.id}/add-line/",
            {"source_order_line": self.order_line.id, "quantity": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_branch_cannot_access_correction(self):
        correction = self.create_correction()
        self.login(self.other_worker)
        response = self.client.get(f"/api/sales-corrections/{correction.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_correction_confirmation_replay_posts_once(self):
        correction = self.create_correction()
        self.client.post(f"/api/sales-corrections/{correction.id}/add-line/", {"source_order_line": self.order_line.id, "quantity": "1"}, format="json")
        operation_id = str(uuid.uuid4())
        first = self.client.post(f"/api/sales-corrections/{correction.id}/confirm/", {"client_operation_id": operation_id}, format="json")
        second = self.client.post(f"/api/sales-corrections/{correction.id}/confirm/", {"client_operation_id": operation_id}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.MovementType.SALES_CORRECTION_RECEIPT).count(), 1)

    def test_correction_activity_report_is_employee_attributed(self):
        correction = self.create_correction()
        self.client.post(f"/api/sales-corrections/{correction.id}/add-line/", {"source_order_line": self.order_line.id, "quantity": "1"}, format="json")
        self.client.post(f"/api/sales-corrections/{correction.id}/confirm/", {"client_operation_id": str(uuid.uuid4())}, format="json")
        response = self.client.get("/api/sales-corrections/activity-report/", {"branch": "COR", "employee": "COR_WORKER"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["completed_corrections"], 1)
        self.assertEqual(response.data["results"][0]["employee"], "COR_WORKER")


class ShipmentCommandCenterTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="SHP", name="Shipment Branch")
        self.other_branch = Branch.objects.create(code="OTH", name="Other Shipment Branch")
        self.destination_branch = Branch.objects.create(code="DST", name="Destination Branch")
        self.worker = create_branch_user("SHP_WORKER", self.branch)
        self.other_worker = create_branch_user("OTH_SHP_WORKER", self.other_branch)
        self.destination_worker = create_branch_user("DST_WORKER", self.destination_branch)
        self.product = Product.objects.create(sku="SHP-PROD", name="Shipment Product", barcode="777000000001")
        self.location = Location.objects.create(branch=self.branch, code="A-01-01", name="A-01-01", location_type=Location.LocationType.STORAGE)
        InventoryItem.objects.create(branch=self.branch, location=self.location, product=self.product, quantity_on_hand=Decimal("20"))
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="ROUTE-SHP", name="Shipment Route")
        self.route_run = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 0),
            sync_time=time(8, 30),
            departure_time=time(10, 0),
            status=RouteRun.Status.OPEN,
        )
        self.route_run_2 = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate() + timezone.timedelta(days=1),
            run_number=1,
            order_cutoff_time=time(8, 0),
            sync_time=time(8, 30),
            departure_time=time(11, 0),
            status=RouteRun.Status.OPEN,
        )
        self.route_run_today_target = RouteRun.objects.create(
            route=self.route,
            service_date=timezone.localdate(),
            run_number=2,
            order_cutoff_time=time(9, 0),
            sync_time=time(9, 30),
            departure_time=time(12, 0),
            status=RouteRun.Status.OPEN,
        )
        other_route = DeliveryRoute.objects.create(branch=self.other_branch, code="ROUTE-OTH", name="Other Route")
        self.other_route_run = RouteRun.objects.create(
            route=other_route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 0),
            sync_time=time(8, 30),
            departure_time=time(10, 0),
            status=RouteRun.Status.OPEN,
        )
        self.order = Order.objects.create(
            branch=self.branch,
            route_run=self.route_run,
            external_reference="AX-SHP-001",
            customer_name="Shipment Customer",
            customer_alias="SHP-CUST",
            status=Order.Status.IMPORTED,
        )
        self.order_line = OrderLine.objects.create(order=self.order, product=self.product, line_number=1, quantity_ordered=Decimal("3"))
        self.shipment = Shipment.objects.create(
            reference="SHP-TEST-001",
            branch=self.branch,
            order=self.order,
            route_run=self.route_run,
            source_system="AX",
            external_reference="AX-SHP-EXT-001",
            external_order_reference=self.order.external_reference,
            customer_name=self.order.customer_name,
            customer_alias=self.order.customer_alias,
            delivery_date=timezone.localdate(),
        )
        ShipmentLine.objects.create(
            shipment=self.shipment,
            order_line=self.order_line,
            product=self.product,
            line_number=1,
            ordered_quantity=self.order_line.quantity_ordered,
            external_line_reference="AX-SHP-EXT-001-L1",
        )

    def login(self, user=None):
        self.client.force_authenticate(user=user or self.worker)

    def test_anonymous_shipments_are_denied(self):
        response = self.client.get("/api/shipments/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_shipment_list_is_branch_scoped(self):
        other_order = Order.objects.create(branch=self.other_branch, external_reference="AX-OTH-SHP", status=Order.Status.IMPORTED)
        other_line = OrderLine.objects.create(order=other_order, product=self.product, line_number=1, quantity_ordered=Decimal("1"))
        other_shipment = Shipment.objects.create(
            reference="SHP-OTH-001",
            branch=self.other_branch,
            order=other_order,
            source_system="AX",
            external_reference="AX-OTH-SHP-EXT",
        )
        ShipmentLine.objects.create(shipment=other_shipment, order_line=other_line, product=self.product, line_number=1, ordered_quantity=Decimal("1"))
        self.login()
        response = self.client.get("/api/shipments/", {"branch": "SHP"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        references = {row["reference"] for row in response.data["results"]}
        self.assertIn("SHP-TEST-001", references)
        self.assertNotIn("SHP-OTH-001", references)

    def test_shipment_detail_loads_by_actual_id_and_enforces_branch_scope(self):
        self.login()
        response = self.client.get(f"/api/shipments/{self.shipment.id}/", {"branch": "SHP"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.shipment.id)
        self.assertEqual(response.data["reference"], self.shipment.reference)

        missing = self.client.get("/api/shipments/999999/", {"branch": "SHP"})
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

        other_order = Order.objects.create(branch=self.other_branch, external_reference="AX-OTH-SHP-DETAIL", status=Order.Status.IMPORTED)
        other_line = OrderLine.objects.create(order=other_order, product=self.product, line_number=1, quantity_ordered=Decimal("1"))
        other_shipment = Shipment.objects.create(
            reference="SHP-OTH-DETAIL",
            branch=self.other_branch,
            order=other_order,
            source_system="AX",
            external_reference="AX-OTH-SHP-DETAIL-EXT",
        )
        ShipmentLine.objects.create(shipment=other_shipment, order_line=other_line, product=self.product, line_number=1, ordered_quantity=Decimal("1"))

        forbidden = self.client.get(f"/api/shipments/{other_shipment.id}/", {"branch": "OTH"})
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

    def test_activation_records_actor_and_event(self):
        self.login()
        response = self.client.post(f"/api/shipments/{self.shipment.id}/activate/", {"client_operation_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.ACTIVE)
        self.assertEqual(self.shipment.activated_by, self.worker)
        self.assertTrue(AuditLog.objects.filter(event_type="shipment_activated", reference=self.shipment.reference).exists())
        replay = self.client.post(f"/api/shipments/{self.shipment.id}/activate/", {"client_operation_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(replay.status_code, status.HTTP_200_OK)

    def test_post_picking_lists_creates_no_duplicate_tasks(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        first = self.client.post(f"/api/shipments/{self.shipment.id}/post-picking-lists/", {"client_operation_id": str(uuid.uuid4())}, format="json")
        second = self.client.post(f"/api/shipments/{self.shipment.id}/post-picking-lists/", {"client_operation_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(PickingTask.objects.filter(order_line=self.order_line).count(), 1)

    def test_prepare_blocks_until_control_complete_then_succeeds(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        self.client.post(f"/api/shipments/{self.shipment.id}/post-picking-lists/", {}, format="json")
        blocked = self.client.post(f"/api/shipments/{self.shipment.id}/prepare/", {}, format="json")
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        task = PickingTask.objects.get(order_line=self.order_line)
        task.quantity_picked = task.quantity_to_pick
        task.quantity_prepared = task.quantity_to_pick
        task.status = PickingTask.Status.COMPLETED
        task.save(update_fields=["quantity_picked", "quantity_prepared", "status", "updated_at"])
        prepared = self.client.post(f"/api/shipments/{self.shipment.id}/prepare/", {}, format="json")
        self.assertEqual(prepared.status_code, status.HTTP_200_OK)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.PREPARED)

    def test_cancel_requires_reason_and_preserves_history(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        missing_reason = self.client.post(f"/api/shipments/{self.shipment.id}/cancel/", {"reason": ""}, format="json")
        self.assertEqual(missing_reason.status_code, status.HTTP_400_BAD_REQUEST)
        cancelled = self.client.post(f"/api/shipments/{self.shipment.id}/cancel/", {"reason": "Customer cancelled."}, format="json")
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.CANCELLED)
        self.assertEqual(self.shipment.cancellation_reason, "Customer cancelled.")

    def test_post_documents_is_document_only_without_pallet_or_receiving_release(self):
        transfer = InterBranchTransfer.objects.create(
            reference="IBT-SHP-TEST",
            source_branch=self.branch,
            destination_branch=self.destination_branch,
            status=InterBranchTransfer.Status.DRAFT,
        )
        pallet = TransferPallet.objects.create(transfer=transfer, scan_code="PAL-SHP-TEST", status=TransferPallet.Status.IN_TRANSIT)
        self.shipment.shipment_type = Shipment.ShipmentType.INTER_BRANCH
        self.shipment.inter_branch_transfer = transfer
        self.shipment.status = Shipment.Status.PREPARED
        self.shipment.document_status = Shipment.DocumentStatus.AVAILABLE
        self.shipment.save(update_fields=["shipment_type", "inter_branch_transfer", "status", "document_status", "updated_at"])
        self.client.force_authenticate(user=self.destination_worker)
        forbidden = self.client.post(f"/api/shipments/{self.shipment.id}/post-documents/", {}, format="json")
        self.assertEqual(forbidden.status_code, status.HTTP_404_NOT_FOUND)
        self.login()
        response = self.client.post(f"/api/shipments/{self.shipment.id}/post-documents/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transfer.refresh_from_db()
        pallet.refresh_from_db()
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.document_status, Shipment.DocumentStatus.POSTED)
        self.assertEqual(transfer.status, InterBranchTransfer.Status.DRAFT)
        self.assertEqual(pallet.status, TransferPallet.Status.IN_TRANSIT)
        self.assertIsNone(pallet.released_at)
        self.client.force_authenticate(user=self.destination_worker)
        arrival = self.client.post("/api/scanner/inter-branch-arrivals/", {"pallet_code": "PAL-SHP-TEST"}, format="json")
        self.assertNotEqual(arrival.status_code, status.HTTP_200_OK)

    def test_change_route_records_history_and_rejects_wrong_branch(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        wrong_branch = self.client.post(
            f"/api/shipments/{self.shipment.id}/change-route/",
            {"route_run": self.other_route_run.id},
            format="json",
        )
        self.assertEqual(wrong_branch.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(
            f"/api/shipments/{self.shipment.id}/change-route/",
            {"route_run": self.route_run_2.id, "client_operation_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.shipment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.shipment.route_run, self.route_run_2)
        self.assertEqual(self.order.route_run, self.route_run_2)
        self.assertEqual(ShipmentRouteAssignment.objects.filter(shipment=self.shipment).count(), 1)
        self.assertEqual(ShipmentRouteAssignment.objects.get(shipment=self.shipment).reason, "")

    def test_route_targets_support_today_default_and_weekly_search(self):
        self.login()
        today = self.client.get(
            "/api/shipments/route-targets/",
            {"branch": "SHP", "exclude_route_run": self.route_run.id, "operational_date": timezone.localdate().isoformat()},
        )
        self.assertEqual(today.status_code, status.HTTP_200_OK)
        today_ids = {row["id"] for row in today.data["results"]}
        self.assertIn(self.route_run_today_target.id, today_ids)
        self.assertNotIn(self.route_run_2.id, today_ids)

        week = self.client.get(
            "/api/shipments/route-targets/",
            {
                "branch": "SHP",
                "exclude_route_run": self.route_run.id,
                "operational_date": timezone.localdate().isoformat(),
                "scope": "week",
                "search": "ROUTE-SHP",
            },
        )
        self.assertEqual(week.status_code, status.HTTP_200_OK)
        week_ids = {row["id"] for row in week.data["results"]}
        self.assertIn(self.route_run_today_target.id, week_ids)
        self.assertIn(self.route_run_2.id, week_ids)
        target = next(row for row in week.data["results"] if row["id"] == self.route_run_2.id)
        self.assertEqual(target["operational_identifier"], "ROUTE-SHP")
        self.assertTrue(target["weekday"])

    def test_controlled_status_change_requires_allowed_transition(self):
        self.login()
        blocked = self.client.post(
            f"/api/shipments/{self.shipment.id}/change-status/",
            {"status": Shipment.Status.COMPLETED, "reason": "Bypass attempt."},
            format="json",
        )
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        allowed = self.client.post(
            f"/api/shipments/{self.shipment.id}/change-status/",
            {"status": Shipment.Status.ACTIVE, "reason": "Manual activation."},
            format="json",
        )
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)

    def test_route_monitor_aggregates_assigned_shipments_and_change_route_updates_totals(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        self.client.post(f"/api/shipments/{self.shipment.id}/post-picking-lists/", {}, format="json")
        before = self.client.get("/api/route-runs/", {"branch_code": "SHP"})
        source_row = next(row for row in before.data["results"] if row["id"] == self.route_run.id)
        target_row = next(row for row in before.data["results"] if row["id"] == self.route_run_2.id)
        self.assertEqual(source_row["order_lines_count"], 1)
        self.assertEqual(target_row["order_lines_count"], 0)

        response = self.client.post(
            f"/api/shipments/{self.shipment.id}/change-route/",
            {"route_run": self.route_run_2.id, "client_operation_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        after = self.client.get("/api/route-runs/", {"branch_code": "SHP"})
        source_row = next(row for row in after.data["results"] if row["id"] == self.route_run.id)
        target_row = next(row for row in after.data["results"] if row["id"] == self.route_run_2.id)
        self.assertEqual(source_row["order_lines_count"], 0)
        self.assertEqual(target_row["order_lines_count"], 1)
        self.assertEqual(Shipment.objects.filter(reference=self.shipment.reference).count(), 1)

    def test_close_route_requires_all_shipments_on_route_ready(self):
        second_order = Order.objects.create(branch=self.branch, route_run=self.route_run, external_reference="AX-SHP-002", status=Order.Status.IMPORTED)
        second_line = OrderLine.objects.create(order=second_order, product=self.product, line_number=1, quantity_ordered=Decimal("1"))
        second_shipment = Shipment.objects.create(
            reference="SHP-TEST-002",
            branch=self.branch,
            order=second_order,
            route_run=self.route_run,
            source_system="AX",
            external_reference="AX-SHP-EXT-002",
            status=Shipment.Status.ACTIVE,
        )
        ShipmentLine.objects.create(shipment=second_shipment, order_line=second_line, product=self.product, line_number=1, ordered_quantity=Decimal("1"))
        task = PickingTask.objects.create(
            branch=self.branch,
            order_line=self.order_line,
            source_location=self.location,
            status=PickingTask.Status.COMPLETED,
            quantity_to_pick=Decimal("3"),
            quantity_picked=Decimal("3"),
            quantity_prepared=Decimal("3"),
        )
        self.shipment.status = Shipment.Status.PREPARED
        self.shipment.save(update_fields=["status", "updated_at"])
        self.route_run.status = RouteRun.Status.READY_TO_CLOSE
        self.route_run.documents_printed_at = timezone.now()
        self.route_run.save(update_fields=["status", "documents_printed_at", "updated_at"])
        self.login()
        blocked = self.client.post(f"/api/shipments/{self.shipment.id}/close-route/", {}, format="json")
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        second_task = PickingTask.objects.create(
            branch=self.branch,
            order_line=second_line,
            source_location=self.location,
            status=PickingTask.Status.COMPLETED,
            quantity_to_pick=Decimal("1"),
            quantity_picked=Decimal("1"),
            quantity_prepared=Decimal("1"),
        )
        second_shipment.status = Shipment.Status.PREPARED
        second_shipment.save(update_fields=["status", "updated_at"])
        allowed = self.client.post(f"/api/shipments/{self.shipment.id}/close-route/", {}, format="json")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.route_run.refresh_from_db()
        second_shipment.refresh_from_db()
        self.assertEqual(self.route_run.status, RouteRun.Status.CLOSED)
        self.assertEqual(second_shipment.status, Shipment.Status.COMPLETED)
        self.assertEqual(task.quantity_prepared, Decimal("3"))
        self.assertEqual(second_task.quantity_prepared, Decimal("1"))

    def test_remove_line_quantity_preserves_original_and_updates_effective_quantity(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        line = self.shipment.lines.get()
        response = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{line.id}/remove-quantity/",
            {"quantity": "1", "reason": "Customer requested fewer units.", "client_operation_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.ordered_quantity, Decimal("3"))
        self.assertEqual(line.cancelled_quantity, Decimal("1"))
        self.assertEqual(ShipmentLineQuantityAdjustment.objects.filter(shipment_line=line).count(), 1)
        serialized_line = response.data["shipment"]["lines"][0]
        self.assertEqual(serialized_line["original_ordered_quantity"], "3.000")
        self.assertEqual(serialized_line["effective_quantity"], "2.000")
        self.assertEqual(serialized_line["removed_quantity"], "1.000")

    def test_remove_line_quantity_allows_final_unpicked_unit_without_inventory_or_return_effects(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        line = self.shipment.lines.get()
        task = PickingTask.objects.create(
            branch=self.branch,
            order_line=self.order_line,
            source_location=self.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("3"),
            quantity_picked=Decimal("0"),
            quantity_prepared=Decimal("0"),
        )
        inventory_before = InventoryItem.objects.get(branch=self.branch, location=self.location, product=self.product).quantity_on_hand
        stock_movements_before = StockMovement.objects.count()
        returns_before = ReturnAction.objects.count()
        corrections_before = SalesCorrection.objects.count()
        shortages_before = PickingShortage.objects.count()
        operation_id = str(uuid.uuid4())

        response = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{line.id}/remove-quantity/",
            {"quantity": "3", "reason": "Customer removed remaining units.", "client_operation_id": operation_id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        task.refresh_from_db()
        inventory_after = InventoryItem.objects.get(branch=self.branch, location=self.location, product=self.product).quantity_on_hand
        self.assertEqual(line.ordered_quantity, Decimal("3"))
        self.assertEqual(line.cancelled_quantity, Decimal("3"))
        self.assertEqual(task.quantity_to_pick, Decimal("3"))
        self.assertEqual(task.status, PickingTask.Status.CANCELLED)
        self.assertEqual(response.data["shipment"]["lines"][0]["effective_quantity"], "0.000")
        self.assertEqual(response.data["shipment"]["lines"][0]["service_status"], ShipmentLine.ServiceStatus.CANCELLED)
        self.assertEqual(inventory_after, inventory_before)
        self.assertEqual(StockMovement.objects.count(), stock_movements_before)
        self.assertEqual(ReturnAction.objects.count(), returns_before)
        self.assertEqual(SalesCorrection.objects.count(), corrections_before)
        self.assertEqual(PickingShortage.objects.count(), shortages_before)
        self.assertTrue(AuditLog.objects.filter(event_type="shipment_line_quantity_removed", reference=self.shipment.reference).exists())

        route_response = self.client.get("/api/route-runs/", {"branch_code": "SHP"})
        source_row = next(row for row in route_response.data["results"] if row["id"] == self.route_run.id)
        self.assertEqual(source_row["order_lines_count"], 0)

        replay = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{line.id}/remove-quantity/",
            {"quantity": "3", "reason": "Replay.", "client_operation_id": operation_id},
            format="json",
        )
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(ShipmentLineQuantityAdjustment.objects.filter(shipment_line=line).count(), 1)

        conflict = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{line.id}/remove-quantity/",
            {"quantity": "1", "reason": "Conflicting replay.", "client_operation_id": operation_id},
            format="json",
        )
        self.assertEqual(conflict.status_code, status.HTTP_400_BAD_REQUEST)

        zero_effective = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{line.id}/remove-quantity/",
            {"quantity": "0.001", "reason": "No quantity remains.", "client_operation_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(zero_effective.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_line_quantity_cannot_remove_picked_quantity_or_wrong_parent(self):
        self.login()
        self.shipment.status = Shipment.Status.ACTIVE
        self.shipment.save(update_fields=["status", "updated_at"])
        line = self.shipment.lines.get()
        PickingTask.objects.create(
            branch=self.branch,
            order_line=self.order_line,
            source_location=self.location,
            status=PickingTask.Status.PICKED,
            quantity_to_pick=Decimal("3"),
            quantity_picked=Decimal("2"),
            quantity_prepared=Decimal("0"),
        )
        blocked = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{line.id}/remove-quantity/",
            {"quantity": "2", "reason": "Too much."},
            format="json",
        )
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        other_order = Order.objects.create(branch=self.branch, external_reference="AX-SHP-003", status=Order.Status.IMPORTED)
        other_line = OrderLine.objects.create(order=other_order, product=self.product, line_number=1, quantity_ordered=Decimal("1"))
        other_shipment = Shipment.objects.create(reference="SHP-TEST-003", branch=self.branch, order=other_order, external_reference="AX-SHP-EXT-003")
        other_shipment_line = ShipmentLine.objects.create(
            shipment=other_shipment,
            order_line=other_line,
            product=self.product,
            line_number=1,
            ordered_quantity=Decimal("1"),
        )
        mismatch = self.client.post(
            f"/api/shipments/{self.shipment.id}/lines/{other_shipment_line.id}/remove-quantity/",
            {"quantity": "1", "reason": "Mismatch."},
            format="json",
        )
        self.assertEqual(mismatch.status_code, status.HTTP_400_BAD_REQUEST)

    def test_trusted_localhost_origin_can_post_shipment_action_but_untrusted_origin_is_rejected(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.login(username=self.worker.username, password="demo12345")
        session_response = csrf_client.get("/api/auth/session/")
        token = csrf_client.cookies["csrftoken"].value
        trusted = csrf_client.post(
            f"/api/shipments/{self.shipment.id}/activate/",
            {"client_operation_id": str(uuid.uuid4())},
            format="json",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(session_response.status_code, status.HTTP_200_OK)
        self.assertEqual(trusted.status_code, status.HTTP_200_OK)

        other_order = Order.objects.create(branch=self.branch, external_reference="AX-SHP-004", status=Order.Status.IMPORTED)
        other_line = OrderLine.objects.create(order=other_order, product=self.product, line_number=1, quantity_ordered=Decimal("1"))
        other_shipment = Shipment.objects.create(reference="SHP-TEST-004", branch=self.branch, order=other_order, external_reference="AX-SHP-EXT-004")
        ShipmentLine.objects.create(shipment=other_shipment, order_line=other_line, product=self.product, line_number=1, ordered_quantity=Decimal("1"))
        untrusted = csrf_client.post(
            f"/api/shipments/{other_shipment.id}/activate/",
            {"client_operation_id": str(uuid.uuid4())},
            format="json",
            HTTP_ORIGIN="http://evil.example",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(untrusted.status_code, status.HTTP_403_FORBIDDEN)


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

    def test_authenticated_operational_api_endpoints_reject_anonymous_access(self):
        for path in [
            "/api/products/",
            "/api/locations/",
            "/api/inventory-items/",
            "/api/orders/",
            "/api/route-runs/",
            "/api/stock-movements/",
            "/api/cycle-counts/",
            "/api/inventory-exceptions/",
            "/api/transport-overview/",
            "/api/cycle-count-review-queue/",
            "/api/scanner/proformas/",
            "/api/scanner/cycle-counts/",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

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


class InventoryExceptionSummaryApiTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.worker = User.objects.create_user(username="GDY_EXCEPTION_WORKER", password="demo12345")
        self.leader = User.objects.create_user(username="GDY_EXCEPTION_LEADER", password="demo12345")
        self.other_worker = User.objects.create_user(username="GDA_EXCEPTION_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        UserBranchMembership.objects.create(user=self.other_worker, branch=self.other_branch, role=UserBranchMembership.Role.WORKER)
        self.location = Location.objects.create(
            branch=self.branch,
            code="EX-01",
            name="Exception shelf",
            location_type=Location.LocationType.STORAGE,
        )
        self.unconfirmed = Location.objects.create(
            branch=self.branch,
            code="UNCONFIRMED",
            name="Unconfirmed",
            location_type=Location.LocationType.STORAGE,
        )
        self.other_location = Location.objects.create(
            branch=self.other_branch,
            code="OT-01",
            name="Other shelf",
            location_type=Location.LocationType.STORAGE,
        )
        self.product = Product.objects.create(sku="EX-P1", name="Exception product", barcode="880000000001")
        self.other_product = Product.objects.create(sku="EX-P2", name="Other exception product", barcode="880000000002")

    def create_route_order_task(self, reference="EX-ORDER-1"):
        route = DeliveryRoute.objects.create(branch=self.branch, code=f"R-{reference}", name="Exception route")
        run = RouteRun.objects.create(
            route=route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 0),
            sync_time=time(8, 30),
            departure_time=time(10, 0),
        )
        order = Order.objects.create(branch=self.branch, route_run=run, external_reference=reference)
        line = OrderLine.objects.create(order=order, product=self.product, line_number=1, quantity_ordered=Decimal("2"))
        task = PickingTask.objects.create(
            branch=self.branch,
            order_line=line,
            source_location=self.location,
            quantity_to_pick=Decimal("2"),
        )
        return order, line, task

    def create_transfer_discrepancy(self, reference, source_branch, destination_branch, status_value):
        transfer = InterBranchTransfer.objects.create(
            reference=f"IBT-{reference}",
            source_branch=source_branch,
            destination_branch=destination_branch,
            status=InterBranchTransfer.Status.CLOSED_WITH_DISCREPANCY,
        )
        pallet = TransferPallet.objects.create(
            transfer=transfer,
            scan_code=f"PAL-{reference}",
            status=TransferPallet.Status.CLOSED_WITH_DISCREPANCY,
        )
        return TransferDiscrepancy.objects.create(
            reference=f"DISC-{reference}",
            pallet=pallet,
            transfer=transfer,
            status=status_value,
        )

    def create_cycle_count_review_item(self):
        session = CycleCountSession.objects.create(
            branch=self.branch,
            reference="CC-EX-001",
            status=CycleCountSession.Status.AWAITING_REVIEW,
            snapshot_at=timezone.now(),
        )
        cycle_location = CycleCountLocation.objects.create(
            session=session,
            branch=self.branch,
            location=self.location,
            status=CycleCountLocation.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        return CycleCountLine.objects.create(
            session=session,
            cycle_count_location=cycle_location,
            branch=self.branch,
            location=self.location,
            product=self.product,
            expected_quantity=Decimal("5"),
            counted_quantity=Decimal("4"),
            counted_at=timezone.now(),
            reconciliation_status=CycleCountLine.ReconciliationStatus.PENDING_REVIEW,
        )

    def create_exception_records(self):
        order, _, task = self.create_route_order_task()
        PickingShortage.objects.create(
            picking_task=task,
            order=order,
            branch=self.branch,
            product=self.product,
            reported_location=self.location,
            unconfirmed_location=self.unconfirmed,
            quantity=Decimal("1"),
            confirmation_nonce="shortage-open",
            status=PickingShortage.Status.OPEN,
        )
        closed_shortage = PickingShortage.objects.create(
            picking_task=task,
            order=order,
            branch=self.branch,
            product=self.product,
            reported_location=self.location,
            unconfirmed_location=self.unconfirmed,
            quantity=Decimal("1"),
            confirmation_nonce="shortage-found",
            status=PickingShortage.Status.FOUND,
        )
        ReplenishmentRequest.objects.create(
            picking_shortage=closed_shortage,
            picking_task=task,
            branch=self.branch,
            customer_alias="EX-CUSTOMER",
            order_reference=order.external_reference,
            product=self.product,
            quantity=Decimal("1"),
            status=ReplenishmentRequest.Status.ORDERED_MANUALLY,
        )
        ReplenishmentRequest.objects.create(
            picking_task=task,
            branch=self.branch,
            customer_alias="EX-CUSTOMER",
            order_reference=order.external_reference,
            product=self.product,
            quantity=Decimal("1"),
            status=ReplenishmentRequest.Status.PENDING_ORDER,
        )
        self.create_transfer_discrepancy(
            "OPEN",
            source_branch=self.other_branch,
            destination_branch=self.branch,
            status_value=TransferDiscrepancy.Status.OPEN,
        )
        self.create_transfer_discrepancy(
            "RESOLVED",
            source_branch=self.other_branch,
            destination_branch=self.branch,
            status_value=TransferDiscrepancy.Status.RESOLVED,
        )
        source_review_discrepancy = self.create_transfer_discrepancy(
            "SOURCE",
            source_branch=self.branch,
            destination_branch=self.other_branch,
            status_value=TransferDiscrepancy.Status.CONFIRMED_SHORTAGE,
        )
        TransferDiscrepancySourceReview.objects.create(
            discrepancy=source_review_discrepancy,
            source_branch=self.branch,
            status=TransferDiscrepancySourceReview.Status.PENDING_REVIEW,
        )
        reconciliation_discrepancy = self.create_transfer_discrepancy(
            "RECON",
            source_branch=self.other_branch,
            destination_branch=self.branch,
            status_value=TransferDiscrepancy.Status.CONFIRMED_SHORTAGE,
        )
        reconciliation_review = TransferDiscrepancySourceReview.objects.create(
            discrepancy=reconciliation_discrepancy,
            source_branch=self.other_branch,
            status=TransferDiscrepancySourceReview.Status.COMPLETED,
            finding=TransferDiscrepancySourceReview.Finding.INCONCLUSIVE,
        )
        TransferDiscrepancyReconciliation.objects.create(
            discrepancy=reconciliation_discrepancy,
            source_review=reconciliation_review,
            route=TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION,
            status=TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED,
        )
        source_stock_discrepancy = self.create_transfer_discrepancy(
            "SSTOCK",
            source_branch=self.branch,
            destination_branch=self.other_branch,
            status_value=TransferDiscrepancy.Status.CONFIRMED_SHORTAGE,
        )
        source_stock_review = TransferDiscrepancySourceReview.objects.create(
            discrepancy=source_stock_discrepancy,
            source_branch=self.branch,
            status=TransferDiscrepancySourceReview.Status.COMPLETED,
            finding=TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND,
        )
        source_stock_reconciliation = TransferDiscrepancyReconciliation.objects.create(
            discrepancy=source_stock_discrepancy,
            source_review=source_stock_review,
            route=TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION,
            status=TransferDiscrepancyReconciliation.Status.IN_PROGRESS,
        )
        TransferDiscrepancySourceStockVerification.objects.create(
            reconciliation=source_stock_reconciliation,
            status=TransferDiscrepancySourceStockVerification.Status.PENDING_VERIFICATION,
        )
        self.create_cycle_count_review_item()

    def categories(self, response):
        return {category["key"]: category for category in response.data["categories"]}

    def test_inventory_exception_summary_requires_authentication(self):
        response = self.client.get("/api/inventory-exceptions/summary/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_exception_summary_counts_actionable_statuses_for_leader(self):
        self.create_exception_records()
        self.client.force_authenticate(self.leader)

        response = self.client.get("/api/inventory-exceptions/summary/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        categories = self.categories(response)
        self.assertEqual(categories["picking_shortages"]["count"], 1)
        self.assertEqual(categories["replenishment"]["count"], 1)
        self.assertEqual(categories["transfer_discrepancies"]["count"], 1)
        self.assertEqual(categories["source_reviews"]["count"], 1)
        self.assertEqual(categories["reconciliations"]["count"], 2)
        self.assertEqual(categories["source_stock"]["count"], 1)
        self.assertEqual(categories["cycle_count_review"]["count"], 1)
        self.assertEqual(categories["reconciliations"]["urgent_count"], 1)
        self.assertEqual(response.data["leader_only_count"], 1)
        self.assertGreaterEqual(response.data["total_actionable"], 8)
        self.assertTrue(response.data["immediate_attention"])

    def test_worker_does_not_receive_leader_only_cycle_count_category(self):
        self.create_exception_records()
        self.client.force_authenticate(self.worker)

        response = self.client.get("/api/inventory-exceptions/summary/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        categories = self.categories(response)
        self.assertNotIn("cycle_count_review", categories)
        self.assertEqual(response.data["leader_only_count"], 0)
        self.assertEqual(categories["action_queue"]["urgent_count"], 0)

    def test_inventory_exception_summary_rejects_other_branch_filter(self):
        self.create_exception_records()
        self.client.force_authenticate(self.worker)

        response = self.client.get("/api/inventory-exceptions/summary/", {"branch": "GDA"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_exception_summary_empty_branch_returns_zero_summary(self):
        self.client.force_authenticate(self.leader)

        response = self.client.get("/api/inventory-exceptions/summary/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_actionable"], 0)
        self.assertEqual(response.data["active_categories"], 0)
        self.assertIsNone(response.data["oldest_waiting_since"])


class TransportOverviewApiTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.unrelated_branch = Branch.objects.create(code="WAW", name="Warsaw", city="Warsaw", country="Poland")
        self.worker = User.objects.create_user(username="GDY_TRANSPORT_WORKER", password="demo12345")
        self.other_worker = User.objects.create_user(username="GDA_TRANSPORT_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.other_worker, branch=self.other_branch, role=UserBranchMembership.Role.WORKER)
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="TR-01", name="Morning route")
        self.other_route = DeliveryRoute.objects.create(branch=self.other_branch, code="TR-02", name="Other route")

    def create_route_run(self, status_value, *, route=None, run_number=1):
        return RouteRun.objects.create(
            route=route or self.route,
            service_date=timezone.localdate(),
            run_number=run_number,
            order_cutoff_time=time(8, 0),
            sync_time=time(8, 30),
            departure_time=time(10, 0),
            status=status_value,
            ready_at=timezone.now() if status_value == RouteRun.Status.READY_TO_CLOSE else None,
        )

    def create_transfer(self, reference, source_branch, destination_branch, status_value):
        return InterBranchTransfer.objects.create(
            reference=reference,
            source_branch=source_branch,
            destination_branch=destination_branch,
            status=status_value,
        )

    def create_discrepancy(self, reference, source_branch, destination_branch, status_value):
        transfer = self.create_transfer(
            f"IBT-{reference}",
            source_branch,
            destination_branch,
            InterBranchTransfer.Status.CLOSED_WITH_DISCREPANCY,
        )
        pallet = TransferPallet.objects.create(
            transfer=transfer,
            scan_code=f"PAL-{reference}",
            status=TransferPallet.Status.CLOSED_WITH_DISCREPANCY,
        )
        return TransferDiscrepancy.objects.create(
            reference=f"DISC-{reference}",
            transfer=transfer,
            pallet=pallet,
            status=status_value,
        )

    def create_transit_investigation(self):
        discrepancy = self.create_discrepancy(
            "TRANSIT",
            self.other_branch,
            self.branch,
            TransferDiscrepancy.Status.CONFIRMED_SHORTAGE,
        )
        source_review = TransferDiscrepancySourceReview.objects.create(
            discrepancy=discrepancy,
            source_branch=self.other_branch,
            status=TransferDiscrepancySourceReview.Status.COMPLETED,
            finding=TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES,
        )
        reconciliation = TransferDiscrepancyReconciliation.objects.create(
            discrepancy=discrepancy,
            source_review=source_review,
            route=TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION,
            status=TransferDiscrepancyReconciliation.Status.IN_PROGRESS,
        )
        return TransferDiscrepancyTransitInvestigation.objects.create(
            reconciliation=reconciliation,
            status=TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION,
        )

    def create_transport_records(self):
        self.create_route_run(RouteRun.Status.OPEN, run_number=1)
        self.create_route_run(RouteRun.Status.PICKING, run_number=2)
        self.create_route_run(RouteRun.Status.READY_TO_CLOSE, run_number=3)
        self.create_route_run(RouteRun.Status.CLOSED, run_number=4)
        self.create_route_run(RouteRun.Status.OPEN, route=self.other_route, run_number=1)
        transfer = self.create_transfer(
            "IBT-ACTIVE",
            self.branch,
            self.other_branch,
            InterBranchTransfer.Status.IN_TRANSIT,
        )
        TransferPallet.objects.create(
            transfer=transfer,
            scan_code="PAL-ACTIVE",
            status=TransferPallet.Status.IN_TRANSIT,
        )
        completed_transfer = self.create_transfer(
            "IBT-DONE",
            self.branch,
            self.other_branch,
            InterBranchTransfer.Status.RECEIVED,
        )
        TransferPallet.objects.create(
            transfer=completed_transfer,
            scan_code="PAL-DONE",
            status=TransferPallet.Status.RECEIVED,
        )
        self.create_discrepancy(
            "OPEN",
            self.other_branch,
            self.branch,
            TransferDiscrepancy.Status.OPEN,
        )
        self.create_discrepancy(
            "RESOLVED",
            self.other_branch,
            self.branch,
            TransferDiscrepancy.Status.RESOLVED,
        )
        self.create_transit_investigation()

    def test_transport_overview_requires_authentication(self):
        response = self.client.get("/api/transport-overview/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_transport_overview_counts_branch_scoped_transport_statuses(self):
        self.create_transport_records()
        self.client.force_authenticate(self.worker)

        response = self.client.get("/api/transport-overview/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        self.assertEqual(summary["active_route_runs"], 3)
        self.assertEqual(summary["preparing_route_runs"], 2)
        self.assertEqual(summary["ready_to_close_route_runs"], 1)
        self.assertEqual(summary["transfers_in_transit"], 1)
        self.assertEqual(summary["pallets_awaiting_receipt"], 1)
        self.assertEqual(summary["unresolved_discrepancy_transfers"], 2)
        self.assertEqual(summary["transit_investigations"], 1)
        self.assertEqual([row["status"] for row in response.data["active_routes"]][:1], [RouteRun.Status.READY_TO_CLOSE])
        self.assertTrue(any(item["item_type"] == "route_ready_to_close" for item in response.data["attention_items"]))
        self.assertTrue(any(item["item_type"] == "transit_investigation" for item in response.data["attention_items"]))

    def test_transport_overview_rejects_other_branch_filter(self):
        self.create_transport_records()
        self.client.force_authenticate(self.worker)

        response = self.client.get("/api/transport-overview/", {"branch": "GDA"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_transport_overview_empty_branch_returns_zero_summary(self):
        self.client.force_authenticate(self.worker)

        response = self.client.get("/api/transport-overview/", {"branch": "GDY"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["active_route_runs"], 0)
        self.assertEqual(response.data["summary"]["transfers_in_transit"], 0)
        self.assertEqual(response.data["active_routes"], [])
        self.assertEqual(response.data["attention_items"], [])


class EventRegisterApiTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.user = User.objects.create_user(username="GDY_EVENT_WORKER", password="demo12345")
        self.other_user = User.objects.create_user(username="GDA_EVENT_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.user, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.other_user, branch=self.other_branch, role=UserBranchMembership.Role.WORKER)
        self.product = Product.objects.create(sku="EV-P1", name="Event product", barcode="770000000001")
        self.location = Location.objects.create(
            branch=self.branch,
            code="EV-01",
            name="Event location",
            location_type=Location.LocationType.STORAGE,
        )

    def create_event(self, *, branch=None, event_type="pick", message="Worker GDY_EVENT_WORKER picked stock.", created_at=None):
        event = AuditLog.objects.create(
            actor=self.user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type=event_type,
            branch=branch or self.branch,
            product=self.product,
            quantity=Decimal("2"),
            source_location=self.location,
            reference="EV-REF-001",
            entity_name="StockMovement",
            entity_id="101",
            message=message,
        )
        if created_at is not None:
            AuditLog.objects.filter(pk=event.pk).update(created_at=created_at)
            event.refresh_from_db()
        return event

    def test_current_events_expose_register_presentation_fields(self):
        self.create_event(event_type="stock_adjustment_created")
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/audit-logs/current/", {"branch": "GDY", "event_type": "stock_adjustment_created"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = response.data["results"][0]
        self.assertEqual(event["source"], "current")
        self.assertEqual(event["event_type_label"], "Stock Adjustment Created")
        self.assertEqual(event["event_category"], "Stock Adjustments")
        self.assertTrue(any(item["label"] == "Product" for item in event["metadata"]))
        self.assertTrue(any(link["label"] == "Source location" for link in event["related_links"]))

    def test_archive_requires_date_range_and_uses_same_presentation(self):
        archived_at = timezone.now() - timezone.timedelta(days=45)
        self.create_event(event_type="receive", created_at=archived_at)
        self.client.force_authenticate(self.user)

        missing_dates = self.client.get("/api/audit-logs/archive/", {"branch": "GDY"})
        response = self.client.get(
            "/api/audit-logs/archive/",
            {
                "branch": "GDY",
                "date_from": archived_at.date().isoformat(),
                "date_to": timezone.localdate().isoformat(),
                "event_type": "receive",
            },
        )

        self.assertEqual(missing_dates.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["source"], "archive")
        self.assertEqual(response.data["results"][0]["event_category"], "Receiving")

    def test_event_detail_respects_branch_membership(self):
        event = self.create_event()

        self.client.force_authenticate(self.user)
        allowed = self.client.get(f"/api/audit-logs/{event.id}/")
        self.client.force_authenticate(self.other_user)
        forbidden = self.client.get(f"/api/audit-logs/{event.id}/")

        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

    def test_branch_query_manipulation_is_rejected(self):
        self.create_event()
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/audit-logs/current/", {"branch": "GDA"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StockTransferHistoryApiTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="STH", name="Stock History", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="OTH", name="Other Branch", city="Gdansk", country="Poland")
        self.user = User.objects.create_user(username="STH_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.user, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.product = Product.objects.create(sku="STH-001", name="Stock transfer product", barcode="889900000001")
        self.source = Location.objects.create(
            branch=self.branch,
            code="S-01-01",
            name="Source",
            location_type=Location.LocationType.STORAGE,
        )
        self.destination = Location.objects.create(
            branch=self.branch,
            code="S-02-01",
            name="Destination",
            location_type=Location.LocationType.PICKING,
        )
        self.other_location = Location.objects.create(
            branch=self.other_branch,
            code="O-01-01",
            name="Other",
            location_type=Location.LocationType.STORAGE,
        )
        self.inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.source,
            product=self.product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        self.internal_transfer = StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.source,
            destination_location=self.destination,
            movement_type=StockMovement.MovementType.TRANSFER,
            quantity=Decimal("2"),
            reference="SCANNER-TRANSFER-S-01-01-S-02-01",
            performed_by=self.user,
        )
        StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            destination_location=self.destination,
            movement_type=StockMovement.MovementType.TRANSFER,
            quantity=Decimal("1"),
            reference="PALLET-TRANSFER",
        )
        self.other_transfer = StockMovement.objects.create(
            branch=self.other_branch,
            product=self.product,
            source_location=self.other_location,
            destination_location=self.other_location,
            movement_type=StockMovement.MovementType.TRANSFER,
            quantity=Decimal("1"),
            reference="SCANNER-TRANSFER-OTHER",
        )

    def test_internal_transfer_list_uses_structured_stock_movements(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/stock-movements/",
            {"branch": "STH", "movement_type": "transfer", "internal_transfer": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], self.internal_transfer.id)
        self.assertEqual(row["product_sku"], "STH-001")
        self.assertEqual(row["product_name"], "Stock transfer product")
        self.assertEqual(row["source_location_code"], "S-01-01")
        self.assertEqual(row["destination_location_code"], "S-02-01")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["origin"], "Scanner Quick Transfer")

    def test_stock_movement_list_requires_authentication(self):
        response = self.client.get(
            "/api/stock-movements/",
            {"branch": "STH", "movement_type": "transfer", "internal_transfer": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_internal_transfer_list_is_branch_scoped(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/stock-movements/",
            {"branch": "OTH", "movement_type": "transfer", "internal_transfer": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_internal_transfer_detail_requires_branch_access(self):
        self.client.force_authenticate(self.user)

        forbidden = self.client.get(f"/api/stock-movements/{self.other_transfer.id}/")
        allowed = self.client.get(f"/api/stock-movements/{self.internal_transfer.id}/")

        self.assertEqual(forbidden.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed.data["reference"], "SCANNER-TRANSFER-S-01-01-S-02-01")


class StockAdjustmentHistoryApiTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="ADJ", name="Adjustment Branch", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="AOT", name="Other Adjustment", city="Gdansk", country="Poland")
        self.user = User.objects.create_user(username="ADJ_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.user, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.leader = User.objects.create_user(username="ADJ_LEADER", password="demo12345")
        UserBranchMembership.objects.create(user=self.leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.other_leader = User.objects.create_user(username="AOT_LEADER", password="demo12345")
        UserBranchMembership.objects.create(
            user=self.other_leader,
            branch=self.other_branch,
            role=UserBranchMembership.Role.LEADER,
        )
        self.product = Product.objects.create(sku="ADJ-001", name="Adjustment product", barcode="779900000001")
        self.location = Location.objects.create(
            branch=self.branch,
            code="ADJ-01",
            name="Adjustment location",
            location_type=Location.LocationType.STORAGE,
        )
        self.decrease_location = Location.objects.create(
            branch=self.branch,
            code="ADJ-02",
            name="Decrease location",
            location_type=Location.LocationType.PICKING,
        )
        self.other_location = Location.objects.create(
            branch=self.other_branch,
            code="AOT-01",
            name="Other location",
            location_type=Location.LocationType.STORAGE,
        )
        self.inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        self.increase = StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            destination_location=self.location,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            quantity=Decimal("2"),
            reference="ADJ-INC-001",
            performed_by=self.user,
        )
        self.decrease = StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.decrease_location,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            quantity=Decimal("1"),
            reference="ADJ-DEC-001",
        )
        self.transfer = StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.location,
            destination_location=self.decrease_location,
            movement_type=StockMovement.MovementType.TRANSFER,
            quantity=Decimal("1"),
            reference="TRANSFER-NOT-ADJUSTMENT",
        )
        self.other_adjustment = StockMovement.objects.create(
            branch=self.other_branch,
            product=self.product,
            destination_location=self.other_location,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            quantity=Decimal("1"),
            reference="ADJ-OTHER-001",
        )

    def adjustment_payload(self, **overrides):
        payload = {
            "branch": "ADJ",
            "direction": "increase",
            "location": self.location.id,
            "note": "Manual count correction after shelf recount.",
            "product": self.product.id,
            "quantity": "2",
            "reason_code": StockMovement.AdjustmentReason.COUNT_CORRECTION,
        }
        payload.update(overrides)
        return payload

    def test_stock_adjustment_list_requires_authentication(self):
        response = self.client.get("/api/stock-adjustments/", {"branch": "ADJ"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_stock_adjustment_create_requires_leader_role(self):
        unauthenticated = self.client.post("/api/stock-adjustments/", self.adjustment_payload(), format="json")
        self.client.force_authenticate(self.user)
        worker = self.client.post("/api/stock-adjustments/", self.adjustment_payload(), format="json")
        self.client.force_authenticate(self.other_leader)
        other_leader = self.client.post("/api/stock-adjustments/", self.adjustment_payload(), format="json")

        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(worker.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(other_leader.status_code, status.HTTP_403_FORBIDDEN)

    def test_stock_adjustment_list_shows_only_adjustments_newest_first(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/stock-adjustments/", {"branch": "ADJ"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        references = [row["reference"] for row in response.data["results"]]
        self.assertEqual(references, ["ADJ-DEC-001", "ADJ-INC-001"])
        self.assertNotIn("TRANSFER-NOT-ADJUSTMENT", references)

    def test_stock_adjustment_direction_and_location_fields_are_structured(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/stock-adjustments/", {"branch": "ADJ", "adjustment_direction": "increase"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["reference"], "ADJ-INC-001")
        self.assertEqual(row["adjustment_direction"], "increase")
        self.assertEqual(row["adjustment_location"], self.location.id)
        self.assertEqual(row["adjustment_location_code"], "ADJ-01")
        self.assertEqual(row["origin"], "Adjustment")

    def test_stock_adjustment_filters_by_location_and_worker(self):
        self.client.force_authenticate(self.user)

        by_location = self.client.get("/api/stock-adjustments/", {"branch": "ADJ", "location": "ADJ-02"})
        by_worker = self.client.get("/api/stock-adjustments/", {"branch": "ADJ", "performed_by": "ADJ_WORKER"})

        self.assertEqual(by_location.status_code, status.HTTP_200_OK)
        self.assertEqual([row["reference"] for row in by_location.data["results"]], ["ADJ-DEC-001"])
        self.assertEqual(by_worker.status_code, status.HTTP_200_OK)
        self.assertEqual([row["reference"] for row in by_worker.data["results"]], ["ADJ-INC-001"])

    def test_stock_adjustment_list_is_branch_scoped(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/stock-adjustments/", {"branch": "AOT"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_stock_adjustment_detail_rejects_non_adjustment_and_other_branch(self):
        self.client.force_authenticate(self.user)

        transfer_response = self.client.get(f"/api/stock-adjustments/{self.transfer.id}/")
        other_response = self.client.get(f"/api/stock-adjustments/{self.other_adjustment.id}/")
        allowed_response = self.client.get(f"/api/stock-adjustments/{self.increase.id}/")

        self.assertEqual(transfer_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(other_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(allowed_response.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed_response.data["reference"], "ADJ-INC-001")

    def test_stock_adjustment_increase_updates_inventory_and_records_structured_history(self):
        self.client.force_authenticate(self.leader)

        response = self.client.post("/api/stock-adjustments/", self.adjustment_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("7.000"))
        self.assertEqual(response.data["adjustment_direction"], "increase")
        self.assertEqual(response.data["adjustment_reason"], StockMovement.AdjustmentReason.COUNT_CORRECTION)
        self.assertEqual(response.data["adjustment_reason_label"], "Count correction")
        self.assertEqual(response.data["adjustment_note"], "Manual count correction after shelf recount.")
        self.assertEqual(response.data["quantity_before"], "5.000")
        self.assertEqual(response.data["quantity_after"], "7.000")
        self.assertEqual(response.data["performed_by_username"], "ADJ_LEADER")
        self.assertTrue(response.data["reference"].startswith("ADJ-ADJ-"))
        audit_log = AuditLog.objects.get(entity_name="StockMovement", entity_id=str(response.data["id"]))
        self.assertEqual(audit_log.actor, self.leader)
        self.assertEqual(audit_log.branch, self.branch)
        self.assertEqual(audit_log.product, self.product)
        self.assertEqual(audit_log.quantity, Decimal("2.000"))
        self.assertEqual(audit_log.destination_location, self.location)
        self.assertEqual(audit_log.result, "increase")

    def test_stock_adjustment_decrease_updates_inventory_and_records_before_after(self):
        self.client.force_authenticate(self.leader)

        response = self.client.post(
            "/api/stock-adjustments/",
            self.adjustment_payload(direction="decrease", quantity="3", reason_code=StockMovement.AdjustmentReason.DAMAGED_STOCK),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("2.000"))
        self.assertEqual(response.data["adjustment_direction"], "decrease")
        self.assertEqual(response.data["quantity_before"], "5.000")
        self.assertEqual(response.data["quantity_after"], "2.000")
        movement = StockMovement.objects.get(pk=response.data["id"])
        self.assertEqual(movement.source_location, self.location)
        self.assertIsNone(movement.destination_location)

    def test_stock_adjustment_increase_creates_inventory_row_when_missing(self):
        self.client.force_authenticate(self.leader)
        new_product = Product.objects.create(sku="ADJ-NEW", name="New adjustment product", barcode="779900000002")

        response = self.client.post(
            "/api/stock-adjustments/",
            self.adjustment_payload(product=new_product.id, quantity="4"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        inventory = InventoryItem.objects.get(branch=self.branch, location=self.location, product=new_product)
        self.assertEqual(inventory.quantity_on_hand, Decimal("4.000"))
        self.assertEqual(response.data["quantity_before"], "0.000")
        self.assertEqual(response.data["quantity_after"], "4.000")

    def test_stock_adjustment_validation_rejects_invalid_payloads(self):
        self.client.force_authenticate(self.leader)

        cases = [
            (self.adjustment_payload(quantity="0"), "quantity"),
            (self.adjustment_payload(quantity="-1"), "quantity"),
            (self.adjustment_payload(direction="move"), "direction"),
            (self.adjustment_payload(reason_code="bad_reason"), "reason_code"),
            (self.adjustment_payload(note=""), "note"),
            (self.adjustment_payload(reason_code=StockMovement.AdjustmentReason.OTHER, note="short"), "note"),
            (self.adjustment_payload(product=999999), "product"),
            (self.adjustment_payload(location=999999), "location"),
        ]
        for payload, field in cases:
            response = self.client.post("/api/stock-adjustments/", payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, response.data)

    def test_stock_adjustment_rejects_excessive_decrease_and_cross_branch_location(self):
        self.client.force_authenticate(self.leader)

        excessive = self.client.post(
            "/api/stock-adjustments/",
            self.adjustment_payload(direction="decrease", quantity="99"),
            format="json",
        )
        cross_branch = self.client.post(
            "/api/stock-adjustments/",
            self.adjustment_payload(location=self.other_location.id),
            format="json",
        )
        branch_substitution = self.client.post(
            "/api/stock-adjustments/",
            self.adjustment_payload(branch="AOT"),
            format="json",
        )

        self.assertEqual(excessive.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(cross_branch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(branch_substitution.status_code, status.HTTP_400_BAD_REQUEST)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))

    def test_stock_adjustment_rollback_when_audit_creation_fails(self):
        self.client.force_authenticate(self.leader)

        with patch("operations.viewsets.AuditLog.objects.create", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/stock-adjustments/", self.adjustment_payload(), format="json")

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))
        self.assertFalse(StockMovement.objects.filter(reference__startswith="ADJ-ADJ-").exists())

    def test_stock_adjustment_methods_remain_immutable(self):
        self.client.force_authenticate(self.leader)

        patch_response = self.client.patch(f"/api/stock-adjustments/{self.increase.id}/", {"quantity": "9"}, format="json")
        delete_response = self.client.delete(f"/api/stock-adjustments/{self.increase.id}/")

        self.assertEqual(patch_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(delete_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class CycleCountWorkflowTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="CCB", name="Cycle Branch", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="CCO", name="Other Cycle Branch", city="Gdansk", country="Poland")
        self.leader = User.objects.create_user(username="CCB_LEADER", password="demo12345")
        self.worker = User.objects.create_user(username="CCB_WORKER", password="demo12345")
        self.other_worker = User.objects.create_user(username="CCO_WORKER", password="demo12345")
        self.other_leader = User.objects.create_user(username="CCO_LEADER", password="demo12345")
        UserBranchMembership.objects.create(user=self.leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        UserBranchMembership.objects.create(user=self.worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.other_worker, branch=self.other_branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.other_leader, branch=self.other_branch, role=UserBranchMembership.Role.LEADER)
        self.location = Location.objects.create(branch=self.branch, code="CC-01", name="Count location", location_type=Location.LocationType.STORAGE)
        self.other_location = Location.objects.create(branch=self.other_branch, code="CO-01", name="Other location", location_type=Location.LocationType.STORAGE)
        self.product = Product.objects.create(sku="CC-P1", name="Counted product", barcode="661000000001")
        self.unexpected_product = Product.objects.create(sku="CC-P2", name="Unexpected product", barcode="661000000002")
        self.inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )

    def create_session(self, client=None):
        client = client or self.client
        return client.post(
            "/api/cycle-counts/",
            {"branch": "CCB", "location_ids": [self.location.id], "name": "Test count", "note": "Count shelf."},
            format="json",
        )

    def open_session(self, session_id):
        return self.client.post(f"/api/cycle-counts/{session_id}/open/", {}, format="json")

    def submit_counted_session(self, quantity):
        self.client.force_authenticate(self.leader)
        session_id = self.create_session().data["id"]
        self.open_session(session_id)
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/count/",
            {"product_code": self.product.sku, "quantity": str(quantity)},
            format="json",
        )
        self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/submit/",
            {"confirm_zeroes": False},
            format="json",
        )
        self.client.force_authenticate(self.leader)
        return CycleCountSession.objects.get(pk=session_id), CycleCountLine.objects.get(session_id=session_id, product=self.product)

    def test_cycle_count_create_requires_branch_leader_and_locations(self):
        unauthenticated = self.create_session()
        self.client.force_authenticate(self.worker)
        worker = self.create_session()
        self.client.force_authenticate(self.leader)
        empty = self.client.post("/api/cycle-counts/", {"branch": "CCB", "location_ids": []}, format="json")
        duplicate = self.client.post("/api/cycle-counts/", {"branch": "CCB", "location_ids": [self.location.id, self.location.id]}, format="json")
        cross_branch = self.client.post("/api/cycle-counts/", {"branch": "CCB", "location_ids": [self.other_location.id]}, format="json")
        created = self.create_session()

        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(worker.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(empty.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(cross_branch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["status"], CycleCountSession.Status.DRAFT)

    def test_open_creates_stable_expected_snapshot_once(self):
        self.client.force_authenticate(self.leader)
        created = self.create_session()
        session_id = created.data["id"]

        first = self.open_session(session_id)
        self.inventory.quantity_on_hand = Decimal("9")
        self.inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        second = self.open_session(session_id)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(CycleCountLine.objects.filter(session_id=session_id).count(), 1)
        line = CycleCountLine.objects.get(session_id=session_id, product=self.product)
        self.assertEqual(line.expected_quantity, Decimal("5.000"))
        self.assertEqual(CycleCountSession.objects.get(pk=session_id).status, CycleCountSession.Status.OPEN)

    def test_scanner_worker_counts_expected_and_unexpected_without_inventory_change(self):
        self.client.force_authenticate(self.leader)
        session_id = self.create_session().data["id"]
        self.open_session(session_id)
        self.client.force_authenticate(self.worker)

        available = self.client.get("/api/scanner/cycle-counts/", {"branch": "CCB"})
        detail = self.client.get(f"/api/scanner/cycle-counts/{session_id}/")
        expected = self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/count/",
            {"product_code": self.product.sku, "quantity": "4"},
            format="json",
        )
        unexpected = self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/count/",
            {"product_code": self.unexpected_product.sku, "quantity": "2"},
            format="json",
        )

        self.assertEqual(available.status_code, status.HTTP_200_OK)
        self.assertEqual(len(available.data["results"]), 1)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertNotIn("expected_quantity", detail.data["locations"][0]["lines"][0] if detail.data["locations"][0]["lines"] else {})
        self.assertEqual(expected.status_code, status.HTTP_200_OK)
        self.assertEqual(unexpected.status_code, status.HTTP_200_OK)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))
        unexpected_line = CycleCountLine.objects.get(session_id=session_id, product=self.unexpected_product)
        self.assertFalse(unexpected_line.is_expected)
        self.assertEqual(unexpected_line.expected_quantity, Decimal("0.000"))

    def test_submit_requires_zero_confirmation_and_moves_to_review(self):
        self.client.force_authenticate(self.leader)
        session_id = self.create_session().data["id"]
        self.open_session(session_id)
        self.client.force_authenticate(self.worker)

        blocked = self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/submit/",
            {"confirm_zeroes": False},
            format="json",
        )
        submitted = self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/submit/",
            {"confirm_zeroes": True},
            format="json",
        )

        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)
        session = CycleCountSession.objects.get(pk=session_id)
        self.assertEqual(session.status, CycleCountSession.Status.AWAITING_REVIEW)
        line = CycleCountLine.objects.get(session=session, product=self.product)
        self.assertEqual(line.counted_quantity, Decimal("0.000"))
        self.assertTrue(AuditLog.objects.filter(event_type="cycle_count_location_submitted").exists())

    def test_close_does_not_change_inventory_or_create_adjustment(self):
        self.client.force_authenticate(self.leader)
        session_id = self.create_session().data["id"]
        self.open_session(session_id)
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/count/",
            {"product_code": self.product.sku, "quantity": "5"},
            format="json",
        )
        self.client.post(
            f"/api/scanner/cycle-counts/{session_id}/locations/{self.location.id}/submit/",
            {"confirm_zeroes": False},
            format="json",
        )
        self.client.force_authenticate(self.leader)

        closed = self.client.post(f"/api/cycle-counts/{session_id}/close/", {}, format="json")

        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.assertEqual(closed.data["status"], CycleCountSession.Status.CLOSED)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.ADJUSTMENT).exists())

    def test_cycle_count_reconciliation_requires_branch_leader(self):
        session, line = self.submit_counted_session("7")
        url = f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/"

        self.client.force_authenticate(None)
        unauthenticated = self.client.post(url, {}, format="json")
        self.client.force_authenticate(self.worker)
        worker = self.client.post(url, {}, format="json")
        self.client.force_authenticate(self.other_leader)
        other_leader = self.client.post(url, {}, format="json")

        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(worker.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(other_leader.status_code, status.HTTP_403_FORBIDDEN)

    def test_apply_positive_cycle_count_variance_creates_linked_adjustment_once(self):
        session, line = self.submit_counted_session("7")

        first = self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/",
            {"note": "Shelf recount confirmed the surplus."},
            format="json",
        )
        duplicate = self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/",
            {"note": "Duplicate click."},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("7.000"))
        line.refresh_from_db()
        self.assertEqual(line.reconciliation_status, CycleCountLine.ReconciliationStatus.ADJUSTMENT_APPLIED)
        self.assertEqual(line.reconciliation_stock_movement.adjustment_direction, StockMovement.AdjustmentDirection.INCREASE)
        self.assertEqual(line.reconciliation_stock_movement.adjustment_reason, StockMovement.AdjustmentReason.COUNT_CORRECTION)
        self.assertEqual(line.reconciliation_stock_movement.quantity_before, Decimal("5.000"))
        self.assertEqual(line.reconciliation_stock_movement.quantity_after, Decimal("7.000"))
        self.assertEqual(StockMovement.objects.filter(cycle_count_line=line).count(), 1)
        self.assertTrue(AuditLog.objects.filter(event_type="cycle_count_variance_adjustment_applied").exists())

    def test_apply_negative_cycle_count_variance_decreases_stock(self):
        session, line = self.submit_counted_session("3")

        response = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("3.000"))
        movement = StockMovement.objects.get(cycle_count_line=line)
        self.assertEqual(movement.adjustment_direction, StockMovement.AdjustmentDirection.DECREASE)
        self.assertEqual(movement.quantity, Decimal("2.000"))

    def test_apply_rejects_zero_unsubmitted_stale_and_changed_stock(self):
        zero_session, zero_line = self.submit_counted_session("5")
        zero = self.client.post(f"/api/cycle-counts/{zero_session.id}/lines/{zero_line.id}/apply-adjustment/", {}, format="json")
        self.assertEqual(zero.status_code, status.HTTP_400_BAD_REQUEST)

        session, line = self.submit_counted_session("7")
        StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.location,
            movement_type=StockMovement.MovementType.PICK,
            quantity=Decimal("1"),
            reference="POST-SNAPSHOT",
        )
        stale = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        self.assertEqual(stale.status_code, status.HTTP_409_CONFLICT)

        changed_session, changed_line = self.submit_counted_session("7")
        self.inventory.quantity_on_hand = Decimal("6")
        self.inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        changed = self.client.post(
            f"/api/cycle-counts/{changed_session.id}/lines/{changed_line.id}/apply-adjustment/",
            {},
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_409_CONFLICT)

    def test_resolve_without_adjustment_requires_note_and_does_not_change_inventory(self):
        session, line = self.submit_counted_session("7")

        missing_note = self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/resolve-without-adjustment/",
            {"note": ""},
            format="json",
        )
        resolved = self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/resolve-without-adjustment/",
            {"note": "Variance explained by timing during the count."},
            format="json",
        )
        apply_after = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")

        self.assertEqual(missing_note.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resolved.status_code, status.HTTP_200_OK)
        self.assertEqual(apply_after.status_code, status.HTTP_409_CONFLICT)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))
        self.assertFalse(StockMovement.objects.filter(cycle_count_line=line).exists())
        line.refresh_from_db()
        self.assertEqual(line.reconciliation_status, CycleCountLine.ReconciliationStatus.NO_ADJUSTMENT_REQUIRED)
        self.assertEqual(line.resolution_note, "Variance explained by timing during the count.")
        self.assertTrue(AuditLog.objects.filter(event_type="cycle_count_variance_resolved_without_adjustment").exists())

    def test_unresolved_variance_blocks_close_and_resolved_variance_allows_close_without_extra_adjustment(self):
        session, line = self.submit_counted_session("7")

        blocked = self.client.post(f"/api/cycle-counts/{session.id}/close/", {}, format="json")
        self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        movement_count = StockMovement.objects.count()
        closed = self.client.post(f"/api/cycle-counts/{session.id}/close/", {}, format="json")

        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.assertEqual(closed.data["status"], CycleCountSession.Status.CLOSED)
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_recount_request_requires_leader_reason_and_blocks_original_reconciliation(self):
        session, line = self.submit_counted_session("7")
        url = f"/api/cycle-counts/{session.id}/lines/{line.id}/request-recount/"

        missing_reason = self.client.post(url, {"reason": ""}, format="json")
        self.client.force_authenticate(self.worker)
        worker = self.client.post(url, {"reason": "Worker cannot request this."}, format="json")
        self.client.force_authenticate(self.other_leader)
        other_leader = self.client.post(url, {"reason": "Other branch cannot request this."}, format="json")
        self.client.force_authenticate(self.leader)
        created = self.client.post(url, {"reason": "Movement conflict requires second physical count."}, format="json")
        duplicate = self.client.post(url, {"reason": "Duplicate request."}, format="json")
        apply_blocked = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")

        self.assertEqual(missing_reason.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(worker.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(other_leader.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(apply_blocked.status_code, status.HTTP_409_CONFLICT)
        recount = CycleCountRecount.objects.get(original_line=line)
        self.assertEqual(recount.baseline_quantity, Decimal("5.000"))
        self.assertTrue(AuditLog.objects.filter(event_type="cycle_count_recount_requested").exists())

    def test_scanner_recount_is_blind_and_submission_is_immutable_without_inventory_mutation(self):
        session, line = self.submit_counted_session("7")
        self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/request-recount/",
            {"reason": "Verify the shelf result."},
            format="json",
        )
        recount = CycleCountRecount.objects.get(original_line=line)
        self.client.force_authenticate(self.worker)

        listing = self.client.get("/api/scanner/cycle-count-recounts/", {"branch": "CCB"})
        detail = self.client.get(f"/api/scanner/cycle-count-recounts/{recount.id}/")
        wrong_location = self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": "BAD", "product_code": self.product.sku, "quantity": "6"},
            format="json",
        )
        wrong_product = self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.unexpected_product.sku, "quantity": "6"},
            format="json",
        )
        negative = self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "-1"},
            format="json",
        )
        submitted = self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.barcode, "quantity": "6"},
            format="json",
        )
        duplicate = self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "4"},
            format="json",
        )

        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(len(listing.data["results"]), 1)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertNotIn("original_counted_quantity", detail.data)
        self.assertNotIn("baseline_quantity", detail.data)
        self.assertEqual(wrong_location.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(negative.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)
        self.assertEqual(duplicate.status_code, status.HTTP_200_OK)
        recount.refresh_from_db()
        self.assertEqual(recount.counted_quantity, Decimal("6.000"))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))
        self.assertTrue(AuditLog.objects.filter(event_type="cycle_count_recount_submitted").exists())

    def test_leader_accepts_recount_then_adjustment_uses_recount_baseline(self):
        session, line = self.submit_counted_session("7")
        self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/request-recount/",
            {"reason": "Original count needs physical verification."},
            format="json",
        )
        recount = CycleCountRecount.objects.get(original_line=line)
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "6"},
            format="json",
        )
        self.client.force_authenticate(self.leader)

        accepted = self.client.post(f"/api/cycle-counts/{session.id}/recounts/{recount.id}/accept/", {"note": "Recount accepted."}, format="json")
        movement_count = StockMovement.objects.count()
        applied = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")

        self.assertEqual(accepted.status_code, status.HTTP_200_OK)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("6.000"))
        self.assertEqual(StockMovement.objects.count(), movement_count + 1)
        self.assertEqual(applied.status_code, status.HTTP_200_OK)
        movement = StockMovement.objects.get(cycle_count_line=line)
        self.assertEqual(movement.cycle_count_recount_id, recount.id)
        self.assertEqual(movement.quantity_before, Decimal("5.000"))
        self.assertEqual(movement.quantity_after, Decimal("6.000"))
        line.refresh_from_db()
        self.assertEqual(line.counted_quantity, Decimal("7.000"))
        self.assertEqual(line.reconciliation_status, CycleCountLine.ReconciliationStatus.ADJUSTMENT_APPLIED)

    def test_stale_recount_acceptance_and_close_are_blocked_until_cancelled_or_resolved(self):
        session, line = self.submit_counted_session("7")
        self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/request-recount/",
            {"reason": "Check stale variance."},
            format="json",
        )
        recount = CycleCountRecount.objects.get(original_line=line)
        StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.location,
            movement_type=StockMovement.MovementType.PICK,
            quantity=Decimal("1"),
            reference="RECOUNT-WINDOW",
        )
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "6"},
            format="json",
        )
        self.client.force_authenticate(self.leader)

        close_blocked = self.client.post(f"/api/cycle-counts/{session.id}/close/", {}, format="json")
        accept_blocked = self.client.post(f"/api/cycle-counts/{session.id}/recounts/{recount.id}/accept/", {}, format="json")
        cancelled = self.client.post(f"/api/cycle-counts/{session.id}/recounts/{recount.id}/cancel/", {"note": "Movement occurred."}, format="json")

        self.assertEqual(close_blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(accept_blocked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        recount.refresh_from_db()
        self.assertEqual(recount.status, CycleCountRecount.Status.CANCELLED)
        self.assertTrue(recount.movement_after_baseline)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("5.000"))

    def get_cycle_count_review_queue(self, **params):
        query = {"branch": "CCB", **params}
        return self.client.get("/api/cycle-count-review-queue/", query)

    def test_cycle_count_review_queue_requires_branch_leader_and_is_branch_scoped(self):
        self.submit_counted_session("7")

        self.client.force_authenticate(None)
        unauthenticated = self.get_cycle_count_review_queue()
        self.client.force_authenticate(self.worker)
        worker = self.get_cycle_count_review_queue()
        self.client.force_authenticate(self.other_leader)
        other_branch = self.client.get("/api/cycle-count-review-queue/", {"branch": "CCO"})
        other_forbidden = self.get_cycle_count_review_queue()
        self.client.force_authenticate(self.leader)
        leader = self.get_cycle_count_review_queue()

        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(worker.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(other_branch.status_code, status.HTTP_200_OK)
        self.assertEqual(other_branch.data["count"], 0)
        self.assertEqual(other_forbidden.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(leader.status_code, status.HTTP_200_OK)
        self.assertEqual(leader.data["summary"]["variance_pending_review"], 1)

    def test_cycle_count_review_queue_categories_summary_and_actions(self):
        stale_session, stale_line = self.submit_counted_session("8")
        StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.location,
            movement_type=StockMovement.MovementType.PICK,
            quantity=Decimal("1"),
            reference="QUEUE-STALE",
        )
        pending_session, pending_line = self.submit_counted_session("7")

        requested_session, requested_line = self.submit_counted_session("6")
        self.client.post(
            f"/api/cycle-counts/{requested_session.id}/lines/{requested_line.id}/request-recount/",
            {"reason": "Queue requested recount."},
            format="json",
        )

        submitted_session, submitted_line = self.submit_counted_session("6")
        self.client.post(
            f"/api/cycle-counts/{submitted_session.id}/lines/{submitted_line.id}/request-recount/",
            {"reason": "Queue submitted recount."},
            format="json",
        )
        submitted_recount = CycleCountRecount.objects.get(original_line=submitted_line)
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-count-recounts/{submitted_recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "6"},
            format="json",
        )
        self.client.force_authenticate(self.leader)

        accepted_session, accepted_line = self.submit_counted_session("6")
        self.client.post(
            f"/api/cycle-counts/{accepted_session.id}/lines/{accepted_line.id}/request-recount/",
            {"reason": "Queue accepted recount."},
            format="json",
        )
        accepted_recount = CycleCountRecount.objects.get(original_line=accepted_line)
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-count-recounts/{accepted_recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "6"},
            format="json",
        )
        self.client.force_authenticate(self.leader)
        self.client.post(f"/api/cycle-counts/{accepted_session.id}/recounts/{accepted_recount.id}/accept/", {}, format="json")

        ready_session, _ = self.submit_counted_session("5")

        response = self.get_cycle_count_review_queue()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        self.assertEqual(summary["variance_pending_review"], 1)
        self.assertGreaterEqual(summary["stale_variance"], 1)
        self.assertEqual(summary["recount_requested"], 1)
        self.assertEqual(summary["recount_waiting_review"], 1)
        self.assertEqual(summary["accepted_recount_pending_reconciliation"], 1)
        self.assertEqual(summary["session_waiting_close"], 1)
        rows = response.data["results"]
        priorities = [row["priority"] for row in rows]
        self.assertEqual(priorities, sorted(priorities))
        pending_row = next(row for row in rows if row["line"] == pending_line.id)
        self.assertIn("apply_adjustment", pending_row["valid_actions"])
        stale_row = next(row for row in rows if row["line"] == stale_line.id)
        self.assertEqual(stale_row["item_type"], "stale_variance")
        self.assertNotIn("apply_adjustment", stale_row["valid_actions"])
        self.assertIn("request_recount", stale_row["valid_actions"])
        ready_row = next(row for row in rows if row["session"] == ready_session.id and row["item_type"] == "session_waiting_close")
        self.assertIn("close_session", ready_row["valid_actions"])

    def test_cycle_count_review_queue_filters_and_pagination_use_all_matching_summary(self):
        self.submit_counted_session("7")
        self.submit_counted_session("8")

        filtered = self.get_cycle_count_review_queue(item_type="variance_pending_review", page_size="1")
        search = self.get_cycle_count_review_queue(search="CC-P1")
        missing = self.get_cycle_count_review_queue(product="NOPE")

        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(len(filtered.data["results"]), 1)
        self.assertEqual(filtered.data["summary"]["variance_pending_review"], 2)
        self.assertEqual(filtered.data["count"], 2)
        self.assertEqual(search.data["summary"]["total"], 2)
        self.assertEqual(missing.data["summary"]["total"], 0)

    def test_other_branch_user_cannot_view_or_count(self):
        self.client.force_authenticate(self.leader)
        session_id = self.create_session().data["id"]
        self.open_session(session_id)
        self.client.force_authenticate(self.other_worker)

        list_response = self.client.get("/api/cycle-counts/", {"branch": "CCB"})
        scanner_detail = self.client.get(f"/api/scanner/cycle-counts/{session_id}/")

        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(scanner_detail.status_code, status.HTTP_403_FORBIDDEN)


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
        self.user = create_branch_user("TST_WORKER", self.branch)
        self.client.force_authenticate(self.user)

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
        self.user = create_branch_user("SCN_WORKER", self.branch)
        self.client.force_authenticate(self.user)

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
        self.user = create_branch_user("CNT_WORKER", self.branch)
        self.client.force_authenticate(self.user)

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
        self.client.force_authenticate(self.worker)
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
        self.worker = User.objects.create_superuser(
            username="WORKER-1",
            password="demo12345",
            email="receiving@example.com",
        )
        self.final_leader = User.objects.create_superuser(
            username="FINAL-LEADER",
            password="demo12345",
            email="final-leader@example.com",
        )
        self.client.force_authenticate(self.worker)

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
        self.client.force_authenticate(self.final_leader)
        response = self.client.post(
            f"/api/transfer-discrepancies/{discrepancy.id}/confirm-shortage/",
            {
                "product_code": product_code,
                "quantity": quantity,
                "worker_code": "FINAL-LEADER",
                "client_operation_id": operation_id,
            },
            format="json",
        )
        self.client.force_authenticate(self.worker)
        return response

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
        self.client.force_authenticate(self.final_leader)
        return self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/complete-manual/",
            {
                "outcome": outcome,
                "decision_note": note,
                "worker_code": "FINAL-LEADER",
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
        self.assertEqual(discrepancy.confirmed_shortage_by_worker_code, "FINAL-LEADER")
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
        self.assertEqual(reconciliation.completed_by_worker_code, "FINAL-LEADER")
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
        self.assertEqual(manual_event["actor_display"], "WORKER-1")
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


class CriticalInterBranchReceivingFixtureMixin:
    def setUp(self):
        self.source_branch = Branch.objects.create(code="GDA", name="Gdansk", city="Gdansk", country="Poland")
        self.destination_branch = Branch.objects.create(code="GDY", name="Gdynia", city="Gdynia", country="Poland")
        self.unrelated_branch = Branch.objects.create(code="WAW", name="Warsaw", city="Warsaw", country="Poland")
        self.destination_location = Location.objects.create(
            branch=self.destination_branch,
            code="GDY-A-01",
            name="Gdynia A-01",
            location_type=Location.LocationType.STORAGE,
        )
        self.second_destination_location = Location.objects.create(
            branch=self.destination_branch,
            code="GDY-B-01",
            name="Gdynia B-01",
            location_type=Location.LocationType.STORAGE,
        )
        self.source_location = Location.objects.create(
            branch=self.source_branch,
            code="GDA-SRC-01",
            name="Gdansk source",
            location_type=Location.LocationType.STORAGE,
        )
        self.unrelated_location = Location.objects.create(
            branch=self.unrelated_branch,
            code="WAW-A-01",
            name="Warsaw A-01",
            location_type=Location.LocationType.STORAGE,
        )
        self.product = Product.objects.create(
            sku="IBT-FILTR-001",
            name="Critical filter",
            barcode="590100000001",
            unit_of_measure="pcs",
        )
        self.second_product = Product.objects.create(
            sku="IBT-OLEJ-001",
            name="Critical oil",
            barcode="590100000002",
            unit_of_measure="pcs",
        )
        self.unexpected_product = Product.objects.create(
            sku="IBT-OTHER-001",
            name="Unexpected product",
            barcode="590100000099",
            unit_of_measure="pcs",
        )
        self.destination_worker = create_branch_user("CRIT_GDY_WORKER", self.destination_branch)
        self.destination_leader = create_branch_user(
            "CRIT_GDY_LEADER",
            self.destination_branch,
            role=UserBranchMembership.Role.LEADER,
        )
        self.source_worker = create_branch_user("CRIT_GDA_WORKER", self.source_branch)
        self.unrelated_worker = create_branch_user("CRIT_WAW_WORKER", self.unrelated_branch)

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_in_transit_pallet(self, *, reference, pallet_code, first_quantity="3", second_quantity="2"):
        transfer = InterBranchTransfer.objects.create(
            reference=reference,
            source_branch=self.source_branch,
            destination_branch=self.destination_branch,
            status=InterBranchTransfer.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        pallet = TransferPallet.objects.create(
            transfer=transfer,
            scan_code=pallet_code,
            status=TransferPallet.Status.IN_TRANSIT,
            released_at=timezone.now(),
        )
        TransferPalletItem.objects.create(
            pallet=pallet,
            product=self.product,
            expected_quantity=Decimal(first_quantity),
        )
        TransferPalletItem.objects.create(
            pallet=pallet,
            product=self.second_product,
            expected_quantity=Decimal(second_quantity),
        )
        InventoryItem.objects.create(
            branch=self.source_branch,
            location=self.source_location,
            product=self.product,
            quantity_on_hand=Decimal("20"),
            quantity_reserved=Decimal("0"),
        )
        InventoryItem.objects.create(
            branch=self.source_branch,
            location=self.source_location,
            product=self.second_product,
            quantity_on_hand=Decimal("20"),
            quantity_reserved=Decimal("0"),
        )
        StockMovement.objects.create(
            branch=self.source_branch,
            product=self.product,
            source_location=self.source_location,
            movement_type=StockMovement.MovementType.TRANSFER,
            quantity=Decimal(first_quantity),
            reference=reference,
        )
        return transfer, pallet

    def confirm_arrival(self, pallet):
        return self.client.post(
            "/api/scanner/inter-branch-arrivals/",
            {
                "pallet_code": pallet.scan_code,
                "worker_code": self.destination_worker.username,
                "client_operation_id": f"arrival-{pallet.scan_code.lower()}",
            },
            format="json",
        )

    def start_receiving(self, pallet):
        return self.client.post(
            "/api/scanner/receiving/start/",
            {"pallet_code": pallet.scan_code, "worker_code": self.destination_worker.username},
            format="json",
        )

    def scan_product(self, session_id, product_code, quantity):
        return self.client.post(
            "/api/scanner/receiving/scan-product/",
            {"receiving_session_id": session_id, "product_code": product_code, "quantity": str(quantity)},
            format="json",
        )

    def put_away(self, session_id, location_code):
        return self.client.post(
            "/api/scanner/receiving/put-away/",
            {"receiving_session_id": session_id, "location_code": location_code},
            format="json",
        )

    def close_receiving(self, session_id):
        return self.client.post(
            "/api/scanner/receiving/close/",
            {"receiving_session_id": session_id},
            format="json",
        )

    def complete_receiving_alias(self, session_id):
        return self.client.post(
            "/api/scanner/receiving/complete/",
            {"receiving_session_id": session_id},
            format="json",
        )

    def receive_line(self, session_id, product_code, quantity, location_code):
        scan = self.scan_product(session_id, product_code, quantity)
        self.assertEqual(scan.status_code, status.HTTP_200_OK)
        put_away = self.put_away(session_id, location_code)
        self.assertEqual(put_away.status_code, status.HTTP_200_OK)
        return put_away

    def paged_results(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"] if isinstance(response.data, dict) and "results" in response.data else response.data

    def inventory_quantity(self, product, location):
        return InventoryItem.objects.get(
            branch=self.destination_branch,
            location=location,
            product=product,
        ).quantity_on_hand


class CriticalInterBranchExactReceivingIntegrationTests(CriticalInterBranchReceivingFixtureMixin, APITestCase):
    def test_exact_receiving_closes_without_discrepancy_and_updates_read_models(self):
        transfer, pallet = self.create_in_transit_pallet(
            reference="IBT-CRIT-EXACT-001",
            pallet_code="PAL-CRIT-EXACT-001",
        )
        self.authenticate(self.unrelated_worker)
        unrelated_arrivals = self.client.get("/api/scanner/inter-branch-arrivals/", {"branch": self.destination_branch.code})
        self.assertEqual(unrelated_arrivals.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.destination_worker)
        arrival = self.confirm_arrival(pallet)
        repeated_arrival = self.confirm_arrival(pallet)
        self.assertEqual(arrival.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated_arrival.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated_arrival.data["arrival"]["arrival_result"], "already_registered")
        self.assertEqual(TransferPalletArrival.objects.filter(pallet=pallet).count(), 1)
        mm_tasks = self.paged_results(self.client.get("/api/mm-tasks/", {"branch": self.destination_branch.code}))
        self.assertEqual([row["pallet_code"] for row in mm_tasks], [pallet.scan_code])

        started = self.start_receiving(pallet)
        self.assertEqual(started.status_code, status.HTTP_200_OK)
        session_id = started.data["receiving_session"]["id"]
        self.assertEqual(started.data["receiving_session"]["summary"]["remaining_quantity"], 5)

        invalid_product = self.scan_product(session_id, self.unexpected_product.sku, "1")
        self.assertEqual(invalid_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PalletReceivingScan.objects.filter(pallet=pallet).count(), 0)
        self.assertEqual(StockMovement.objects.filter(branch=self.destination_branch, reference=pallet.scan_code).count(), 0)

        pending = self.scan_product(session_id, self.product.sku, "3")
        self.assertEqual(pending.status_code, status.HTTP_200_OK)
        wrong_branch_location = self.put_away(session_id, self.source_location.code)
        self.assertEqual(wrong_branch_location.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PalletReceivingScan.objects.filter(pallet=pallet).count(), 0)
        self.assertFalse(
            InventoryItem.objects.filter(
                branch=self.destination_branch,
                product=self.product,
                quantity_on_hand__gt=0,
            ).exists()
        )
        self.assertEqual(self.put_away(session_id, self.destination_location.code).status_code, status.HTTP_200_OK)
        self.receive_line(session_id, self.second_product.barcode, "2", self.second_destination_location.code)

        closed = self.close_receiving(session_id)
        duplicate_close = self.close_receiving(session_id)
        alias_after_close = self.complete_receiving_alias(session_id)

        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.assertEqual(closed.data["result"], "exact")
        self.assertEqual(duplicate_close.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(alias_after_close.status_code, status.HTTP_400_BAD_REQUEST)
        pallet.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(pallet.status, TransferPallet.Status.RECEIVED)
        self.assertEqual(transfer.status, InterBranchTransfer.Status.RECEIVED)
        self.assertFalse(TransferDiscrepancy.objects.filter(pallet=pallet).exists())
        self.assertEqual(self.inventory_quantity(self.product, self.destination_location), Decimal("3.000"))
        self.assertEqual(self.inventory_quantity(self.second_product, self.second_destination_location), Decimal("2.000"))
        self.assertEqual(PalletReceivingScan.objects.filter(pallet=pallet).count(), 2)
        self.assertEqual(
            StockMovement.objects.filter(
                branch=self.destination_branch,
                reference=pallet.scan_code,
                movement_type=StockMovement.MovementType.TRANSFER,
            ).count(),
            2,
        )
        self.assertEqual(AuditLog.objects.filter(event_type="mm_task_completed", pallet=pallet).count(), 1)
        self.assertEqual(self.client.get("/api/mm-tasks/", {"branch": self.destination_branch.code}).data["results"], [])

        overview = self.client.get("/api/transport-overview/", {"branch": self.destination_branch.code})
        self.assertEqual(overview.status_code, status.HTTP_200_OK)
        self.assertEqual(overview.data["summary"]["pallets_awaiting_receipt"], 0)
        self.assertEqual(overview.data["summary"]["unresolved_discrepancy_transfers"], 0)

        contents = self.client.get("/api/scanner/contents/", {"code": pallet.scan_code})
        self.assertEqual(contents.status_code, status.HTTP_200_OK)
        self.assertEqual(contents.data["object_type"], "pallet")
        self.assertEqual(contents.data["status"], TransferPallet.Status.RECEIVED)
        self.assertIsNone(contents.data["discrepancy_reference"])
        self.assertEqual(
            {item["sku"]: item["received_quantity"] for item in contents.data["items"]},
            {self.product.sku: 3, self.second_product.sku: 2},
        )

        events = self.client.get("/api/current-events/", {"branch": self.destination_branch.code, "search": pallet.scan_code})
        event_types = {event["event_type"] for event in events.data["results"]}
        self.assertIn("inter_branch_arrival", event_types)
        self.assertIn("receive_scan", event_types)
        self.assertIn("mm_task_completed", event_types)

        self.authenticate(self.source_worker)
        source_current = self.client.get("/api/scanner/receiving/current/", {"receiving_session_id": session_id})
        self.assertEqual(source_current.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            any(
                row["pallet_code"] == pallet.scan_code
                for row in self.client.get("/api/mm-tasks/", {"branch": self.source_branch.code}).data["results"]
            )
        )


class CriticalInterBranchShortageIntegrationTests(CriticalInterBranchReceivingFixtureMixin, APITestCase):
    def test_shortage_receiving_creates_one_case_and_exposes_branch_scoped_read_models(self):
        transfer, pallet = self.create_in_transit_pallet(
            reference="IBT-CRIT-SHORT-001",
            pallet_code="PAL-CRIT-SHORT-001",
        )
        self.authenticate(self.destination_worker)
        self.assertEqual(self.confirm_arrival(pallet).status_code, status.HTTP_200_OK)
        session_id = self.start_receiving(pallet).data["receiving_session"]["id"]
        self.receive_line(session_id, self.product.sku, "2", self.destination_location.code)
        self.receive_line(session_id, self.second_product.sku, "1", self.second_destination_location.code)

        closed = self.close_receiving(session_id)
        duplicate_close = self.close_receiving(session_id)
        alias_after_close = self.complete_receiving_alias(session_id)

        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.assertEqual(closed.data["result"], "discrepancy")
        self.assertEqual(duplicate_close.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(alias_after_close.status_code, status.HTTP_400_BAD_REQUEST)
        pallet.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(pallet.status, TransferPallet.Status.CLOSED_WITH_DISCREPANCY)
        self.assertEqual(transfer.status, InterBranchTransfer.Status.CLOSED_WITH_DISCREPANCY)
        self.assertEqual(self.inventory_quantity(self.product, self.destination_location), Decimal("2.000"))
        self.assertEqual(self.inventory_quantity(self.second_product, self.second_destination_location), Decimal("1.000"))

        discrepancy = TransferDiscrepancy.objects.get(pallet=pallet)
        self.assertEqual(TransferDiscrepancy.objects.filter(pallet=pallet).count(), 1)
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.OPEN)
        lines = {item.product.sku: item for item in discrepancy.items.select_related("product")}
        self.assertEqual(set(lines), {self.product.sku, self.second_product.sku})
        self.assertEqual(lines[self.product.sku].expected_quantity, Decimal("3.000"))
        self.assertEqual(lines[self.product.sku].received_quantity, Decimal("2.000"))
        self.assertEqual(lines[self.product.sku].difference_quantity, Decimal("-1.000"))
        self.assertEqual(lines[self.product.sku].discrepancy_quantity, Decimal("1.000"))
        self.assertEqual(lines[self.second_product.sku].expected_quantity, Decimal("2.000"))
        self.assertEqual(lines[self.second_product.sku].received_quantity, Decimal("1.000"))
        self.assertEqual(lines[self.second_product.sku].discrepancy_quantity, Decimal("1.000"))
        self.assertEqual(AuditLog.objects.filter(event_type="mm_task_completed", pallet=pallet).count(), 1)
        self.assertEqual(PalletReceivingScan.objects.filter(pallet=pallet).count(), 2)

        destination_list = self.client.get("/api/transfer-discrepancies/", {"branch": self.destination_branch.code})
        self.assertEqual(destination_list.status_code, status.HTTP_200_OK)
        self.assertEqual([row["reference"] for row in destination_list.data["results"]], [discrepancy.reference])
        destination_detail = self.client.get(f"/api/transfer-discrepancies/{discrepancy.id}/")
        self.assertEqual(destination_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(destination_detail.data["line_count"], 2)
        self.assertTrue(destination_detail.data["source_review"] is None)

        contents = self.client.get("/api/scanner/contents/", {"code": pallet.scan_code})
        self.assertEqual(contents.status_code, status.HTTP_200_OK)
        self.assertEqual(contents.data["discrepancy_reference"], discrepancy.reference)
        self.assertEqual(
            {item["sku"]: item["missing_quantity"] for item in contents.data["items"]},
            {self.product.sku: 1, self.second_product.sku: 1},
        )

        overview = self.client.get("/api/transport-overview/", {"branch": self.destination_branch.code})
        self.assertEqual(overview.status_code, status.HTTP_200_OK)
        self.assertEqual(overview.data["summary"]["pallets_awaiting_receipt"], 0)
        self.assertEqual(overview.data["summary"]["unresolved_discrepancy_transfers"], 1)
        self.assertTrue(
            any(item["reference"] == discrepancy.reference for item in overview.data["attention_items"])
        )

        exceptions = self.client.get("/api/inventory-exceptions/summary/", {"branch": self.destination_branch.code})
        self.assertEqual(exceptions.status_code, status.HTTP_200_OK)
        transfer_category = next(item for item in exceptions.data["categories"] if item["key"] == "transfer_discrepancies")
        action_category = next(item for item in exceptions.data["categories"] if item["key"] == "action_queue")
        self.assertEqual(transfer_category["count"], 1)
        self.assertEqual(action_category["count"], 1)

        action_rows = self.client.get("/api/transfer-discrepancy-actions/", {"branch": self.destination_branch.code})
        self.assertEqual(action_rows.status_code, status.HTTP_200_OK)
        self.assertEqual(action_rows.data["results"][0]["action_type"], "review_destination_shortage")
        self.assertEqual(action_rows.data["results"][0]["target_reference"], discrepancy.reference)
        source_actions = self.client.get("/api/transfer-discrepancy-actions/", {"branch": self.source_branch.code})
        self.assertEqual(source_actions.status_code, status.HTTP_200_OK)
        self.assertEqual(source_actions.data["results"], [])

        events = self.client.get("/api/current-events/", {"branch": self.destination_branch.code, "search": pallet.scan_code})
        self.assertEqual(events.status_code, status.HTTP_200_OK)
        self.assertTrue(any(event["event_type"] == "mm_task_completed" for event in events.data["results"]))

        self.authenticate(self.source_worker)
        source_detail = self.client.get(f"/api/transfer-discrepancies/{discrepancy.id}/")
        self.assertEqual(source_detail.status_code, status.HTTP_200_OK)
        source_filtered_list = self.client.get("/api/transfer-discrepancies/", {"branch": self.source_branch.code})
        self.assertEqual(source_filtered_list.status_code, status.HTTP_200_OK)
        self.assertEqual(source_filtered_list.data["results"], [])
        source_start = self.start_receiving(pallet)
        self.assertEqual(source_start.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.unrelated_worker)
        unrelated_list = self.client.get("/api/transfer-discrepancies/")
        self.assertEqual(unrelated_list.status_code, status.HTTP_200_OK)
        self.assertEqual(unrelated_list.data["results"], [])
        unrelated_detail = self.client.get(f"/api/transfer-discrepancies/{discrepancy.id}/")
        self.assertEqual(unrelated_detail.status_code, status.HTTP_404_NOT_FOUND)
        unrelated_overview = self.client.get("/api/transport-overview/", {"branch": self.unrelated_branch.code})
        self.assertEqual(unrelated_overview.status_code, status.HTTP_200_OK)
        self.assertEqual(unrelated_overview.data["summary"]["unresolved_discrepancy_transfers"], 0)


class CriticalSourceReviewReconciliationIntegrationTests(CriticalInterBranchReceivingFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.source_leader = create_branch_user(
            "CRIT_GDA_LEADER",
            self.source_branch,
            role=UserBranchMembership.Role.LEADER,
        )
        self.unconfirmed_location = Location.objects.create(
            branch=self.destination_branch,
            code="UNCONFIRMED",
            name="Unconfirmed receiving discrepancy",
            location_type=Location.LocationType.RECEIVING,
        )

    def post_print_report(self, discrepancy):
        return self.client.post(
            f"/api/transfer-discrepancies/{discrepancy.id}/print-report/",
            {"printer_code": "ZEBRA-CRIT", "worker_code": self.destination_worker.username},
            format="json",
        )

    def post_confirm_shortage(self, discrepancy, product, quantity, operation_id):
        return self.client.post(
            f"/api/transfer-discrepancies/{discrepancy.id}/confirm-shortage/",
            {
                "product_code": product.sku,
                "quantity": str(quantity),
                "worker_code": self.destination_leader.username,
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def post_begin_source_review(self, review, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-source-reviews/{review.id}/begin/",
            {"worker_code": worker_code or self.source_worker.username},
            format="json",
        )

    def post_complete_source_review(self, review, *, operation_id, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-source-reviews/{review.id}/complete/",
            {
                "finding": TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND,
                "finding_note": "Source dispatch review confirms a source-side shortage investigation is required.",
                "worker_code": worker_code or self.source_worker.username,
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def post_acknowledge_reconciliation(self, reconciliation, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/acknowledge/",
            {"worker_code": worker_code or self.source_worker.username},
            format="json",
        )

    def post_begin_source_verification(self, verification, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/begin/",
            {"worker_code": worker_code or self.source_worker.username},
            format="json",
        )

    def post_record_found(self, verification, product, quantity, location_code, operation_id, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/record-found/",
            {
                "product_code": product.sku,
                "destination_location_code": location_code,
                "quantity": str(quantity),
                "worker_code": worker_code or self.source_worker.username,
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def post_complete_source_search(self, verification, operation_id, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/complete-search/",
            {
                "worker_code": worker_code or self.source_worker.username,
                "search_completion_note": "Source search completed. Remaining units were not found in source stock.",
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def post_complete_manual(self, reconciliation, operation_id, worker_code=None):
        return self.client.post(
            f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/complete-manual/",
            {
                "outcome": TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED,
                "decision_note": "One unit was found at source and two units are final source loss.",
                "worker_code": worker_code or self.source_leader.username,
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def action_types(self, branch_code):
        response = self.client.get("/api/transfer-discrepancy-actions/", {"branch": branch_code})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [row["action_type"] for row in response.data["results"]]

    def category_count(self, branch_code, key):
        response = self.client.get("/api/inventory-exceptions/summary/", {"branch": branch_code})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return next(category["count"] for category in response.data["categories"] if category["key"] == key)

    def source_inventory_quantity(self, product):
        item = InventoryItem.objects.filter(
            branch=self.source_branch,
            location=self.source_location,
            product=product,
        ).first()
        return item.quantity_on_hand if item else Decimal("0")

    def create_receiving_shortage(self):
        transfer, pallet = self.create_in_transit_pallet(
            reference="IBT-GDA-GDY-CRIT-RECON",
            pallet_code="PAL-GDA-GDY-CRIT-RECON",
            first_quantity="5",
            second_quantity="4",
        )
        self.authenticate(self.destination_worker)
        self.assertEqual(self.confirm_arrival(pallet).status_code, status.HTTP_200_OK)
        session_id = self.start_receiving(pallet).data["receiving_session"]["id"]
        self.receive_line(session_id, self.product.sku, "3", self.destination_location.code)
        self.receive_line(session_id, self.second_product.sku, "3", self.second_destination_location.code)
        close = self.close_receiving(session_id)
        self.assertEqual(close.status_code, status.HTTP_200_OK)
        self.assertEqual(close.data["result"], "discrepancy")
        return transfer, pallet, TransferDiscrepancy.objects.get(pallet=pallet)

    def test_source_review_reconciliation_full_accounting_chain(self):
        transfer, pallet, discrepancy = self.create_receiving_shortage()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.OPEN)
        self.assertEqual(discrepancy.items.count(), 2)
        self.assertEqual(
            sum((item.expected_quantity for item in discrepancy.items.all()), Decimal("0")),
            Decimal("9.000"),
        )
        self.assertEqual(
            sum((item.received_quantity for item in discrepancy.items.all()), Decimal("0")),
            Decimal("6.000"),
        )
        self.assertEqual(
            sum((item.discrepancy_quantity for item in discrepancy.items.all()), Decimal("0")),
            Decimal("3.000"),
        )
        self.assertEqual(self.inventory_quantity(self.product, self.destination_location), Decimal("3.000"))
        self.assertEqual(self.inventory_quantity(self.second_product, self.second_destination_location), Decimal("3.000"))
        self.assertEqual(self.action_types(self.destination_branch.code), ["review_destination_shortage"])
        self.assertEqual(self.category_count(self.destination_branch.code, "transfer_discrepancies"), 1)
        self.assertFalse(TransferDiscrepancySourceReview.objects.filter(discrepancy=discrepancy).exists())

        report = self.post_print_report(discrepancy)
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(report.data["posted_quantity"], "3.000")
        self.assertEqual(self.post_print_report(discrepancy).data["first_print"], False)
        self.assertEqual(discrepancy.items.filter(posted_to_unconfirmed_quantity__gt=0).count(), 2)

        self.authenticate(self.source_leader)
        forbidden_source_confirmation = self.post_confirm_shortage(
            discrepancy,
            self.product,
            "1",
            "source-cannot-confirm-destination-shortage",
        )
        self.assertEqual(forbidden_source_confirmation.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.destination_leader)
        confirm_product = self.post_confirm_shortage(discrepancy, self.product, "2", "confirm-product-a")
        retry_confirm_product = self.post_confirm_shortage(discrepancy, self.product, "2", "confirm-product-a")
        confirm_second = self.post_confirm_shortage(discrepancy, self.second_product, "1", "confirm-product-b")
        self.assertEqual(confirm_product.status_code, status.HTTP_200_OK)
        self.assertEqual(retry_confirm_product.status_code, status.HTTP_200_OK)
        self.assertEqual(confirm_second.status_code, status.HTTP_200_OK)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.status, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE)
        self.assertEqual(discrepancy.confirmed_shortage_by_worker_code, self.destination_leader.username)
        self.assertEqual(TransferDiscrepancyShortageConfirmation.objects.filter(discrepancy=discrepancy).count(), 2)
        self.assertEqual(TransferDiscrepancySourceReview.objects.filter(discrepancy=discrepancy).count(), 1)
        review = TransferDiscrepancySourceReview.objects.get(discrepancy=discrepancy)
        self.assertEqual(review.source_branch, self.source_branch)
        self.assertEqual(self.action_types(self.destination_branch.code), [])

        self.authenticate(self.unrelated_worker)
        self.assertEqual(self.client.get(f"/api/transfer-discrepancy-source-reviews/{review.id}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get("/api/transfer-discrepancy-actions/", {"branch": self.unrelated_branch.code}).data["results"], [])
        self.assertEqual(self.post_begin_source_review(review).status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.destination_leader)
        forbidden_destination_begin = self.post_begin_source_review(review)
        self.assertEqual(forbidden_destination_begin.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.source_worker)
        source_review_detail = self.client.get(f"/api/transfer-discrepancy-source-reviews/{review.id}/")
        self.assertEqual(source_review_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(source_review_detail.data["total_expected_quantity"], "9.000")
        self.assertEqual(source_review_detail.data["total_received_quantity"], "6.000")
        self.assertEqual(source_review_detail.data["total_confirmed_shortage_quantity"], "3.000")
        self.assertEqual(len(source_review_detail.data["source_dispatch_evidence"]), 2)
        self.assertEqual(len(source_review_detail.data["destination_receiving_evidence"]), 2)
        self.assertEqual(self.action_types(self.source_branch.code), ["begin_source_review"])
        begin_review = self.post_begin_source_review(review)
        repeat_begin_review = self.post_begin_source_review(review)
        self.assertEqual(begin_review.status_code, status.HTTP_200_OK)
        self.assertEqual(repeat_begin_review.status_code, status.HTTP_200_OK)
        self.assertEqual(self.action_types(self.source_branch.code), ["complete_source_review"])
        pending_manual = self.client.post(
            "/api/transfer-discrepancy-reconciliations/999999/complete-manual/",
            {
                "outcome": TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED,
                "decision_note": "Cannot complete guessed reconciliation.",
                "worker_code": self.source_leader.username,
                "client_operation_id": "bad-guessed-reconciliation",
            },
            format="json",
        )
        self.assertEqual(pending_manual.status_code, status.HTTP_404_NOT_FOUND)
        complete_review = self.post_complete_source_review(review, operation_id="complete-source-review")
        retry_complete_review = self.post_complete_source_review(review, operation_id="complete-source-review")
        overwrite_review = self.post_complete_source_review(review, operation_id="complete-source-review-overwrite")
        self.assertEqual(complete_review.status_code, status.HTTP_200_OK)
        self.assertEqual(retry_complete_review.status_code, status.HTTP_200_OK)
        self.assertEqual(overwrite_review.status_code, status.HTTP_400_BAD_REQUEST)
        review.refresh_from_db()
        self.assertEqual(review.status, TransferDiscrepancySourceReview.Status.COMPLETED)
        self.assertEqual(review.finding, TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND)
        self.assertEqual(TransferDiscrepancyReconciliation.objects.filter(discrepancy=discrepancy).count(), 1)
        reconciliation = TransferDiscrepancyReconciliation.objects.get(discrepancy=discrepancy)
        self.assertEqual(reconciliation.route, TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION)
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.PENDING_ACTION)
        self.assertEqual(self.action_types(self.source_branch.code), ["acknowledge_reconciliation"])

        self.authenticate(self.source_worker)
        self.assertEqual(self.post_acknowledge_reconciliation(reconciliation).status_code, status.HTTP_200_OK)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.IN_PROGRESS)
        self.assertEqual(TransferDiscrepancySourceStockVerification.objects.filter(reconciliation=reconciliation).count(), 1)
        verification = TransferDiscrepancySourceStockVerification.objects.get(reconciliation=reconciliation)
        self.assertEqual(verification.items.count(), 2)
        self.assertEqual(
            {item.product.sku: item.target_quantity for item in verification.items.select_related("product")},
            {self.product.sku: Decimal("2.000"), self.second_product.sku: Decimal("1.000")},
        )
        self.assertEqual(self.action_types(self.source_branch.code), ["begin_source_stock_verification"])

        self.authenticate(self.destination_leader)
        self.assertEqual(self.post_begin_source_verification(verification).status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.source_worker)
        begin_verification = self.post_begin_source_verification(verification)
        repeat_begin_verification = self.post_begin_source_verification(verification)
        self.assertEqual(begin_verification.status_code, status.HTTP_200_OK)
        self.assertEqual(repeat_begin_verification.status_code, status.HTTP_200_OK)
        self.assertEqual(self.action_types(self.source_branch.code), ["continue_source_stock_verification"])
        source_before = self.source_inventory_quantity(self.product)
        movement_count_before_failures = StockMovement.objects.count()
        bad_product = self.post_record_found(
            verification,
            self.unexpected_product,
            "1",
            self.source_location.code,
            "bad-source-product",
        )
        bad_quantity = self.post_record_found(
            verification,
            self.product,
            "3",
            self.source_location.code,
            "bad-source-quantity",
        )
        bad_location = self.post_record_found(
            verification,
            self.product,
            "1",
            self.destination_location.code,
            "bad-source-location",
        )
        self.assertEqual(bad_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_quantity.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_location.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TransferDiscrepancySourceStockRecovery.objects.filter(verification=verification).count(), 0)
        self.assertEqual(self.source_inventory_quantity(self.product), source_before)
        self.assertEqual(StockMovement.objects.count(), movement_count_before_failures)

        found = self.post_record_found(
            verification,
            self.product,
            "1",
            self.source_location.code,
            "source-found-product-a-one",
        )
        retry_found = self.post_record_found(
            verification,
            self.product,
            "1",
            self.source_location.code,
            "source-found-product-a-one",
        )
        self.assertEqual(found.status_code, status.HTTP_200_OK)
        self.assertEqual(retry_found.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancySourceStockRecovery.objects.filter(verification=verification).count(), 1)
        self.assertEqual(self.source_inventory_quantity(self.product), source_before + Decimal("1.000"))
        self.assertEqual(
            StockMovement.objects.filter(
                reference=verification.reference,
                movement_type=StockMovement.MovementType.SOURCE_DISCREPANCY_RECOVERY,
            ).count(),
            1,
        )
        self.assertEqual(self.action_types(self.source_branch.code), ["complete_source_search"])

        source_search = self.post_complete_source_search(verification, "source-search-complete")
        retry_source_search = self.post_complete_source_search(verification, "source-search-complete")
        late_found = self.post_record_found(
            verification,
            self.second_product,
            "1",
            self.source_location.code,
            "late-found-after-search",
        )
        self.assertEqual(source_search.status_code, status.HTTP_200_OK)
        self.assertEqual(retry_source_search.status_code, status.HTTP_200_OK)
        self.assertEqual(late_found.status_code, status.HTTP_400_BAD_REQUEST)
        verification.refresh_from_db()
        reconciliation.refresh_from_db()
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED)
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED)
        self.authenticate(self.source_leader)
        self.assertEqual(self.action_types(self.source_branch.code), ["record_final_reconciliation_outcome"])
        self.authenticate(self.destination_leader)
        self.assertEqual(self.action_types(self.destination_branch.code), ["record_final_reconciliation_outcome"])
        self.authenticate(self.source_leader)
        verification_detail = self.client.get(f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/")
        self.assertEqual(verification_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(verification_detail.data["total_target_quantity"], "3.000")
        self.assertEqual(verification_detail.data["total_found_quantity"], "1.000")
        self.assertEqual(verification_detail.data["total_remaining_quantity"], "2.000")
        self.assertEqual(verification_detail.data["total_unresolved_quantity"], "2.000")

        self.authenticate(self.source_worker)
        worker_final = self.post_complete_manual(reconciliation, "worker-final-forbidden")
        self.assertEqual(worker_final.status_code, status.HTTP_403_FORBIDDEN)
        self.authenticate(self.source_leader)
        final = self.post_complete_manual(reconciliation, "source-final-complete")
        self.assertEqual(final.status_code, status.HTTP_200_OK)
        self.assertEqual(TransferDiscrepancyManualReconciliationDecision.objects.filter(reconciliation=reconciliation).count(), 1)
        retry_destination_final = self.post_complete_manual(reconciliation, "source-final-complete")
        different_final = self.post_complete_manual(reconciliation, "different-final-after-complete")
        self.assertEqual(retry_destination_final.status_code, status.HTTP_200_OK)
        self.assertEqual(different_final.status_code, status.HTTP_400_BAD_REQUEST)
        reconciliation.refresh_from_db()
        verification.refresh_from_db()
        self.assertEqual(reconciliation.status, TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertEqual(reconciliation.completed_by_worker_code, self.source_leader.username)
        self.assertEqual(verification.status, TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED)
        decision = TransferDiscrepancyManualReconciliationDecision.objects.get(reconciliation=reconciliation)
        self.assertEqual(decision.outcome, TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED)
        self.assertEqual(decision.decision_note, "One unit was found at source and two units are final source loss.")

        detail = self.client.get(f"/api/transfer-discrepancies/{discrepancy.id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["total_posted_to_unconfirmed_quantity"], "3.000")
        self.assertEqual(detail.data["total_confirmed_shortage_quantity"], "3.000")
        self.assertEqual(detail.data["total_recovered_quantity"], "0.000")
        self.assertEqual(detail.data["total_remaining_quantity"], "0.000")
        self.assertEqual(detail.data["reconciliation"]["status"], TransferDiscrepancyReconciliation.Status.COMPLETED)
        self.assertEqual(detail.data["reconciliation"]["source_stock_verification"]["total_found_quantity"], "1.000")
        reconciliation_detail = self.client.get(f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/")
        self.assertEqual(reconciliation_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(reconciliation_detail.data["total_confirmed_shortage_quantity"], "3.000")
        self.assertEqual(reconciliation_detail.data["source_stock_verification"]["total_found_quantity"], "1.000")
        self.assertEqual(reconciliation_detail.data["source_stock_verification"]["total_unresolved_quantity"], "2.000")
        self.assertEqual(reconciliation_detail.data["manual_decision"]["outcome"], decision.outcome)

        expected_total = Decimal("9.000")
        received_total = Decimal("6.000")
        source_found_total = Decimal(verification_detail.data["total_found_quantity"])
        unresolved_total = Decimal(verification_detail.data["total_unresolved_quantity"])
        self.assertEqual(expected_total, received_total + source_found_total + unresolved_total)
        self.assertEqual(TransferDiscrepancyReconciliation.objects.filter(discrepancy=discrepancy).count(), 1)
        self.assertEqual(TransferDiscrepancySourceStockVerification.objects.filter(reconciliation=reconciliation).count(), 1)
        self.assertEqual(AuditLog.objects.filter(entity_name="TransferDiscrepancyReconciliation", message__icontains="with final outcome").count(), 1)
        self.assertEqual(self.action_types(self.source_branch.code), [])
        self.assertEqual(self.action_types(self.destination_branch.code), [])
        self.assertEqual(self.category_count(self.source_branch.code, "action_queue"), 0)
        self.assertEqual(self.category_count(self.source_branch.code, "reconciliations"), 0)
        self.assertEqual(self.category_count(self.source_branch.code, "source_stock"), 0)

        source_events = self.client.get("/api/current-events/", {"branch": self.source_branch.code, "search": reconciliation.reference})
        self.assertEqual(source_events.status_code, status.HTTP_200_OK)
        source_entity_names = {event["entity_name"] for event in source_events.data["results"]}
        self.assertIn("TransferDiscrepancyReconciliation", source_entity_names)
        self.assertTrue(any("final outcome" in event["message"] for event in source_events.data["results"]))
        self.authenticate(self.destination_leader)
        discrepancy_events = self.client.get("/api/current-events/", {"branch": self.destination_branch.code, "search": discrepancy.reference})
        self.assertEqual(discrepancy_events.status_code, status.HTTP_200_OK)
        self.assertTrue(any(event["entity_name"] == "TransferDiscrepancy" for event in discrepancy_events.data["results"]))

        self.authenticate(self.unrelated_worker)
        self.assertEqual(self.client.get(f"/api/transfer-discrepancies/{discrepancy.id}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(f"/api/transfer-discrepancy-source-reviews/{review.id}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(f"/api/transfer-discrepancy-source-stock-verifications/{verification.id}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(f"/api/transfer-discrepancy-reconciliations/{reconciliation.id}/").status_code, status.HTTP_404_NOT_FOUND)


class ScannerLookupAndQuickTransferTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="LKP", name="Lookup Branch", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="XBR", name="Cross Branch", city="Gdansk", country="Poland")
        self.user = User.objects.create_user(username="LKP_WORKER", password="demo12345")
        UserBranchMembership.objects.create(user=self.user, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.other_user = User.objects.create_user(username="XBR_WORKER", password="demo12345")
        UserBranchMembership.objects.create(
            user=self.other_user,
            branch=self.other_branch,
            role=UserBranchMembership.Role.WORKER,
        )
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
        self.other_target_location = Location.objects.create(
            branch=self.other_branch,
            code="X-02-01",
            name="Cross target shelf",
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
        self.client.force_authenticate(self.user)

    def transfer(self, **overrides):
        payload = {
            "source_location_code": "L-01-01",
            "product_code": "LOOK-001",
            "target_location_code": "L-02-01",
            "quantity": "1",
            "client_operation_id": "11111111-1111-4111-8111-111111111111",
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
        self.client.force_authenticate(self.user)

        response = self.transfer(quantity="2")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.source_item.refresh_from_db()
        target_item = InventoryItem.objects.get(location=self.target_location, product=self.product)
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("3.000"))
        self.assertEqual(target_item.quantity_on_hand, Decimal("2.000"))
        operation = ScannerQuickTransferOperation.objects.get(client_operation_id="11111111-1111-4111-8111-111111111111")
        self.assertEqual(operation.status, ScannerQuickTransferOperation.Status.COMPLETED)
        self.assertEqual(operation.stock_movement_id, response.data["movement_id"])
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.TRANSFER,
                source_location=self.source_location,
                destination_location=self.target_location,
                quantity=Decimal("2.000"),
                performed_by=self.user,
            ).exists()
        )
        audit_log = AuditLog.objects.get(message__icontains="Scanner quick transfer")
        self.assertEqual(audit_log.actor, self.user)
        self.assertEqual(audit_log.branch, self.branch)
        self.assertEqual(audit_log.product, self.product)
        self.assertEqual(audit_log.source_location, self.source_location)
        self.assertEqual(audit_log.destination_location, self.target_location)

    def test_quick_transfer_rejects_same_source_and_target(self):
        response = self.transfer(target_location_code="L-01-01")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("same", response.data["detail"])

    def test_quick_transfer_rejects_insufficient_quantity(self):
        response = self.transfer(quantity="10")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient", response.data["detail"])

    def test_quick_transfer_rejects_cross_branch_locations(self):
        response = self.transfer(target_location_code="X-02-01")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("same branch", response.data["detail"])
        self.assertFalse(StockMovement.objects.exists())

    def test_quick_transfer_rejects_authenticated_user_without_branch_access(self):
        self.client.force_authenticate(self.other_user)

        response = self.transfer(quantity="1")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(StockMovement.objects.exists())


class CriticalQuickTransferIntegrationTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="QTI", name="Quick Transfer Integration", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="QTO", name="Other Transfer Branch", city="Gdansk", country="Poland")
        self.worker = create_branch_user("QTI_WORKER", self.branch)
        self.same_branch_worker = create_branch_user("QTI_OTHER_WORKER", self.branch)
        self.other_worker = create_branch_user("QTO_WORKER", self.other_branch)
        self.source_location = Location.objects.create(
            branch=self.branch,
            code="QT-SRC",
            name="Quick transfer source",
            location_type=Location.LocationType.STORAGE,
        )
        self.target_location = Location.objects.create(
            branch=self.branch,
            code="QT-DST",
            name="Quick transfer destination",
            location_type=Location.LocationType.PICKING,
        )
        self.cross_branch_location = Location.objects.create(
            branch=self.other_branch,
            code="QTO-DST",
            name="Other branch destination",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(
            sku="QT-001",
            name="Quick Transfer Product",
            barcode="881000000001",
            unit_of_measure="pcs",
        )
        self.source_item = InventoryItem.objects.create(
            branch=self.branch,
            location=self.source_location,
            product=self.product,
            quantity_on_hand=Decimal("10"),
            quantity_reserved=Decimal("0"),
        )

    def quick_transfer_payload(self, **overrides):
        payload = {
            "source_location_code": self.source_location.code,
            "target_location_code": self.target_location.code,
            "product_code": self.product.barcode,
            "quantity": "3",
            "client_operation_id": "22222222-2222-4222-8222-222222222222",
        }
        payload.update(overrides)
        return payload

    def post_quick_transfer(self, **overrides):
        return self.client.post("/api/scanner/quick-transfer/", self.quick_transfer_payload(**overrides), format="json")

    def test_quick_transfer_updates_inventory_history_events_and_is_idempotent(self):
        unauthenticated = self.post_quick_transfer()
        self.client.force_authenticate(self.worker)
        first = self.post_quick_transfer()
        duplicate = self.post_quick_transfer()

        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(duplicate.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["movement_id"], duplicate.data["movement_id"])
        self.assertFalse(first.data["replayed"])
        self.assertTrue(duplicate.data["replayed"])

        self.source_item.refresh_from_db()
        target_item = InventoryItem.objects.get(branch=self.branch, location=self.target_location, product=self.product)
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("7.000"))
        self.assertEqual(target_item.quantity_on_hand, Decimal("3.000"))
        self.assertEqual(
            InventoryItem.objects.filter(branch=self.branch, product=self.product).aggregate(total=models.Sum("quantity_on_hand"))["total"],
            Decimal("10.000"),
        )

        movement = StockMovement.objects.get(pk=first.data["movement_id"])
        self.assertEqual(movement.movement_type, StockMovement.MovementType.TRANSFER)
        self.assertEqual(movement.reference, "SCANNER-TRANSFER-22222222-2222-4222-8222-222222222222")
        self.assertEqual(movement.performed_by, self.worker)
        self.assertEqual(movement.quantity_before, Decimal("10.000"))
        self.assertEqual(movement.quantity_after, Decimal("7.000"))
        self.assertEqual(StockMovement.objects.count(), 1)
        operation = ScannerQuickTransferOperation.objects.get(client_operation_id="22222222-2222-4222-8222-222222222222")
        self.assertEqual(operation.stock_movement, movement)
        self.assertEqual(operation.performed_by, self.worker)

        history = self.client.get("/api/stock-movements/", {"branch": "QTI", "movement_type": StockMovement.MovementType.TRANSFER})
        detail = self.client.get(f"/api/stock-movements/{movement.id}/")
        events = self.client.get("/api/current-events/", {"branch": "QTI", "search": "quick transfer"})

        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(history.data["results"][0]["id"], movement.id)
        self.assertEqual(history.data["results"][0]["origin"], "Scanner Quick Transfer")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["client_operation_id"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(detail.data["source_location_code"], self.source_location.code)
        self.assertEqual(detail.data["destination_location_code"], self.target_location.code)
        self.assertEqual(events.status_code, status.HTTP_200_OK)
        self.assertTrue(any(event["event_type"] == "scanner_quick_transfer" for event in events.data["results"]))

        self.client.force_authenticate(self.other_worker)
        forbidden_detail = self.client.get(f"/api/stock-movements/{movement.id}/")
        self.assertEqual(forbidden_detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_quick_transfer_validation_rolls_back_without_history_or_success_event(self):
        self.client.force_authenticate(self.worker)

        missing_key = self.client.post(
            "/api/scanner/quick-transfer/",
            {
                "source_location_code": self.source_location.code,
                "target_location_code": self.target_location.code,
                "product_code": self.product.sku,
                "quantity": "1",
            },
            format="json",
        )
        same_location = self.post_quick_transfer(
            target_location_code=self.source_location.code,
            client_operation_id="33333333-3333-4333-8333-333333333333",
        )
        cross_branch = self.post_quick_transfer(
            target_location_code=self.cross_branch_location.code,
            client_operation_id="44444444-4444-4444-8444-444444444444",
        )
        insufficient = self.post_quick_transfer(quantity="99", client_operation_id="55555555-5555-4555-8555-555555555555")

        self.assertEqual(missing_key.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(same_location.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(cross_branch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(insufficient.status_code, status.HTTP_400_BAD_REQUEST)
        self.source_item.refresh_from_db()
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("10.000"))
        self.assertFalse(InventoryItem.objects.filter(location=self.target_location, product=self.product).exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(ScannerQuickTransferOperation.objects.exists())
        self.assertFalse(AuditLog.objects.filter(event_type="scanner_quick_transfer").exists())

        self.client.force_authenticate(self.other_worker)
        forbidden = self.post_quick_transfer(client_operation_id="66666666-6666-4666-8666-666666666666")
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

    def test_quick_transfer_rejects_operation_id_reuse_for_different_payload_or_user(self):
        operation_id = "77777777-7777-4777-8777-777777777777"
        self.client.force_authenticate(self.worker)
        first = self.post_quick_transfer(client_operation_id=operation_id)
        changed_quantity = self.post_quick_transfer(client_operation_id=operation_id, quantity="2")
        other_product = Product.objects.create(sku="QT-002", name="Other Quick Transfer Product", barcode="881000000002")
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.source_location,
            product=other_product,
            quantity_on_hand=Decimal("3"),
            quantity_reserved=Decimal("0"),
        )
        changed_product = self.post_quick_transfer(client_operation_id=operation_id, product_code=other_product.sku)
        changed_source = self.post_quick_transfer(
            client_operation_id=operation_id,
            source_location_code=self.target_location.code,
            target_location_code=self.source_location.code,
        )
        self.client.force_authenticate(self.same_branch_worker)
        other_user = self.post_quick_transfer(client_operation_id=operation_id)
        self.client.force_authenticate(self.other_worker)
        other_branch_user = self.post_quick_transfer(client_operation_id=operation_id)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        for response in [changed_quantity, changed_product, changed_source, other_user]:
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(other_branch_user.status_code, status.HTTP_403_FORBIDDEN)
        self.source_item.refresh_from_db()
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("7.000"))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(event_type="scanner_quick_transfer").count(), 1)

    def test_quick_transfer_failed_stock_request_does_not_consume_operation_id(self):
        operation_id = "88888888-8888-4888-8888-888888888888"
        self.client.force_authenticate(self.worker)
        failed = self.post_quick_transfer(client_operation_id=operation_id, quantity="99")
        self.assertEqual(failed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ScannerQuickTransferOperation.objects.filter(client_operation_id=operation_id).exists())

        self.source_item.quantity_on_hand = Decimal("120")
        self.source_item.save(update_fields=["quantity_on_hand", "updated_at"])
        retried = self.post_quick_transfer(client_operation_id=operation_id, quantity="99")

        self.assertEqual(retried.status_code, status.HTTP_200_OK)
        self.source_item.refresh_from_db()
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("21.000"))


class CriticalQuickTransferConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.branch = Branch.objects.create(code="QTC", name="Quick Transfer Concurrency", city="Gdynia", country="Poland")
        self.worker = create_branch_user("QTC_WORKER", self.branch)
        self.source_location = Location.objects.create(
            branch=self.branch,
            code="QTC-SRC",
            name="Concurrency source",
            location_type=Location.LocationType.STORAGE,
        )
        self.target_location = Location.objects.create(
            branch=self.branch,
            code="QTC-DST",
            name="Concurrency destination",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(sku="QTC-001", name="Concurrent Quick Transfer Product", barcode="883000000001")
        self.source_item = InventoryItem.objects.create(
            branch=self.branch,
            location=self.source_location,
            product=self.product,
            quantity_on_hand=Decimal("10"),
            quantity_reserved=Decimal("0"),
        )

    def test_concurrent_duplicate_operation_moves_stock_once(self):
        operation_id = "99999999-9999-4999-8999-999999999999"
        payload = {
            "source_location_code": self.source_location.code,
            "target_location_code": self.target_location.code,
            "product_code": self.product.sku,
            "quantity": "4",
            "client_operation_id": operation_id,
        }
        barrier = threading.Barrier(2)
        responses = []
        errors = []

        def submit_duplicate():
            connections.close_all()
            client = APIClient()
            client.force_authenticate(self.worker)
            try:
                barrier.wait(timeout=10)
                response = client.post("/api/scanner/quick-transfer/", payload, format="json")
                responses.append(response)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=submit_duplicate), threading.Thread(target=submit_duplicate)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse(errors)
        self.assertEqual(len(responses), 2)
        self.assertEqual({response.status_code for response in responses}, {status.HTTP_200_OK})
        self.assertEqual({response.data["movement_id"] for response in responses}, {StockMovement.objects.get().id})
        self.assertEqual(sum(1 for response in responses if response.data["replayed"]), 1)
        self.source_item.refresh_from_db()
        target_item = InventoryItem.objects.get(branch=self.branch, location=self.target_location, product=self.product)
        self.assertEqual(self.source_item.quantity_on_hand, Decimal("6.000"))
        self.assertEqual(target_item.quantity_on_hand, Decimal("4.000"))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(ScannerQuickTransferOperation.objects.filter(client_operation_id=operation_id).count(), 1)
        self.assertEqual(AuditLog.objects.filter(event_type="scanner_quick_transfer").count(), 1)


class CriticalCycleCountIntegrationTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="CCI", name="Cycle Count Integration", city="Gdynia", country="Poland")
        self.other_branch = Branch.objects.create(code="CCX", name="Other Count Branch", city="Gdansk", country="Poland")
        self.leader = create_branch_user("CCI_LEADER", self.branch, UserBranchMembership.Role.LEADER)
        self.worker = create_branch_user("CCI_WORKER", self.branch)
        self.other_leader = create_branch_user("CCX_LEADER", self.other_branch, UserBranchMembership.Role.LEADER)
        self.location = Location.objects.create(
            branch=self.branch,
            code="CCI-01",
            name="Integration count location",
            location_type=Location.LocationType.STORAGE,
        )
        self.other_location = Location.objects.create(
            branch=self.other_branch,
            code="CCX-01",
            name="Other count location",
            location_type=Location.LocationType.STORAGE,
        )
        self.product = Product.objects.create(sku="CCI-001", name="Cycle Count Product", barcode="882000000001")
        self.inventory = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )

    def create_and_open_session(self):
        self.client.force_authenticate(self.leader)
        created = self.client.post(
            "/api/cycle-counts/",
            {
                "branch": self.branch.code,
                "location_ids": [self.location.id],
                "name": "Critical workflow count",
                "note": "Integration scenario.",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        opened = self.client.post(f"/api/cycle-counts/{created.data['id']}/open/", {}, format="json")
        self.assertEqual(opened.status_code, status.HTTP_200_OK)
        return CycleCountSession.objects.get(pk=created.data["id"])

    def scanner_count_and_submit(self, session, quantity):
        self.client.force_authenticate(self.worker)
        detail = self.client.get(f"/api/scanner/cycle-counts/{session.id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["locations"][0]["lines"], [])
        self.assertNotIn("expected_quantity", str(detail.data))
        counted = self.client.post(
            f"/api/scanner/cycle-counts/{session.id}/locations/{self.location.id}/count/",
            {"product_code": self.product.sku, "quantity": str(quantity)},
            format="json",
        )
        submitted = self.client.post(
            f"/api/scanner/cycle-counts/{session.id}/locations/{self.location.id}/submit/",
            {"confirm_zeroes": False},
            format="json",
        )
        self.assertEqual(counted.status_code, status.HTTP_200_OK)
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)
        return CycleCountLine.objects.get(session=session, product=self.product)

    def test_cycle_count_safe_variance_flows_from_scanner_to_review_adjustment_and_events(self):
        anonymous_create = self.client.post(
            "/api/cycle-counts/",
            {"branch": self.branch.code, "location_ids": [self.location.id]},
            format="json",
        )
        self.client.force_authenticate(self.worker)
        worker_create = self.client.post(
            "/api/cycle-counts/",
            {"branch": self.branch.code, "location_ids": [self.location.id]},
            format="json",
        )
        session = self.create_and_open_session()
        line = self.scanner_count_and_submit(session, "7")
        snapshot_expected = line.expected_quantity

        self.assertEqual(anonymous_create.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(worker_create.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(snapshot_expected, Decimal("5.000"))
        self.assertEqual(line.counted_quantity, Decimal("7.000"))
        self.assertEqual(line.reconciliation_status, CycleCountLine.ReconciliationStatus.PENDING_REVIEW)

        self.client.force_authenticate(self.worker)
        worker_adjust = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        self.client.force_authenticate(self.other_leader)
        other_detail = self.client.get(f"/api/cycle-counts/{session.id}/")
        self.client.force_authenticate(self.leader)
        review_queue = self.client.get("/api/cycle-count-review-queue/", {"branch": self.branch.code})
        close_before_reconcile = self.client.post(f"/api/cycle-counts/{session.id}/close/", {}, format="json")
        adjusted = self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/",
            {"note": "Integration count confirmed surplus."},
            format="json",
        )
        duplicate_adjust = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        movement_count = StockMovement.objects.count()
        closed = self.client.post(f"/api/cycle-counts/{session.id}/close/", {}, format="json")

        self.assertEqual(worker_adjust.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(other_detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(review_queue.status_code, status.HTTP_200_OK)
        self.assertTrue(any(row["line"] == line.id for row in review_queue.data["results"]))
        self.assertEqual(close_before_reconcile.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(adjusted.status_code, status.HTTP_200_OK)
        self.assertEqual(duplicate_adjust.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.assertEqual(StockMovement.objects.count(), movement_count)

        self.inventory.refresh_from_db()
        line.refresh_from_db()
        movement = StockMovement.objects.get(cycle_count_line=line)
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("7.000"))
        self.assertEqual(line.expected_quantity, snapshot_expected)
        self.assertEqual(line.reconciliation_status, CycleCountLine.ReconciliationStatus.ADJUSTMENT_APPLIED)
        self.assertEqual(movement.quantity_before, Decimal("5.000"))
        self.assertEqual(movement.quantity_after, Decimal("7.000"))
        self.assertEqual(movement.adjustment_reason, StockMovement.AdjustmentReason.COUNT_CORRECTION)

        stock_adjustments = self.client.get("/api/stock-adjustments/", {"branch": self.branch.code})
        cycle_detail = self.client.get(f"/api/cycle-counts/{session.id}/")
        review_queue_after = self.client.get("/api/cycle-count-review-queue/", {"branch": self.branch.code})
        events = self.client.get("/api/current-events/", {"branch": self.branch.code, "search": session.reference})

        self.assertEqual(stock_adjustments.status_code, status.HTTP_200_OK)
        self.assertEqual(stock_adjustments.data["results"][0]["id"], movement.id)
        self.assertEqual(cycle_detail.data["status"], CycleCountSession.Status.CLOSED)
        self.assertFalse(any(row["line"] == line.id for row in review_queue_after.data["results"]))
        event_types = {event["event_type"] for event in events.data["results"]}
        self.assertTrue(
            {
                "cycle_count_created",
                "cycle_count_opened",
                "cycle_count_location_submitted",
                "cycle_count_variance_adjustment_applied",
                "cycle_count_closed",
            }.issubset(event_types)
        )

    def test_cycle_count_recount_uses_new_blind_evidence_and_blocks_stale_recount(self):
        session = self.create_and_open_session()
        line = self.scanner_count_and_submit(session, "7")
        StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            source_location=self.location,
            movement_type=StockMovement.MovementType.PICK,
            quantity=Decimal("1"),
            reference="CCI-POST-SNAPSHOT",
        )

        self.client.force_authenticate(self.leader)
        stale_original = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        requested = self.client.post(
            f"/api/cycle-counts/{session.id}/lines/{line.id}/request-recount/",
            {"reason": "Movement occurred after the original count snapshot."},
            format="json",
        )
        recount = CycleCountRecount.objects.get(original_line=line)
        blocked_original = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")

        self.assertEqual(stale_original.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(requested.status_code, status.HTTP_201_CREATED)
        self.assertEqual(blocked_original.status_code, status.HTTP_409_CONFLICT)

        self.client.force_authenticate(self.worker)
        recount_detail = self.client.get(f"/api/scanner/cycle-count-recounts/{recount.id}/")
        self.assertEqual(recount_detail.status_code, status.HTTP_200_OK)
        self.assertNotIn("baseline_quantity", recount_detail.data)
        self.assertIsNone(recount_detail.data["counted_quantity"])
        submitted = self.client.post(
            f"/api/scanner/cycle-count-recounts/{recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "6"},
            format="json",
        )
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.leader)
        accepted = self.client.post(
            f"/api/cycle-counts/{session.id}/recounts/{recount.id}/accept/",
            {"note": "Accepted integration recount evidence."},
            format="json",
        )
        adjusted = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        duplicate_adjust = self.client.post(f"/api/cycle-counts/{session.id}/lines/{line.id}/apply-adjustment/", {}, format="json")
        closed = self.client.post(f"/api/cycle-counts/{session.id}/close/", {}, format="json")

        self.assertEqual(accepted.status_code, status.HTTP_200_OK)
        self.assertEqual(adjusted.status_code, status.HTTP_200_OK)
        self.assertEqual(duplicate_adjust.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(closed.status_code, status.HTTP_200_OK)
        self.inventory.refresh_from_db()
        recount.refresh_from_db()
        line.refresh_from_db()
        movement = StockMovement.objects.get(cycle_count_line=line)
        self.assertEqual(self.inventory.quantity_on_hand, Decimal("6.000"))
        self.assertEqual(recount.status, CycleCountRecount.Status.ACCEPTED)
        self.assertEqual(movement.cycle_count_recount, recount)
        self.assertEqual(StockMovement.objects.filter(cycle_count_line=line, movement_type=StockMovement.MovementType.ADJUSTMENT).count(), 1)

        events = self.client.get("/api/current-events/", {"branch": self.branch.code, "search": recount.reference})
        event_types = {event["event_type"] for event in events.data["results"]}
        self.assertTrue({"cycle_count_recount_requested", "cycle_count_recount_submitted", "cycle_count_recount_accepted"}.issubset(event_types))

        stale_session = self.create_and_open_session()
        stale_line = self.scanner_count_and_submit(stale_session, "7")
        self.client.force_authenticate(self.leader)
        self.client.post(
            f"/api/cycle-counts/{stale_session.id}/lines/{stale_line.id}/request-recount/",
            {"reason": "Create a stale recount baseline."},
            format="json",
        )
        stale_recount = CycleCountRecount.objects.get(original_line=stale_line)
        StockMovement.objects.create(
            branch=self.branch,
            product=self.product,
            inventory_item=self.inventory,
            destination_location=self.location,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity=Decimal("1"),
            reference="CCI-POST-RECOUNT-BASELINE",
        )
        self.client.force_authenticate(self.worker)
        self.client.post(
            f"/api/scanner/cycle-count-recounts/{stale_recount.id}/submit/",
            {"location_code": self.location.code, "product_code": self.product.sku, "quantity": "8"},
            format="json",
        )
        self.client.force_authenticate(self.leader)
        stale_accept = self.client.post(f"/api/cycle-counts/{stale_session.id}/recounts/{stale_recount.id}/accept/", {}, format="json")

        self.assertEqual(stale_accept.status_code, status.HTTP_409_CONFLICT)
        stale_recount.refresh_from_db()
        self.assertTrue(stale_recount.movement_after_baseline)


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
        UserBranchMembership.objects.create(user=self.demo_user, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.demo_user)

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

    def prepare_three_location_claim_job(self):
        self.location.code = "A-01-01"
        self.location.name = "A-01-01"
        self.location.save(update_fields=["code", "name", "updated_at"])
        self.other_location.code = "A-01-02"
        self.other_location.name = "A-01-02"
        self.other_location.save(update_fields=["code", "name", "updated_at"])
        self.third_location.code = "A-02-01"
        self.third_location.name = "A-02-01"
        self.third_location.save(update_fields=["code", "name", "updated_at"])
        self.task_2.source_location = self.other_location
        self.task_2.save(update_fields=["source_location", "updated_at"])
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.other_location,
            product=self.product_b,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        self.third_product_a_inventory.quantity_on_hand = Decimal("5")
        self.third_product_a_inventory.save(update_fields=["quantity_on_hand", "updated_at"])
        order_3 = self.create_order("JOB-ORDER-3", self.run_2, self.product_a)
        task_3 = self.create_task(order_3.lines.first())
        task_3.source_location = self.third_location
        task_3.save(update_fields=["source_location", "updated_at"])
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        start = self.start_job(job, cart_code="WOZEK-01")
        return job, start, self.task_1, self.task_2, task_3

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

    def test_current_cart_work_get_is_read_only_for_active_participant(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        start = self.start_job(job, cart_code="WOZEK-01")
        cart_work_session_id = start.data["cart_work_session"]["id"]
        participant = CartWorkParticipant.objects.get(user=self.gdy_worker)
        claim_id = PickingTaskClaim.objects.get(cart_work_participant=participant, status=PickingTaskClaim.Status.CLAIMED).id
        last_seen_at = participant.last_seen_at

        first = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": cart_work_session_id})
        second = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": cart_work_session_id})

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["participant"]["username"], "GDY_WORKER")
        self.assertEqual(first.data["current_instruction"]["picking_task_id"], participant.current_picking_task_id)
        self.assertEqual(len(first.data["cart_work_session"]["participants"]), 1)
        self.assertEqual(len(first.data["tasks"]), 2)
        participant.refresh_from_db()
        self.assertEqual(participant.last_seen_at, last_seen_at)
        self.assertEqual(PickingTaskClaim.objects.filter(id=claim_id, status=PickingTaskClaim.Status.CLAIMED).count(), 1)
        self.assertEqual(PickingTaskClaim.objects.filter(cart_work_participant=participant).count(), 1)

    def test_current_cart_work_get_does_not_create_missing_participant_or_claim(self):
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        start = self.start_job(job, cart_code="WOZEK-01")
        claim_count = PickingTaskClaim.objects.count()

        self.client.force_authenticate(self.gdy_leader)
        response = self.client.get(
            "/api/scanner/cart-work/current/",
            {"cart_work_session_id": start.data["cart_work_session"]["id"]},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CartWorkParticipant.objects.filter(user=self.gdy_leader).exists())
        self.assertEqual(PickingTaskClaim.objects.count(), claim_count)

    def test_two_workers_can_poll_same_cart_work_without_changing_claims(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.gdy_worker)
        self.create_jobs(route_run_ids=[self.run_1.id, self.run_2.id])
        job = PickingJob.objects.get()
        start = self.start_job(job, cart_code="WOZEK-01")
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.client.force_authenticate(self.gdy_leader)
        join = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")
        self.assertEqual(join.status_code, status.HTTP_200_OK)
        claim_snapshot = list(
            PickingTaskClaim.objects.filter(status=PickingTaskClaim.Status.CLAIMED)
            .order_by("cart_work_participant__user__username")
            .values_list("cart_work_participant__user__username", "picking_task_id")
        )

        self.client.force_authenticate(self.gdy_worker)
        worker_response = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": cart_work_session_id})
        self.client.force_authenticate(self.gdy_leader)
        leader_response = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": cart_work_session_id})

        self.assertEqual(worker_response.status_code, status.HTTP_200_OK)
        self.assertEqual(leader_response.status_code, status.HTTP_200_OK)
        self.assertEqual(worker_response.data["participant"]["username"], "GDY_WORKER")
        self.assertEqual(leader_response.data["participant"]["username"], "GDY_LEADER")
        self.assertNotEqual(
            worker_response.data["current_instruction"]["picking_task_id"],
            leader_response.data["current_instruction"]["picking_task_id"],
        )
        self.assertEqual(worker_response.data["cart_work_session"]["picking_job"]["progress_percent"], 0)
        self.assertEqual(leader_response.data["cart_work_session"]["picking_job"]["progress_percent"], 0)
        self.assertEqual(len(worker_response.data["cart_work_session"]["participants"]), 2)
        self.assertEqual(len(leader_response.data["cart_work_session"]["participants"]), 2)
        self.assertEqual(
            claim_snapshot,
            list(
                PickingTaskClaim.objects.filter(status=PickingTaskClaim.Status.CLAIMED)
                .order_by("cart_work_participant__user__username")
                .values_list("cart_work_participant__user__username", "picking_task_id")
            ),
        )

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

    def test_specific_pick_this_line_claims_exact_task_and_survives_polling(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        _, start, task_1, task_2, task_3 = self.prepare_three_location_claim_job()
        cart_work_session_id = start.data["cart_work_session"]["id"]

        claim_second = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "specific", "picking_task_id": task_2.id},
            format="json",
        )
        claim_third = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "specific", "picking_task_id": task_3.id},
            format="json",
        )
        poll = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": cart_work_session_id})

        self.assertEqual(claim_second.status_code, status.HTTP_200_OK)
        self.assertEqual(claim_second.data["current_instruction"]["picking_task_id"], task_2.id)
        self.assertEqual(claim_second.data["current_instruction"]["product"]["sku"], "JOB-B")
        self.assertEqual(claim_second.data["current_instruction"]["location"]["code"], "A-01-02")
        self.assertEqual(claim_third.status_code, status.HTTP_200_OK)
        self.assertEqual(claim_third.data["current_instruction"]["picking_task_id"], task_3.id)
        self.assertEqual(claim_third.data["current_instruction"]["product"]["sku"], "JOB-A")
        self.assertEqual(claim_third.data["current_instruction"]["location"]["code"], "A-02-01")
        self.assertEqual(poll.status_code, status.HTTP_200_OK)
        self.assertEqual(poll.data["current_instruction"]["picking_task_id"], task_3.id)
        self.assertFalse(PickingTaskClaim.objects.filter(picking_task=task_1, status=PickingTaskClaim.Status.CLAIMED).exists())
        self.assertFalse(PickingTaskClaim.objects.filter(picking_task=task_2, status=PickingTaskClaim.Status.CLAIMED).exists())
        self.assertTrue(PickingTaskClaim.objects.filter(picking_task=task_3, status=PickingTaskClaim.Status.CLAIMED).exists())

    def test_beginning_and_end_use_canonical_manifest_order(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        _, start, task_1, _, task_3 = self.prepare_three_location_claim_job()
        cart_work_session_id = start.data["cart_work_session"]["id"]

        end_response = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "end"},
            format="json",
        )
        beginning_response = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "beginning"},
            format="json",
        )

        self.assertEqual(end_response.status_code, status.HTTP_200_OK)
        self.assertEqual(end_response.data["current_instruction"]["picking_task_id"], task_3.id)
        self.assertEqual(end_response.data["current_instruction"]["location"]["code"], "A-02-01")
        self.assertEqual(beginning_response.status_code, status.HTTP_200_OK)
        self.assertEqual(beginning_response.data["current_instruction"]["picking_task_id"], task_1.id)
        self.assertEqual(beginning_response.data["current_instruction"]["location"]["code"], "A-01-01")

    def test_end_direction_persists_after_completing_line(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        job, start, _, _, task_3 = self.prepare_three_location_claim_job()
        fourth_location = Location.objects.create(
            branch=self.branch,
            code="A-02-02",
            name="A-02-02",
            location_type=Location.LocationType.PICKING,
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=fourth_location,
            product=self.product_b,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        fourth_order = self.create_order("JOB-ORDER-4", self.run_2, self.product_b)
        fourth_task = self.create_task(fourth_order.lines.first())
        fourth_task.source_location = fourth_location
        fourth_task.save(update_fields=["source_location", "updated_at"])
        PickingJobTask.objects.create(picking_job=job, picking_task=fourth_task)
        cart_work_session_id = start.data["cart_work_session"]["id"]

        end_claim = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "end"},
            format="json",
        )
        self.assertEqual(end_claim.status_code, status.HTTP_200_OK)
        self.assertEqual(end_claim.data["current_instruction"]["picking_task_id"], fourth_task.id)
        self.assertEqual(end_claim.data["participant"]["picking_direction"], "end")
        self.confirm_location(cart_work_session_id, "A-02-02")
        pick = self.client.post(
            "/api/scanner/picking/pick/",
            {"cart_work_session_id": cart_work_session_id, "product_code": "JOB-B", "quantity": "1"},
            format="json",
        )

        self.assertEqual(pick.status_code, status.HTTP_200_OK)
        self.assertEqual(pick.data["current_instruction"]["picking_task_id"], task_3.id)
        self.assertEqual(pick.data["current_instruction"]["location"]["code"], "A-02-01")
        self.assertEqual(pick.data["participant"]["picking_direction"], "end")
        self.assertEqual(CartWorkParticipant.objects.get(user=self.gdy_worker).picking_direction, "end")

    def test_manual_selection_waits_after_completion_instead_of_resetting_to_beginning(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        _, start, task_1, task_2, _ = self.prepare_three_location_claim_job()
        cart_work_session_id = start.data["cart_work_session"]["id"]

        manual_claim = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "specific", "picking_task_id": task_2.id},
            format="json",
        )
        self.confirm_location(cart_work_session_id, "A-01-02")
        pick = self.client.post(
            "/api/scanner/picking/pick/",
            {"cart_work_session_id": cart_work_session_id, "product_code": "JOB-B", "quantity": "1"},
            format="json",
        )

        self.assertEqual(manual_claim.status_code, status.HTTP_200_OK)
        self.assertEqual(manual_claim.data["participant"]["picking_direction"], "manual")
        self.assertEqual(pick.status_code, status.HTTP_200_OK)
        self.assertEqual(pick.data["state"], "waiting_for_available_line")
        self.assertIsNone(pick.data["current_instruction"])
        self.assertEqual(pick.data["participant"]["picking_direction"], "manual")
        self.assertTrue(PickingTask.objects.filter(pk=task_1.id, quantity_picked=Decimal("0.000")).exists())

    def test_participant_waiting_does_not_complete_shared_cart_when_other_claim_remains(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.gdy_worker)
        _, start, task_1, task_2, _ = self.prepare_three_location_claim_job()
        cart_work_session_id = start.data["cart_work_session"]["id"]
        self.client.force_authenticate(self.gdy_leader)
        leader_claim = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "specific", "picking_task_id": task_2.id},
            format="json",
        )
        self.assertEqual(leader_claim.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.gdy_worker)
        worker_claim = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "specific", "picking_task_id": task_1.id},
            format="json",
        )
        self.assertEqual(worker_claim.status_code, status.HTTP_200_OK)
        self.confirm_location(cart_work_session_id, "A-01-01")
        worker_pick = self.client.post(
            "/api/scanner/picking/pick/",
            {"cart_work_session_id": cart_work_session_id, "product_code": "JOB-A", "quantity": "1"},
            format="json",
        )

        self.assertEqual(worker_pick.status_code, status.HTTP_200_OK)
        self.assertEqual(worker_pick.data["state"], "waiting_for_available_line")
        self.assertEqual(worker_pick.data["participant"]["participant_work_state"], "waiting_for_available_line")
        self.assertEqual(worker_pick.data["cart_work_session"]["status"], CartWorkSession.Status.ACTIVE)
        self.assertTrue(PickingTaskClaim.objects.filter(picking_task=task_2, status=PickingTaskClaim.Status.CLAIMED).exists())
        self.assertTrue(PickingTask.objects.filter(pk=task_2.id, quantity_picked=Decimal("0.000")).exists())

    def test_specific_claim_rejects_line_handled_by_another_worker(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        UserBranchMembership.objects.create(user=self.gdy_leader, branch=self.branch, role=UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.gdy_worker)
        _, start, task_1, _, _ = self.prepare_three_location_claim_job()
        cart_work_session_id = start.data["cart_work_session"]["id"]

        self.client.force_authenticate(self.gdy_leader)
        response = self.client.post(
            "/api/scanner/cart-work/claim/",
            {"cart_work_session_id": cart_work_session_id, "mode": "specific", "picking_task_id": task_1.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already handled", response.data["detail"])

    def test_natural_manifest_order_places_a02_before_a10(self):
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        a10_location = Location.objects.create(
            branch=self.branch,
            code="A-10-01",
            name="A-10-01",
            location_type=Location.LocationType.PICKING,
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=a10_location,
            product=self.product_b,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("0"),
        )
        _, start, _, _, _ = self.prepare_three_location_claim_job()
        a10_order = self.create_order("JOB-ORDER-10", self.run_2, self.product_b)
        a10_task = self.create_task(a10_order.lines.first())
        a10_task.source_location = a10_location
        a10_task.save(update_fields=["source_location", "updated_at"])
        PickingJobTask.objects.create(picking_job=PickingJob.objects.get(), picking_task=a10_task)

        response = self.client.get("/api/scanner/cart-work/current/", {"cart_work_session_id": start.data["cart_work_session"]["id"]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        location_codes = [task["source_location_code"] for task in response.data["tasks"]]
        self.assertLess(location_codes.index("A-02-01"), location_codes.index("A-10-01"))

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
                "location_code": "J-01-01",
                "product_code": "JOB-A",
                "quantity": "2.000",
                "worker_code": "DEMO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("whole number", response.data["detail"])
        participant = CartWorkParticipant.objects.get(cart_work_session_id=cart_work_session_id, user=self.demo_user)
        self.assertEqual(participant.confirmed_location_id, self.location.id)

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
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
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
        leave_response = self.client.post(
            "/api/scanner/cart-work/leave/",
            {"cart_work_session_id": cart_work_session_id},
            format="json",
        )
        self.assertEqual(leave_response.status_code, status.HTTP_200_OK)
        UserBranchMembership.objects.create(user=self.gdy_worker, branch=self.branch, role=UserBranchMembership.Role.WORKER)
        self.client.force_authenticate(self.gdy_worker)
        join_response = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "WOZEK-01"}, format="json")
        self.assertEqual(join_response.status_code, status.HTTP_200_OK)
        claim_response = self.client.post(
            "/api/scanner/cart-work/claim/",
            {
                "cart_work_session_id": cart_work_session_id,
                "picking_task_id": self.task_2.id,
                "mode": "specific",
            },
            format="json",
        )
        self.assertEqual(claim_response.status_code, status.HTTP_200_OK)
        confirm_response = self.confirm_location(cart_work_session_id)
        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK)
        second_pick_response = self.client.post(
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
        self.assertEqual(second_pick_response.status_code, status.HTTP_200_OK)

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


class CriticalPickingControlFixtureMixin:
    def setUp(self):
        self.branch = Branch.objects.create(code="PCK", name="Critical Picking", city="Gdynia", country="Poland")
        self.unrelated_branch = Branch.objects.create(code="OTH", name="Other Branch", city="Gdansk", country="Poland")
        self.worker = create_branch_user("PCK_WORKER", self.branch, UserBranchMembership.Role.WORKER)
        self.control_worker = create_branch_user("PCK_CONTROL", self.branch, UserBranchMembership.Role.WORKER)
        self.leader = create_branch_user("PCK_LEADER", self.branch, UserBranchMembership.Role.LEADER)
        self.unrelated_worker = create_branch_user("OTH_WORKER", self.unrelated_branch, UserBranchMembership.Role.WORKER)
        self.unrelated_leader = create_branch_user("OTH_LEADER", self.unrelated_branch, UserBranchMembership.Role.LEADER)
        self.location_a = Location.objects.create(
            branch=self.branch,
            code="PCK-A-01",
            name="PCK-A-01",
            location_type=Location.LocationType.PICKING,
        )
        self.location_b = Location.objects.create(
            branch=self.branch,
            code="PCK-B-01",
            name="PCK-B-01",
            location_type=Location.LocationType.PICKING,
        )
        self.wrong_location = Location.objects.create(
            branch=self.branch,
            code="PCK-Z-99",
            name="PCK-Z-99",
            location_type=Location.LocationType.PICKING,
        )
        self.unconfirmed_location = Location.objects.create(
            branch=self.branch,
            code="UNCONFIRMED",
            name="UNCONFIRMED",
            location_type=Location.LocationType.RECEIVING,
        )
        self.unrelated_location = Location.objects.create(
            branch=self.unrelated_branch,
            code="OTH-A-01",
            name="OTH-A-01",
            location_type=Location.LocationType.PICKING,
        )
        self.product_a = Product.objects.create(
            sku="PCK-A",
            name="Critical Pick Product A",
            barcode="770000000001",
            unit_of_measure="pcs",
        )
        self.product_b = Product.objects.create(
            sku="PCK-B",
            name="Critical Pick Product B",
            barcode="770000000002",
            unit_of_measure="pcs",
        )
        self.unrelated_product = Product.objects.create(
            sku="OTH-P",
            name="Other Branch Product",
            barcode="779900000001",
            unit_of_measure="pcs",
        )
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.unconfirmed_location,
            product=self.product_a,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
        )
        InventoryItem.objects.create(
            branch=self.unrelated_branch,
            location=self.unrelated_location,
            product=self.unrelated_product,
            quantity_on_hand=Decimal("9"),
            quantity_reserved=Decimal("0"),
        )

    def create_route_work(self, *, reference, route_code, quantity_a, quantity_b, stock_a, stock_b):
        route = DeliveryRoute.objects.create(branch=self.branch, code=route_code, name=f"{route_code} Route")
        route_run = RouteRun.objects.create(
            route=route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 30),
            sync_time=time(8, 45),
            departure_time=time(12, 0),
            status=RouteRun.Status.OPEN,
        )
        order = Order.objects.create(
            branch=self.branch,
            route_run=route_run,
            external_reference=reference,
            customer_name="Critical Customer",
            customer_alias="CRIT-CUST",
            status=Order.Status.IMPORTED,
        )
        line_a = OrderLine.objects.create(
            order=order,
            product=self.product_a,
            line_number=1,
            quantity_ordered=Decimal(str(quantity_a)),
            quantity_picked=Decimal("0"),
        )
        line_b = OrderLine.objects.create(
            order=order,
            product=self.product_b,
            line_number=2,
            quantity_ordered=Decimal(str(quantity_b)),
            quantity_picked=Decimal("0"),
        )
        task_a = PickingTask.objects.create(
            branch=self.branch,
            order_line=line_a,
            source_location=self.location_a,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal(str(quantity_a)),
        )
        task_b = PickingTask.objects.create(
            branch=self.branch,
            order_line=line_b,
            source_location=self.location_b,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal(str(quantity_b)),
        )
        inventory_a = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location_a,
            product=self.product_a,
            quantity_on_hand=Decimal(str(stock_a)),
            quantity_reserved=Decimal("0"),
        )
        inventory_b = InventoryItem.objects.create(
            branch=self.branch,
            location=self.location_b,
            product=self.product_b,
            quantity_on_hand=Decimal(str(stock_b)),
            quantity_reserved=Decimal("0"),
        )
        return {
            "route_run": route_run,
            "order": order,
            "task_a": task_a,
            "task_b": task_b,
            "inventory_a": inventory_a,
            "inventory_b": inventory_b,
        }

    def create_unrelated_route_work(self, suffix="1"):
        route = DeliveryRoute.objects.create(branch=self.unrelated_branch, code=f"OTH-R-{suffix}", name="Other Route")
        route_run = RouteRun.objects.create(
            route=route,
            service_date=timezone.localdate(),
            run_number=1,
            order_cutoff_time=time(8, 30),
            sync_time=time(8, 45),
            departure_time=time(12, 0),
            status=RouteRun.Status.OPEN,
        )
        order = Order.objects.create(
            branch=self.unrelated_branch,
            route_run=route_run,
            external_reference=f"ORDER-CRIT-PICK-OTHER-{suffix}",
            customer_name="Other Customer",
            status=Order.Status.IMPORTED,
        )
        line = OrderLine.objects.create(
            order=order,
            product=self.unrelated_product,
            line_number=1,
            quantity_ordered=Decimal("1"),
        )
        return PickingTask.objects.create(
            branch=self.unrelated_branch,
            order_line=line,
            source_location=self.unrelated_location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=Decimal("1"),
        )

    def create_job(self, route_run):
        response = self.client.post(
            "/api/scanner/proformas/create-jobs/",
            {"route_run_ids": [route_run.id], "mode": "merged"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return PickingJob.objects.get(id=response.data["jobs"][0]["id"])

    def start_job(self, job, cart_code):
        response = self.client.post(f"/api/scanner/tasks/{job.id}/start/", {"cart_code": cart_code}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["session"]["id"], response.data["cart_work_session"]["id"]

    def confirm_location(self, cart_work_session_id, location_code):
        return self.client.post(
            "/api/scanner/picking/confirm-location/",
            {"cart_work_session_id": cart_work_session_id, "location_code": location_code},
            format="json",
        )

    def pick(self, cart_work_session_id, product_code, quantity):
        return self.client.post(
            "/api/scanner/picking/pick/",
            {"cart_work_session_id": cart_work_session_id, "product_code": product_code, "quantity": str(quantity)},
            format="json",
        )

    def claim(self, cart_work_session_id, mode="beginning", picking_task_id=None):
        payload = {"cart_work_session_id": cart_work_session_id, "mode": mode}
        if picking_task_id is not None:
            payload["picking_task_id"] = picking_task_id
        return self.client.post("/api/scanner/cart-work/claim/", payload, format="json")

    def shortage_challenge(self, cart_work_session_id, quantity):
        return self.client.post(
            "/api/scanner/picking/shortage-challenge/",
            {"cart_work_session_id": cart_work_session_id, "quantity": str(quantity)},
            format="json",
        )

    def report_shortage(self, challenge, operation_id="critical-shortage"):
        return self.client.post(
            "/api/scanner/picking/report-shortage/",
            {
                "challenge_token": challenge.data["challenge_token"],
                "confirmation_code": challenge.data["confirmation_code"],
                "client_operation_id": operation_id,
            },
            format="json",
        )

    def print_label(self, session_id, order_reference):
        return self.client.post(
            "/api/scanner/control/print-label/",
            {"session_id": session_id, "order_reference": order_reference, "printer_code": "CRIT-PRINTER"},
            format="json",
        )

    def prepare(self, session_id, order_reference, product_code, quantity):
        return self.client.post(
            "/api/scanner/picking/prepare/",
            {
                "session_id": session_id,
                "order_reference": order_reference,
                "product_code": product_code,
                "quantity": str(quantity),
            },
            format="json",
        )

    def category_count(self, response, key):
        category = next(item for item in response.data["categories"] if item["key"] == key)
        return category["count"]


class CriticalExactPickingControlIntegrationTests(CriticalPickingControlFixtureMixin, APITestCase):
    def test_exact_picking_to_control_completion_is_branch_scoped_and_immutable(self):
        work = self.create_route_work(
            reference="ORDER-CRIT-PICK-EXACT",
            route_code="CRIT-EXACT",
            quantity_a="3",
            quantity_b="2",
            stock_a="5",
            stock_b="4",
        )
        self.create_unrelated_route_work("visibility")

        unauthenticated = self.client.get("/api/scanner/tasks/")
        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.worker)
        proformas = self.client.get("/api/scanner/proformas/", {"branch": self.branch.id})
        self.assertEqual(proformas.status_code, status.HTTP_200_OK)
        self.assertTrue(any(row["id"] == work["route_run"].id and row["akt"] == 2 for row in proformas.data["results"]))

        job = self.create_job(work["route_run"])
        tasks = self.client.get("/api/scanner/tasks/")
        self.assertEqual(tasks.status_code, status.HTTP_200_OK)
        self.assertTrue(any(row["id"] == job.id and row["total_lines"] == 2 for row in tasks.data["results"]))

        self.client.force_authenticate(self.unrelated_worker)
        unrelated_tasks = self.client.get("/api/scanner/tasks/")
        self.assertEqual(unrelated_tasks.status_code, status.HTTP_200_OK)
        self.assertFalse(any(row["id"] == job.id for row in unrelated_tasks.data["results"]))
        forbidden_start = self.client.post(f"/api/scanner/tasks/{job.id}/start/", {"cart_code": "CART-CRIT-PICK-EXACT"}, format="json")
        self.assertEqual(forbidden_start.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.worker)
        session_id, cart_work_session_id = self.start_job(job, "CART-CRIT-PICK-EXACT")
        duplicate_join = self.client.post("/api/scanner/cart-work/join/", {"cart_barcode": "CART-CRIT-PICK-EXACT"}, format="json")
        self.assertEqual(duplicate_join.status_code, status.HTTP_200_OK)
        self.assertEqual(CartWorkParticipant.objects.filter(cart_work_session_id=cart_work_session_id, user=self.worker).count(), 1)
        direction = self.claim(cart_work_session_id, mode="beginning")
        self.assertEqual(direction.status_code, status.HTTP_200_OK)
        self.assertEqual(direction.data["participant"]["picking_direction"], "beginning")
        participant = CartWorkParticipant.objects.get(cart_work_session_id=cart_work_session_id, user=self.worker)
        self.assertEqual(participant.picking_direction, CartWorkParticipant.PickingDirection.BEGINNING)

        unrelated_task = self.create_unrelated_route_work("substitution")
        substituted_task = self.claim(cart_work_session_id, mode="specific", picking_task_id=unrelated_task.id)
        self.assertEqual(substituted_task.status_code, status.HTTP_404_NOT_FOUND)

        wrong_location = self.confirm_location(cart_work_session_id, self.location_b.code)
        self.assertEqual(wrong_location.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.confirm_location(cart_work_session_id, self.location_a.code).status_code, status.HTTP_200_OK)

        wrong_product = self.pick(cart_work_session_id, self.product_b.sku, "1")
        excessive = self.pick(cart_work_session_id, self.product_a.sku, "4")
        self.assertEqual(wrong_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(excessive.status_code, status.HTTP_400_BAD_REQUEST)
        work["inventory_a"].refresh_from_db()
        self.assertEqual(work["inventory_a"].quantity_on_hand, Decimal("5.000"))
        self.assertFalse(PickingShortage.objects.exists())

        pick_a = self.pick(cart_work_session_id, self.product_a.barcode, "3")
        self.assertEqual(pick_a.status_code, status.HTTP_200_OK)
        self.assertEqual(pick_a.data["current_instruction"]["product"]["sku"], self.product_b.sku)
        self.assertEqual(self.confirm_location(cart_work_session_id, self.location_b.code).status_code, status.HTTP_200_OK)
        pick_b = self.pick(cart_work_session_id, self.product_b.sku, "2")
        duplicate_pick = self.pick(cart_work_session_id, self.product_b.sku, "1")
        self.assertEqual(pick_b.status_code, status.HTTP_200_OK)
        self.assertEqual(duplicate_pick.status_code, status.HTTP_400_BAD_REQUEST)

        work["task_a"].refresh_from_db()
        work["task_b"].refresh_from_db()
        work["inventory_a"].refresh_from_db()
        work["inventory_b"].refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(work["task_a"].quantity_picked, Decimal("3.000"))
        self.assertEqual(work["task_b"].quantity_picked, Decimal("2.000"))
        self.assertEqual(work["inventory_a"].quantity_on_hand, Decimal("2.000"))
        self.assertEqual(work["inventory_b"].quantity_on_hand, Decimal("2.000"))
        self.assertEqual(job.status, PickingJob.Status.PICKED)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.MovementType.PICK).count(), 2)

        self.client.force_authenticate(self.unrelated_worker)
        forbidden_control = self.client.get("/api/scanner/control/cart/", {"cart_code": "CART-CRIT-PICK-EXACT"})
        self.assertEqual(forbidden_control.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.control_worker)
        control_cart = self.client.get("/api/scanner/control/cart/", {"cart_code": "CART-CRIT-PICK-EXACT"})
        self.assertEqual(control_cart.status_code, status.HTTP_200_OK)
        self.assertEqual(len(control_cart.data["items"]), 2)
        self.assertEqual(self.client.get("/api/scanner/control/target/", {"session_id": session_id, "product_code": self.product_a.sku}).status_code, status.HTTP_200_OK)
        self.assertEqual(self.print_label(session_id, work["order"].external_reference).status_code, status.HTTP_200_OK)
        early_finish = self.client.post("/api/scanner/control/finish/", {"session_id": session_id}, format="json")
        self.assertEqual(early_finish.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(self.prepare(session_id, work["order"].external_reference, self.product_a.sku, "3").status_code, status.HTTP_200_OK)
        self.assertEqual(self.prepare(session_id, work["order"].external_reference, self.product_b.sku, "2").status_code, status.HTTP_200_OK)
        over_prepare = self.prepare(session_id, work["order"].external_reference, self.product_b.sku, "1")
        self.assertEqual(over_prepare.status_code, status.HTTP_400_BAD_REQUEST)
        finish = self.client.post("/api/scanner/control/finish/", {"session_id": session_id}, format="json")
        second_finish = self.client.post("/api/scanner/control/finish/", {"session_id": session_id}, format="json")
        self.assertEqual(finish.status_code, status.HTTP_200_OK)
        self.assertEqual(second_finish.status_code, status.HTTP_400_BAD_REQUEST)

        job.refresh_from_db()
        cart_work = CartWorkSession.objects.get(id=cart_work_session_id)
        work["route_run"].refresh_from_db()
        work["task_a"].refresh_from_db()
        work["task_b"].refresh_from_db()
        self.assertEqual(job.status, PickingJob.Status.COMPLETED)
        self.assertEqual(cart_work.status, CartWorkSession.Status.COMPLETED)
        self.assertEqual(cart_work.cart.status, ScannerCart.Status.AVAILABLE)
        self.assertEqual(work["task_a"].status, PickingTask.Status.COMPLETED)
        self.assertEqual(work["task_b"].status, PickingTask.Status.COMPLETED)
        self.assertEqual(work["route_run"].status, RouteRun.Status.READY_TO_CLOSE)
        self.assertFalse(PickingShortage.objects.exists())

        route_detail = self.client.get(f"/api/route-runs/{work['route_run'].id}/")
        exceptions = self.client.get("/api/inventory-exceptions/", {"branch": self.branch.code})
        events = self.client.get("/api/current-events/", {"search": "ORDER-CRIT-PICK-EXACT", "branch": self.branch.code})
        self.assertEqual(route_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(route_detail.data["progress_percent"], 100.0)
        self.assertEqual(route_detail.data["is_ready_to_close"], True)
        self.assertEqual(exceptions.status_code, status.HTTP_200_OK)
        self.assertEqual(self.category_count(exceptions, "picking_shortages"), 0)
        self.assertEqual(events.status_code, status.HTTP_200_OK)
        event_types = {row["event_type"] for row in events.data["results"]}
        self.assertTrue({"pick", "control"}.issubset(event_types))


class CriticalPickingShortageControlIntegrationTests(CriticalPickingControlFixtureMixin, APITestCase):
    def test_location_shortage_remains_actionable_while_picked_goods_are_controlled(self):
        work = self.create_route_work(
            reference="ORDER-CRIT-PICK-SHORT",
            route_code="CRIT-SHORT",
            quantity_a="5",
            quantity_b="2",
            stock_a="5",
            stock_b="2",
        )
        self.client.force_authenticate(self.worker)
        job = self.create_job(work["route_run"])
        session_id, cart_work_session_id = self.start_job(job, "CART-CRIT-PICK-SHORT")

        self.assertEqual(self.confirm_location(cart_work_session_id, self.location_a.code).status_code, status.HTTP_200_OK)
        pick_wrong_product = self.pick(cart_work_session_id, self.product_b.sku, "1")
        self.assertEqual(pick_wrong_product.status_code, status.HTTP_400_BAD_REQUEST)
        first_pick = self.pick(cart_work_session_id, self.product_a.sku, "3")
        self.assertEqual(first_pick.status_code, status.HTTP_200_OK)
        excessive_challenge = self.shortage_challenge(cart_work_session_id, "3")
        self.assertEqual(excessive_challenge.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PickingShortage.objects.exists())

        challenge = self.shortage_challenge(cart_work_session_id, "2")
        self.assertEqual(challenge.status_code, status.HTTP_200_OK)
        shortage_response = self.report_shortage(challenge, operation_id="critical-shortage-same-op")
        duplicate_shortage_response = self.report_shortage(challenge, operation_id="critical-shortage-same-op")
        self.assertEqual(shortage_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(duplicate_shortage_response.status_code, status.HTTP_200_OK)
        self.assertEqual(PickingShortage.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.MovementType.PICKING_SHORTAGE).count(), 1)
        self.assertEqual(shortage_response.data["replenishment_request"]["quantity"], "2.000")

        shortage = PickingShortage.objects.get()
        replenishment = ReplenishmentRequest.objects.get()
        work["task_a"].refresh_from_db()
        work["inventory_a"].refresh_from_db()
        unconfirmed = InventoryItem.objects.get(branch=self.branch, location=self.unconfirmed_location, product=self.product_a)
        self.assertEqual(shortage.quantity, Decimal("2.000"))
        self.assertEqual(shortage.customer_unfulfilled_quantity, Decimal("2.000"))
        self.assertEqual(shortage.status, PickingShortage.Status.OPEN)
        self.assertEqual(replenishment.status, ReplenishmentRequest.Status.PENDING_ORDER)
        self.assertEqual(work["task_a"].quantity_picked, Decimal("3.000"))
        self.assertEqual(work["task_a"].shortage_quantity, Decimal("2.000"))
        self.assertEqual(work["inventory_a"].quantity_on_hand, Decimal("0.000"))
        self.assertEqual(unconfirmed.quantity_on_hand, Decimal("2.000"))

        self.assertEqual(self.confirm_location(cart_work_session_id, self.location_b.code).status_code, status.HTTP_200_OK)
        self.assertEqual(self.pick(cart_work_session_id, self.product_b.barcode, "2").status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, PickingJob.Status.PICKED)

        self.client.force_authenticate(self.unrelated_worker)
        forbidden_shortages = self.client.get("/api/picking-shortages/", {"branch": self.branch.code})
        self.assertEqual(forbidden_shortages.status_code, status.HTTP_403_FORBIDDEN)
        forbidden_control = self.client.get("/api/scanner/control/cart/", {"cart_code": "CART-CRIT-PICK-SHORT"})
        self.assertEqual(forbidden_control.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.worker)
        worker_confirm_missing = self.client.post(
            f"/api/picking-shortages/{shortage.id}/confirm-missing/",
            {"worker_code": self.worker.username},
            format="json",
        )
        self.assertEqual(worker_confirm_missing.status_code, status.HTTP_403_FORBIDDEN)
        shortage.refresh_from_db()
        self.assertEqual(shortage.status, PickingShortage.Status.OPEN)

        shortages = self.client.get("/api/picking-shortages/", {"branch": self.branch.code, "search": "ORDER-CRIT-PICK-SHORT"})
        exceptions = self.client.get("/api/inventory-exceptions/", {"branch": self.branch.code})
        self.assertEqual(shortages.status_code, status.HTTP_200_OK)
        self.assertEqual(len(shortages.data["results"]), 1)
        self.assertEqual(shortages.data["results"][0]["reported_by_username"], self.worker.username)
        self.assertEqual(shortages.data["results"][0]["product_sku"], self.product_a.sku)
        self.assertEqual(shortages.data["results"][0]["replenishment_quantity"], "2.000")
        self.assertEqual(exceptions.status_code, status.HTTP_200_OK)
        self.assertEqual(self.category_count(exceptions, "picking_shortages"), 1)
        self.assertEqual(self.category_count(exceptions, "replenishment"), 1)

        self.client.force_authenticate(self.control_worker)
        self.assertEqual(self.print_label(session_id, work["order"].external_reference).status_code, status.HTTP_200_OK)
        control_a = self.prepare(session_id, work["order"].external_reference, self.product_a.sku, "3")
        control_b = self.prepare(session_id, work["order"].external_reference, self.product_b.sku, "2")
        missing_units_not_on_cart = self.prepare(session_id, work["order"].external_reference, self.product_a.sku, "2")
        self.assertEqual(control_a.status_code, status.HTTP_200_OK)
        self.assertEqual(control_b.status_code, status.HTTP_200_OK)
        self.assertEqual(missing_units_not_on_cart.status_code, status.HTTP_400_BAD_REQUEST)

        finish = self.client.post("/api/scanner/control/finish/", {"session_id": session_id}, format="json")
        after_finish_pick = self.pick(cart_work_session_id, self.product_b.sku, "1")
        self.assertEqual(finish.status_code, status.HTTP_200_OK)
        self.assertEqual(after_finish_pick.status_code, status.HTTP_400_BAD_REQUEST)

        job.refresh_from_db()
        cart_work = CartWorkSession.objects.get(id=cart_work_session_id)
        work["route_run"].refresh_from_db()
        work["task_a"].refresh_from_db()
        work["task_b"].refresh_from_db()
        work["inventory_b"].refresh_from_db()
        shortage.refresh_from_db()
        self.assertEqual(job.status, PickingJob.Status.PICKED)
        self.assertEqual(cart_work.status, CartWorkSession.Status.COMPLETED)
        self.assertEqual(work["route_run"].status, RouteRun.Status.OPEN)
        self.assertEqual(work["task_a"].quantity_prepared, Decimal("3.000"))
        self.assertEqual(work["task_a"].status, PickingTask.Status.IN_PROGRESS)
        self.assertEqual(work["task_b"].quantity_prepared, Decimal("2.000"))
        self.assertEqual(work["task_b"].status, PickingTask.Status.COMPLETED)
        self.assertEqual(work["inventory_b"].quantity_on_hand, Decimal("0.000"))
        self.assertEqual(shortage.status, PickingShortage.Status.OPEN)

        route_detail = self.client.get(f"/api/route-runs/{work['route_run'].id}/")
        events = self.client.get("/api/current-events/", {"search": "ORDER-CRIT-PICK-SHORT", "branch": self.branch.code})
        replenishment_events = self.client.get("/api/current-events/", {"search": replenishment.reference, "branch": self.branch.code})
        self.assertEqual(route_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(route_detail.data["is_ready_to_close"], False)
        self.assertEqual(events.status_code, status.HTTP_200_OK)
        self.assertEqual(replenishment_events.status_code, status.HTTP_200_OK)
        event_types = {row["event_type"] for row in events.data["results"]}
        self.assertTrue({"pick", "picking_location_shortage", "control"}.issubset(event_types))
        self.assertTrue(any(row["event_type"] == "replenishment_requested" for row in replenishment_events.data["results"]))


class SeedDemoDataCommandTests(APITestCase):
    def run_seed(self):
        output = StringIO()
        call_command("seed_demo_data", stdout=output)
        self.client.force_authenticate(User.objects.get(username="DEMO"))
        return output.getvalue()

    def available_routes(self):
        response = self.client.get("/api/scanner/proformas/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [row for row in response.data["results"] if row["is_selectable"]]

    def available_route_ids(self):
        return [row["id"] for row in self.available_routes()]

    def available_route_ids_for_same_branch(self, count=2):
        routes_by_branch = {}
        for route in self.available_routes():
            routes_by_branch.setdefault(route["branch_code"], []).append(route["id"])
        for route_ids in routes_by_branch.values():
            if len(route_ids) >= count:
                return route_ids[:count]
        return []

    def start_demo_job(self):
        route_ids = self.available_route_ids_for_same_branch()
        self.assertGreaterEqual(len(route_ids), 2)
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
        self.user = create_branch_user("LFC_LEADER", self.branch, UserBranchMembership.Role.LEADER)
        self.client.force_authenticate(self.user)

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
        past_departure = timezone.localtime() - timezone.timedelta(hours=1)
        self.route_run.service_date = past_departure.date()
        self.route_run.departure_time = past_departure.time()
        self.route_run.save(update_fields=["service_date", "departure_time", "updated_at"])
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
