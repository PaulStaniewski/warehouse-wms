import re
from decimal import Decimal

from rest_framework import serializers

from operations.models import (
    AuditLog,
    DeliveryRoute,
    Order,
    OrderLine,
    PickingShortage,
    PickingShortageAllocation,
    PickingTaskReallocation,
    PickingTask,
    ReplenishmentRequest,
    ReturnBatch,
    ReturnLine,
    RouteRun,
    StockMovement,
    TransferDiscrepancy,
    TransferDiscrepancyItem,
    TransferDiscrepancyManualReconciliationDecision,
    TransferDiscrepancyReconciliation,
    TransferDiscrepancyRecovery,
    TransferDiscrepancyShortageConfirmation,
    TransferDiscrepancySourceStockRecovery,
    TransferDiscrepancySourceStockVerification,
    TransferDiscrepancySourceReview,
    TransferDiscrepancyTransitInvestigation,
)
from operations.services import (
    discrepancy_line_remaining,
    get_discrepancy_investigation_totals,
    reconciliation_next_action,
    get_source_verification_totals,
    source_verification_item_remaining,
    source_verification_next_action,
    transit_investigation_next_action,
)
from operations.services import is_route_late, is_route_work_fully_prepared, route_close_result


class DeliveryRouteSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = DeliveryRoute
        fields = [
            "id",
            "branch",
            "branch_code",
            "code",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        ]


class RouteRunSerializer(serializers.ModelSerializer):
    route_code = serializers.CharField(source="route.code", read_only=True)
    route_name = serializers.CharField(source="route.name", read_only=True)
    branch = serializers.IntegerField(source="route.branch_id", read_only=True)
    branch_code = serializers.CharField(source="route.branch.code", read_only=True)
    orders_count = serializers.IntegerField(read_only=True)
    order_lines_count = serializers.IntegerField(read_only=True)
    picked_lines_count = serializers.IntegerField(read_only=True)
    pending_lines_count = serializers.IntegerField(read_only=True)
    has_pending_work = serializers.BooleanField(read_only=True)
    is_urgent = serializers.BooleanField(read_only=True)
    is_selectable = serializers.BooleanField(read_only=True)
    total_picking_tasks = serializers.SerializerMethodField()
    open_picking_tasks = serializers.SerializerMethodField()
    in_progress_picking_tasks = serializers.SerializerMethodField()
    picked_picking_tasks = serializers.SerializerMethodField()
    completed_picking_tasks = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    last_activity_at = serializers.SerializerMethodField()
    is_ready_to_close = serializers.SerializerMethodField()
    is_late = serializers.SerializerMethodField()
    close_result = serializers.SerializerMethodField()

    def _get_picking_tasks(self, obj: RouteRun):
        cache_name = "_monitor_picking_tasks"
        if not hasattr(obj, cache_name):
            setattr(
                obj,
                cache_name,
                list(PickingTask.objects.filter(order_line__order__route_run=obj)),
            )
        return getattr(obj, cache_name)

    def get_total_picking_tasks(self, obj: RouteRun) -> int:
        return len(self._get_picking_tasks(obj))

    def get_open_picking_tasks(self, obj: RouteRun) -> int:
        return sum(
            task.status in {PickingTask.Status.OPEN, PickingTask.Status.ASSIGNED}
            for task in self._get_picking_tasks(obj)
        )

    def get_in_progress_picking_tasks(self, obj: RouteRun) -> int:
        return sum(task.status == PickingTask.Status.IN_PROGRESS for task in self._get_picking_tasks(obj))

    def get_picked_picking_tasks(self, obj: RouteRun) -> int:
        return sum(task.status == PickingTask.Status.PICKED for task in self._get_picking_tasks(obj))

    def get_completed_picking_tasks(self, obj: RouteRun) -> int:
        return sum(task.status == PickingTask.Status.COMPLETED for task in self._get_picking_tasks(obj))

    def get_progress_percent(self, obj: RouteRun) -> float:
        tasks = self._get_picking_tasks(obj)
        total_quantity = sum((task.quantity_to_pick - task.shortage_quantity for task in tasks), Decimal("0"))
        picked_quantity = sum((task.quantity_picked for task in tasks), Decimal("0"))

        if total_quantity <= 0:
            return 0

        return round(float((picked_quantity / total_quantity) * 100), 1)

    def get_last_activity_at(self, obj: RouteRun) -> str | None:
        task_ids = [str(task.id) for task in self._get_picking_tasks(obj)]
        if not task_ids:
            return None

        audit_log = (
            AuditLog.objects.filter(entity_name="PickingTask", entity_id__in=task_ids)
            .order_by("-created_at")
            .first()
        )
        if audit_log is None:
            return None

        return audit_log.created_at.isoformat()

    def get_is_ready_to_close(self, obj: RouteRun) -> bool:
        return obj.status == RouteRun.Status.READY_TO_CLOSE or is_route_work_fully_prepared(obj)

    def get_is_late(self, obj: RouteRun) -> bool:
        if obj.status == RouteRun.Status.CLOSED:
            return obj.closed_at is not None and is_route_late(obj, obj.closed_at)
        return is_route_late(obj)

    def get_close_result(self, obj: RouteRun) -> str:
        return route_close_result(obj)

    class Meta:
        model = RouteRun
        fields = [
            "id",
            "route",
            "branch",
            "route_code",
            "route_name",
            "branch_code",
            "service_date",
            "run_number",
            "order_cutoff_time",
            "sync_time",
            "departure_time",
            "status",
            "orders_count",
            "order_lines_count",
            "picked_lines_count",
            "pending_lines_count",
            "has_pending_work",
            "is_urgent",
            "is_selectable",
            "total_picking_tasks",
            "open_picking_tasks",
            "in_progress_picking_tasks",
            "picked_picking_tasks",
            "completed_picking_tasks",
            "progress_percent",
            "last_activity_at",
            "is_ready_to_close",
            "is_late",
            "close_result",
            "ready_at",
            "documents_printed_at",
            "closed_at",
            "created_at",
            "updated_at",
        ]


class OrderSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    route_run_label = serializers.SerializerMethodField()

    def get_route_run_label(self, obj: Order) -> str | None:
        if obj.route_run is None:
            return None
        return str(obj.route_run)

    class Meta:
        model = Order
        fields = [
            "id",
            "branch",
            "branch_code",
            "route_run",
            "route_run_label",
            "external_reference",
            "customer_name",
            "customer_alias",
            "status",
            "requested_ship_date",
            "created_at",
            "updated_at",
        ]


class OrderLineSerializer(serializers.ModelSerializer):
    order_reference = serializers.CharField(source="order.external_reference", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    remaining_quantity = serializers.SerializerMethodField()
    source_location_code = serializers.SerializerMethodField()
    source_location_name = serializers.SerializerMethodField()

    def get_remaining_quantity(self, obj: OrderLine) -> str:
        return str(obj.quantity_ordered - obj.quantity_picked)

    def get_source_location_code(self, obj: OrderLine) -> str | None:
        picking_task = obj.picking_tasks.select_related("source_location").first()
        if picking_task is None:
            return None
        return picking_task.source_location.code

    def get_source_location_name(self, obj: OrderLine) -> str | None:
        picking_task = obj.picking_tasks.select_related("source_location").first()
        if picking_task is None:
            return None
        return picking_task.source_location.name

    class Meta:
        model = OrderLine
        fields = [
            "id",
            "order",
            "order_reference",
            "product",
            "product_sku",
            "product_name",
            "line_number",
            "quantity_ordered",
            "quantity_picked",
            "remaining_quantity",
            "source_location_code",
            "source_location_name",
            "created_at",
            "updated_at",
        ]


class ReturnBatchSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = ReturnBatch
        fields = [
            "id",
            "branch",
            "branch_code",
            "reference",
            "status",
            "received_at",
            "created_at",
            "updated_at",
        ]


class ReturnLineSerializer(serializers.ModelSerializer):
    return_reference = serializers.CharField(source="return_batch.reference", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = ReturnLine
        fields = [
            "id",
            "return_batch",
            "return_reference",
            "product",
            "product_sku",
            "line_number",
            "quantity",
            "condition",
            "created_at",
            "updated_at",
        ]


class PickingTaskSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    order_reference = serializers.CharField(source="order_line.order.external_reference", read_only=True)
    product_sku = serializers.CharField(source="order_line.product.sku", read_only=True)
    product_name = serializers.CharField(source="order_line.product.name", read_only=True)
    product_brand = serializers.CharField(source="order_line.product.brand", read_only=True)
    product_description = serializers.CharField(source="order_line.product.description", read_only=True)
    product_image_url = serializers.CharField(source="order_line.product.image_url", read_only=True)
    source_location_code = serializers.CharField(source="source_location.code", read_only=True)
    source_location_name = serializers.CharField(source="source_location.name", read_only=True)
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)
    remaining_quantity = serializers.SerializerMethodField()
    remaining_to_prepare = serializers.SerializerMethodField()
    is_replacement_pick = serializers.SerializerMethodField()
    replacement_shortage_reference = serializers.SerializerMethodField()
    original_shortage_location_code = serializers.SerializerMethodField()
    is_system_reallocated_pick = serializers.SerializerMethodField()
    reallocation_reason = serializers.SerializerMethodField()
    reallocated_from_location_code = serializers.SerializerMethodField()

    def get_remaining_quantity(self, obj: PickingTask) -> str:
        return str(obj.quantity_to_pick - obj.quantity_picked - obj.shortage_quantity)

    def get_remaining_to_prepare(self, obj: PickingTask) -> str:
        return str(obj.quantity_to_pick - obj.quantity_prepared)

    def _replacement_allocation(self, obj: PickingTask):
        return getattr(obj, "shortage_replacement_allocation", None)

    def get_is_replacement_pick(self, obj: PickingTask) -> bool:
        return self._replacement_allocation(obj) is not None

    def get_replacement_shortage_reference(self, obj: PickingTask) -> str | None:
        allocation = self._replacement_allocation(obj)
        if allocation is None:
            return None
        return allocation.shortage.reference

    def get_original_shortage_location_code(self, obj: PickingTask) -> str | None:
        allocation = self._replacement_allocation(obj)
        if allocation is None:
            return None
        return allocation.shortage.reported_location.code

    def _system_reallocation(self, obj: PickingTask):
        return getattr(obj, "system_reallocation_source", None)

    def get_is_system_reallocated_pick(self, obj: PickingTask) -> bool:
        return self._system_reallocation(obj) is not None

    def get_reallocation_reason(self, obj: PickingTask) -> str | None:
        reallocation = self._system_reallocation(obj)
        if reallocation is None:
            return None
        return "Reallocated because no system stock remained at " + reallocation.original_location.code

    def get_reallocated_from_location_code(self, obj: PickingTask) -> str | None:
        reallocation = self._system_reallocation(obj)
        if reallocation is None:
            return None
        return reallocation.original_location.code

    class Meta:
        model = PickingTask
        fields = [
            "id",
            "branch",
            "branch_code",
            "order_line",
            "order_reference",
            "product_sku",
            "product_name",
            "product_brand",
            "product_description",
            "product_image_url",
            "source_location",
            "source_location_code",
            "source_location_name",
            "assigned_to",
            "assigned_to_username",
            "status",
            "quantity_to_pick",
            "quantity_picked",
            "shortage_quantity",
            "quantity_prepared",
            "remaining_quantity",
            "remaining_to_prepare",
            "is_replacement_pick",
            "replacement_shortage_reference",
            "original_shortage_location_code",
            "is_system_reallocated_pick",
            "reallocation_reason",
            "reallocated_from_location_code",
            "created_at",
            "updated_at",
        ]


class PickingShortageSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_brand = serializers.CharField(source="product.brand", read_only=True)
    reported_location_code = serializers.CharField(source="reported_location.code", read_only=True)
    unconfirmed_location_code = serializers.CharField(source="unconfirmed_location.code", read_only=True)
    found_location_code = serializers.CharField(source="found_location.code", read_only=True)
    cart_code = serializers.CharField(source="cart.code", read_only=True)
    order_reference = serializers.CharField(source="order.external_reference", read_only=True)
    reported_by_username = serializers.CharField(source="reported_by.username", read_only=True)
    found_by_username = serializers.CharField(source="found_by.username", read_only=True)
    confirmed_missing_by_username = serializers.CharField(source="confirmed_missing_by.username", read_only=True)
    unresolved_quantity = serializers.SerializerMethodField()
    location_missing_quantity = serializers.SerializerMethodField()
    unresolved_unconfirmed_quantity = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    allocations = serializers.SerializerMethodField()
    replenishment_reference = serializers.SerializerMethodField()
    replenishment_quantity = serializers.SerializerMethodField()

    def get_unresolved_quantity(self, obj: PickingShortage) -> str:
        return str(obj.unresolved_quantity)

    def get_location_missing_quantity(self, obj: PickingShortage) -> str:
        return str(obj.location_missing_quantity)

    def get_unresolved_unconfirmed_quantity(self, obj: PickingShortage) -> str:
        return str(obj.unresolved_unconfirmed_quantity)

    def get_allocations(self, obj: PickingShortage) -> list[dict]:
        allocations = obj.allocations.select_related("source_location", "replacement_picking_task").order_by("source_location__code", "id")
        return [
            {
                "id": allocation.id,
                "location_code": allocation.source_location.code,
                "quantity": str(allocation.quantity),
                "picked_quantity": str(allocation.picked_quantity),
                "status": allocation.status,
                "status_label": allocation.get_status_display(),
                "replacement_picking_task": allocation.replacement_picking_task_id,
            }
            for allocation in allocations
        ]

    def get_replenishment_reference(self, obj: PickingShortage) -> str | None:
        request = getattr(obj, "replenishment_request", None)
        return request.reference if request is not None else None

    def get_replenishment_quantity(self, obj: PickingShortage) -> str | None:
        request = getattr(obj, "replenishment_request", None)
        return str(request.quantity) if request is not None else None

    class Meta:
        model = PickingShortage
        fields = [
            "id",
            "reference",
            "picking_task",
            "order",
            "order_reference",
            "branch",
            "branch_code",
            "product",
            "product_sku",
            "product_name",
            "product_brand",
            "reported_location",
            "reported_location_code",
            "unconfirmed_location",
            "unconfirmed_location_code",
            "cart",
            "cart_code",
            "quantity",
            "location_missing_quantity",
            "alternative_allocated_quantity",
            "customer_unfulfilled_quantity",
            "recovered_quantity",
            "confirmed_missing_quantity",
            "unresolved_quantity",
            "unresolved_unconfirmed_quantity",
            "customer_alias_snapshot",
            "reported_by",
            "reported_by_username",
            "reported_by_worker_code",
            "reported_at",
            "status",
            "status_label",
            "found_location",
            "found_location_code",
            "found_by",
            "found_by_username",
            "found_by_worker_code",
            "found_at",
            "confirmed_missing_by",
            "confirmed_missing_by_username",
            "confirmed_missing_by_worker_code",
            "confirmed_missing_at",
            "note",
            "allocations",
            "replenishment_reference",
            "replenishment_quantity",
            "created_at",
            "updated_at",
        ]


class ReplenishmentRequestSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_brand = serializers.CharField(source="product.brand", read_only=True)
    shortage_reference = serializers.SerializerMethodField()
    cart_code = serializers.SerializerMethodField()
    reported_location_code = serializers.SerializerMethodField()
    reported_by_worker_code = serializers.SerializerMethodField()
    reported_at = serializers.SerializerMethodField()
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    ordered_by_username = serializers.CharField(source="ordered_by.username", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    ax_payload = serializers.SerializerMethodField()

    def get_ax_payload(self, obj: ReplenishmentRequest) -> dict:
        return {
            "request_reference": obj.reference,
            "customer_alias": obj.customer_alias,
            "product_sku": obj.product.sku,
            "quantity": str(obj.quantity),
            "order_reference": obj.order_reference,
            "branch": obj.branch.code,
            "reason": obj.reason,
        }

    def get_shortage_reference(self, obj: ReplenishmentRequest) -> str | None:
        shortage = getattr(obj, "picking_shortage", None)
        return shortage.reference if shortage else None

    def get_cart_code(self, obj: ReplenishmentRequest) -> str | None:
        shortage = getattr(obj, "picking_shortage", None)
        return shortage.cart.code if shortage and shortage.cart else None

    def get_reported_location_code(self, obj: ReplenishmentRequest) -> str | None:
        shortage = getattr(obj, "picking_shortage", None)
        if shortage:
            return shortage.reported_location.code
        if obj.picking_task_id:
            return obj.picking_task.source_location.code
        return None

    def get_reported_by_worker_code(self, obj: ReplenishmentRequest) -> str:
        shortage = getattr(obj, "picking_shortage", None)
        return shortage.reported_by_worker_code if shortage else ""

    def get_reported_at(self, obj: ReplenishmentRequest) -> str | None:
        shortage = getattr(obj, "picking_shortage", None)
        return shortage.reported_at.isoformat() if shortage else None

    class Meta:
        model = ReplenishmentRequest
        fields = [
            "id",
            "reference",
            "picking_shortage",
            "picking_task",
            "shortage_reference",
            "branch",
            "branch_code",
            "customer_alias",
            "order_reference",
            "product",
            "product_sku",
            "product_name",
            "product_brand",
            "quantity",
            "reason",
            "reason_label",
            "status",
            "status_label",
            "external_system",
            "external_reference",
            "cart_code",
            "reported_location_code",
            "reported_by_worker_code",
            "reported_at",
            "created_by",
            "created_by_username",
            "ordered_at",
            "ordered_by",
            "ordered_by_username",
            "ordered_by_worker_code",
            "note",
            "ax_payload",
            "created_at",
            "updated_at",
        ]


class StockMovementSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    source_location_code = serializers.CharField(source="source_location.code", read_only=True)
    destination_location_code = serializers.CharField(source="destination_location.code", read_only=True)
    performed_by_username = serializers.CharField(source="performed_by.username", read_only=True)
    movement_type_label = serializers.CharField(source="get_movement_type_display", read_only=True)
    status = serializers.SerializerMethodField()
    origin = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        return "completed"

    def get_origin(self, obj) -> str:
        if obj.movement_type == StockMovement.MovementType.TRANSFER and obj.source_location_id and obj.destination_location_id:
            return "Scanner Quick Transfer"
        return obj.get_movement_type_display()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "branch",
            "branch_code",
            "product",
            "product_sku",
            "product_name",
            "inventory_item",
            "source_location",
            "source_location_code",
            "destination_location",
            "destination_location_code",
            "movement_type",
            "movement_type_label",
            "quantity",
            "reference",
            "performed_by",
            "performed_by_username",
            "status",
            "origin",
            "created_at",
            "updated_at",
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)
    actor_display = serializers.SerializerMethodField()
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    source_location_code = serializers.CharField(source="source_location.code", read_only=True)
    destination_location_code = serializers.CharField(source="destination_location.code", read_only=True)
    cart_code = serializers.CharField(source="cart.code", read_only=True)
    order_reference = serializers.CharField(source="order.external_reference", read_only=True)
    route_run_label = serializers.SerializerMethodField()
    transfer_reference = serializers.CharField(source="transfer.reference", read_only=True)
    pallet_code = serializers.CharField(source="pallet.scan_code", read_only=True)
    discrepancy_reference = serializers.CharField(source="discrepancy.reference", read_only=True)

    def get_actor_display(self, obj) -> str:
        if obj.actor_id and obj.actor:
            return obj.actor.username
        match = re.match(r"^Worker\s+([A-Za-z0-9_.-]+)\s+", obj.message or "")
        if match:
            return match.group(1)
        return "System"

    def get_route_run_label(self, obj) -> str | None:
        if obj.route_run_id is None:
            return None
        return str(obj.route_run)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "actor_username",
            "actor_display",
            "action_type",
            "event_type",
            "branch",
            "branch_code",
            "product",
            "product_sku",
            "product_name",
            "quantity",
            "expected_quantity",
            "checked_quantity",
            "source_location",
            "source_location_code",
            "destination_location",
            "destination_location_code",
            "source_label",
            "destination_label",
            "cart",
            "cart_code",
            "order",
            "order_reference",
            "route_run",
            "route_run_label",
            "transfer",
            "transfer_reference",
            "pallet",
            "pallet_code",
            "discrepancy",
            "discrepancy_reference",
            "result",
            "reference",
            "entity_name",
            "entity_id",
            "message",
            "created_at",
        ]


class TransferDiscrepancyActionSerializer(serializers.Serializer):
    action_type = serializers.CharField()
    action_label = serializers.CharField()
    target_type = serializers.CharField()
    target_reference = serializers.CharField()
    target_url = serializers.CharField()
    discrepancy_reference = serializers.CharField()
    transfer_reference = serializers.CharField()
    pallet_reference = serializers.CharField()
    source_branch = serializers.CharField()
    destination_branch = serializers.CharField()
    route = serializers.CharField(allow_blank=True)
    route_label = serializers.CharField(allow_blank=True)
    current_status = serializers.CharField()
    current_status_label = serializers.CharField()
    confirmed_shortage_quantity = serializers.CharField()
    waiting_since = serializers.DateTimeField()
    created_at = serializers.DateTimeField()


class TransferDiscrepancyItemSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    scan_history = serializers.SerializerMethodField()
    remaining_quantity = serializers.SerializerMethodField()

    def get_scan_history(self, obj: TransferDiscrepancyItem) -> list[dict]:
        scans = obj.discrepancy.pallet.receiving_scans.select_related("destination_location", "product").filter(
            product=obj.product
        )
        return [
            {
                "id": scan.id,
                "product_sku": scan.product.sku,
                "destination_location_code": scan.destination_location.code,
                "quantity": str(scan.quantity),
                "worker_code": scan.worker_code,
                "scanned_at": scan.scanned_at.isoformat(),
            }
            for scan in scans.order_by("scanned_at", "id")
        ]

    def get_remaining_quantity(self, obj: TransferDiscrepancyItem) -> str:
        return str(discrepancy_line_remaining(obj))

    class Meta:
        model = TransferDiscrepancyItem
        fields = [
            "id",
            "pallet_item",
            "product",
            "product_sku",
            "product_name",
            "discrepancy_type",
            "expected_quantity",
            "received_quantity",
            "difference_quantity",
            "discrepancy_quantity",
            "posted_to_unconfirmed_quantity",
            "posted_to_unconfirmed_at",
            "recovered_quantity",
            "last_recovered_at",
            "confirmed_shortage_quantity",
            "last_confirmed_shortage_at",
            "remaining_quantity",
            "scan_history",
            "created_at",
            "updated_at",
        ]


class TransferDiscrepancySerializer(serializers.ModelSerializer):
    pallet_code = serializers.CharField(source="pallet.scan_code", read_only=True)
    transfer_reference = serializers.CharField(source="transfer.reference", read_only=True)
    source_branch_code = serializers.CharField(source="transfer.source_branch.code", read_only=True)
    destination_branch_code = serializers.CharField(source="transfer.destination_branch.code", read_only=True)
    line_count = serializers.SerializerMethodField()
    total_discrepancy_quantity = serializers.SerializerMethodField()
    total_recovered_quantity = serializers.SerializerMethodField()
    total_posted_to_unconfirmed_quantity = serializers.SerializerMethodField()
    total_confirmed_shortage_quantity = serializers.SerializerMethodField()
    total_remaining_quantity = serializers.SerializerMethodField()
    items = TransferDiscrepancyItemSerializer(many=True, read_only=True)
    recoveries = serializers.SerializerMethodField()
    shortage_confirmations = serializers.SerializerMethodField()
    source_review = serializers.SerializerMethodField()
    reconciliation = serializers.SerializerMethodField()

    def get_line_count(self, obj: TransferDiscrepancy) -> int:
        return obj.items.count()

    def get_total_discrepancy_quantity(self, obj: TransferDiscrepancy) -> str:
        total = sum((item.discrepancy_quantity for item in obj.items.all()), Decimal("0"))
        return str(total)

    def get_total_recovered_quantity(self, obj: TransferDiscrepancy) -> str:
        return str(get_discrepancy_investigation_totals(obj)["recovered"])

    def get_total_posted_to_unconfirmed_quantity(self, obj: TransferDiscrepancy) -> str:
        return str(get_discrepancy_investigation_totals(obj)["posted"])

    def get_total_confirmed_shortage_quantity(self, obj: TransferDiscrepancy) -> str:
        return str(get_discrepancy_investigation_totals(obj)["confirmed_shortage"])

    def get_total_remaining_quantity(self, obj: TransferDiscrepancy) -> str:
        return str(get_discrepancy_investigation_totals(obj)["remaining"])

    def get_recoveries(self, obj: TransferDiscrepancy) -> list[dict]:
        recoveries = obj.recoveries.select_related("product", "source_location", "destination_location").order_by("-recovered_at")
        return [
            {
                "id": recovery.id,
                "product_sku": recovery.product.sku,
                "product_name": recovery.product.name,
                "quantity": str(recovery.quantity),
                "source_location_code": recovery.source_location.code,
                "destination_location_code": recovery.destination_location.code,
                "worker_code": recovery.worker_code,
                "recovered_at": recovery.recovered_at.isoformat(),
                "client_operation_id": recovery.client_operation_id,
            }
            for recovery in recoveries
        ]

    def get_shortage_confirmations(self, obj: TransferDiscrepancy) -> list[dict]:
        confirmations = obj.shortage_confirmations.select_related("product", "unconfirmed_location").order_by("-confirmed_at")
        return [
            {
                "id": confirmation.id,
                "product_sku": confirmation.product.sku,
                "product_name": confirmation.product.name,
                "quantity": str(confirmation.quantity),
                "unconfirmed_location_code": confirmation.unconfirmed_location.code,
                "worker_code": confirmation.worker_code,
                "confirmed_at": confirmation.confirmed_at.isoformat(),
                "client_operation_id": confirmation.client_operation_id,
            }
            for confirmation in confirmations
        ]

    def get_source_review(self, obj: TransferDiscrepancy) -> dict | None:
        review = getattr(obj, "source_review", None)
        if review is None:
            return None
        return {
            "id": review.id,
            "reference": review.reference,
            "status": review.status,
            "finding": review.finding,
            "finding_display": review.get_finding_display() if review.finding else "",
            "completed_at": review.completed_at.isoformat() if review.completed_at else None,
        }

    def get_reconciliation(self, obj: TransferDiscrepancy) -> dict | None:
        reconciliation = getattr(obj, "reconciliation", None)
        if reconciliation is None:
            return None
        manual_decision = self._manual_decision_summary(reconciliation)
        return {
            "id": reconciliation.id,
            "reference": reconciliation.reference,
            "route": reconciliation.route,
            "route_label": reconciliation.get_route_display(),
            "status": reconciliation.status,
            "status_label": reconciliation.get_status_display(),
            "next_action_label": reconciliation_next_action(
                reconciliation.route,
                reconciliation.status,
                manual_decision is not None,
            ),
            "manual_decision_required": self._manual_decision_required(reconciliation),
            "manual_decision": manual_decision,
            "source_stock_verification": self._source_verification_summary(reconciliation),
            "transit_investigation": self._transit_investigation_summary(reconciliation),
        }

    def _manual_decision_required(self, reconciliation) -> bool:
        if reconciliation.route == TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION:
            return reconciliation.status == TransferDiscrepancyReconciliation.Status.IN_PROGRESS
        if reconciliation.route == TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION:
            return reconciliation.status == TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED
        if reconciliation.route == TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION:
            investigation = getattr(reconciliation, "transit_investigation", None)
            return (
                reconciliation.status == TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED
                and investigation is not None
                and investigation.status == TransferDiscrepancyTransitInvestigation.Status.COMPLETED
                and bool(investigation.finding)
                and bool(investigation.finding_note.strip())
            )
        return False

    def _manual_decision_summary(self, reconciliation) -> dict | None:
        decision = getattr(reconciliation, "manual_decision", None)
        if decision is None:
            return None
        return {
            "id": decision.id,
            "outcome": decision.outcome,
            "outcome_label": decision.get_outcome_display(),
            "decision_note": decision.decision_note,
            "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
            "decided_by_worker_code": decision.decided_by_worker_code,
        }

    def _transit_investigation_summary(self, reconciliation) -> dict | None:
        investigation = getattr(reconciliation, "transit_investigation", None)
        if investigation is None:
            return None
        return {
            "id": investigation.id,
            "reference": investigation.reference,
            "status": investigation.status,
            "status_label": investigation.get_status_display(),
            "finding": investigation.finding,
            "finding_label": investigation.get_finding_display() if investigation.finding else "",
            "finding_note": investigation.finding_note,
            "started_at": investigation.started_at.isoformat() if investigation.started_at else None,
            "started_by_worker_code": investigation.started_by_worker_code,
            "completed_at": investigation.completed_at.isoformat() if investigation.completed_at else None,
            "completed_by_worker_code": investigation.completed_by_worker_code,
        }

    def _source_verification_summary(self, reconciliation) -> dict | None:
        verification = getattr(reconciliation, "source_stock_verification", None)
        if verification is None:
            return None
        totals = get_source_verification_totals(verification)
        return {
            "id": verification.id,
            "reference": verification.reference,
            "status": verification.status,
            "status_label": verification.get_status_display(),
            "total_target_quantity": str(totals["target"]),
            "total_found_quantity": str(totals["found"]),
            "total_remaining_quantity": str(totals["remaining"]),
            "total_unresolved_quantity": str(totals["unresolved"]),
            "search_completed_at": verification.search_completed_at.isoformat() if verification.search_completed_at else None,
            "search_completed_by_worker_code": verification.search_completed_by_worker_code,
            "search_completion_note": verification.search_completion_note,
        }

    class Meta:
        model = TransferDiscrepancy
        fields = [
            "id",
            "reference",
            "pallet",
            "pallet_code",
            "transfer",
            "transfer_reference",
            "source_branch_code",
            "destination_branch_code",
            "status",
            "created_by_worker_code",
            "notes",
            "closed_at",
            "report_printed_at",
            "report_print_count",
            "last_report_printer_code",
            "shortage_posted_at",
            "resolved_at",
            "resolved_by_worker_code",
            "confirmed_shortage_at",
            "confirmed_shortage_by_worker_code",
            "line_count",
            "total_discrepancy_quantity",
            "total_posted_to_unconfirmed_quantity",
            "total_recovered_quantity",
            "total_confirmed_shortage_quantity",
            "total_remaining_quantity",
            "items",
            "recoveries",
            "shortage_confirmations",
            "source_review",
            "reconciliation",
            "created_at",
            "updated_at",
        ]


class TransferDiscrepancySourceReviewSerializer(serializers.ModelSerializer):
    discrepancy_reference = serializers.CharField(source="discrepancy.reference", read_only=True)
    discrepancy_status = serializers.CharField(source="discrepancy.status", read_only=True)
    discrepancy_created_at = serializers.DateTimeField(source="discrepancy.created_at", read_only=True)
    discrepancy_confirmed_shortage_at = serializers.DateTimeField(
        source="discrepancy.confirmed_shortage_at",
        read_only=True,
    )
    discrepancy_confirmed_shortage_by_worker_code = serializers.CharField(
        source="discrepancy.confirmed_shortage_by_worker_code",
        read_only=True,
    )
    transfer_reference = serializers.CharField(source="discrepancy.transfer.reference", read_only=True)
    source_branch_code = serializers.CharField(source="source_branch.code", read_only=True)
    source_branch_name = serializers.CharField(source="source_branch.name", read_only=True)
    destination_branch_code = serializers.CharField(source="discrepancy.transfer.destination_branch.code", read_only=True)
    destination_branch_name = serializers.CharField(source="discrepancy.transfer.destination_branch.name", read_only=True)
    pallet_code = serializers.CharField(source="discrepancy.pallet.scan_code", read_only=True)
    pallet_closed_at = serializers.DateTimeField(source="discrepancy.closed_at", read_only=True)
    finding_display = serializers.CharField(source="get_finding_display", read_only=True)
    total_expected_quantity = serializers.SerializerMethodField()
    total_received_quantity = serializers.SerializerMethodField()
    total_missing_quantity = serializers.SerializerMethodField()
    total_posted_to_unconfirmed_quantity = serializers.SerializerMethodField()
    total_recovered_quantity = serializers.SerializerMethodField()
    total_confirmed_shortage_quantity = serializers.SerializerMethodField()
    total_remaining_quantity = serializers.SerializerMethodField()
    lines = serializers.SerializerMethodField()
    source_dispatch_evidence = serializers.SerializerMethodField()
    destination_receiving_evidence = serializers.SerializerMethodField()
    recoveries = serializers.SerializerMethodField()
    shortage_confirmations = serializers.SerializerMethodField()
    reconciliation = serializers.SerializerMethodField()

    def _items(self, obj: TransferDiscrepancySourceReview):
        return list(obj.discrepancy.items.select_related("product", "pallet_item").order_by("product__sku"))

    def get_total_expected_quantity(self, obj) -> str:
        return str(sum((item.expected_quantity for item in self._items(obj)), Decimal("0")))

    def get_total_received_quantity(self, obj) -> str:
        return str(sum((item.received_quantity for item in self._items(obj)), Decimal("0")))

    def get_total_missing_quantity(self, obj) -> str:
        return str(sum((item.discrepancy_quantity for item in self._items(obj)), Decimal("0")))

    def get_total_posted_to_unconfirmed_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["posted"])

    def get_total_recovered_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["recovered"])

    def get_total_confirmed_shortage_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["confirmed_shortage"])

    def get_total_remaining_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["remaining"])

    def get_lines(self, obj) -> list[dict]:
        return [
            {
                "id": item.id,
                "product_sku": item.product.sku,
                "product_name": item.product.name,
                "expected_quantity": str(item.expected_quantity),
                "received_quantity": str(item.received_quantity),
                "missing_quantity": str(item.discrepancy_quantity),
                "recovered_quantity": str(item.recovered_quantity),
                "confirmed_shortage_quantity": str(item.confirmed_shortage_quantity),
                "remaining_quantity": str(discrepancy_line_remaining(item)),
            }
            for item in self._items(obj)
        ]

    def get_source_dispatch_evidence(self, obj) -> list[dict]:
        return [
            {
                "product_sku": item.product.sku,
                "product_name": item.product.name,
                "expected_quantity": str(item.expected_quantity),
                "pallet_code": obj.discrepancy.pallet.scan_code,
                "released_at": obj.discrepancy.pallet.released_at.isoformat() if obj.discrepancy.pallet.released_at else None,
            }
            for item in obj.discrepancy.pallet.items.select_related("product").order_by("product__sku")
        ]

    def get_destination_receiving_evidence(self, obj) -> list[dict]:
        scans = obj.discrepancy.pallet.receiving_scans.select_related("product", "destination_location").order_by("scanned_at")
        return [
            {
                "product_sku": scan.product.sku,
                "product_name": scan.product.name,
                "quantity": str(scan.quantity),
                "destination_location_code": scan.destination_location.code,
                "worker_code": scan.worker_code,
                "scanned_at": scan.scanned_at.isoformat(),
            }
            for scan in scans
        ]

    def get_recoveries(self, obj) -> list[dict]:
        return TransferDiscrepancySerializer().get_recoveries(obj.discrepancy)

    def get_shortage_confirmations(self, obj) -> list[dict]:
        return TransferDiscrepancySerializer().get_shortage_confirmations(obj.discrepancy)

    def get_reconciliation(self, obj) -> dict | None:
        reconciliation = getattr(obj, "reconciliation", None)
        if reconciliation is None:
            return None
        helper = TransferDiscrepancySerializer()
        manual_decision = helper._manual_decision_summary(reconciliation)
        return {
            "id": reconciliation.id,
            "reference": reconciliation.reference,
            "route": reconciliation.route,
            "route_label": reconciliation.get_route_display(),
            "status": reconciliation.status,
            "status_label": reconciliation.get_status_display(),
            "next_action_label": reconciliation_next_action(
                reconciliation.route,
                reconciliation.status,
                manual_decision is not None,
            ),
            "manual_decision_required": helper._manual_decision_required(reconciliation),
            "manual_decision": manual_decision,
            "source_stock_verification": helper._source_verification_summary(reconciliation),
            "transit_investigation": helper._transit_investigation_summary(reconciliation),
        }

    class Meta:
        model = TransferDiscrepancySourceReview
        fields = [
            "id",
            "reference",
            "status",
            "finding",
            "finding_display",
            "started_at",
            "started_by_worker_code",
            "completed_at",
            "completed_by_worker_code",
            "finding_note",
            "created_at",
            "updated_at",
            "discrepancy",
            "discrepancy_reference",
            "discrepancy_status",
            "discrepancy_created_at",
            "discrepancy_confirmed_shortage_at",
            "discrepancy_confirmed_shortage_by_worker_code",
            "transfer_reference",
            "source_branch",
            "source_branch_code",
            "source_branch_name",
            "destination_branch_code",
            "destination_branch_name",
            "pallet_code",
            "pallet_closed_at",
            "total_expected_quantity",
            "total_received_quantity",
            "total_missing_quantity",
            "total_posted_to_unconfirmed_quantity",
            "total_recovered_quantity",
            "total_confirmed_shortage_quantity",
            "total_remaining_quantity",
            "lines",
            "source_dispatch_evidence",
            "destination_receiving_evidence",
            "recoveries",
            "shortage_confirmations",
            "reconciliation",
        ]


class TransferDiscrepancyReconciliationSerializer(serializers.ModelSerializer):
    route_label = serializers.CharField(source="get_route_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    next_action_label = serializers.SerializerMethodField()
    source_stock_verification = serializers.SerializerMethodField()
    transit_investigation = serializers.SerializerMethodField()
    manual_decision_required = serializers.SerializerMethodField()
    manual_decision = serializers.SerializerMethodField()
    source_review_reference = serializers.CharField(source="source_review.reference", read_only=True)
    source_review_status = serializers.CharField(source="source_review.status", read_only=True)
    source_review_finding = serializers.CharField(source="source_review.finding", read_only=True)
    source_review_finding_display = serializers.CharField(source="source_review.get_finding_display", read_only=True)
    source_review_finding_note = serializers.CharField(source="source_review.finding_note", read_only=True)
    source_review_completed_at = serializers.DateTimeField(source="source_review.completed_at", read_only=True)
    source_review_completed_by_worker_code = serializers.CharField(
        source="source_review.completed_by_worker_code",
        read_only=True,
    )
    discrepancy_reference = serializers.CharField(source="discrepancy.reference", read_only=True)
    discrepancy_status = serializers.CharField(source="discrepancy.status", read_only=True)
    discrepancy_confirmed_shortage_at = serializers.DateTimeField(
        source="discrepancy.confirmed_shortage_at",
        read_only=True,
    )
    discrepancy_confirmed_shortage_by_worker_code = serializers.CharField(
        source="discrepancy.confirmed_shortage_by_worker_code",
        read_only=True,
    )
    transfer_reference = serializers.CharField(source="discrepancy.transfer.reference", read_only=True)
    source_branch_code = serializers.CharField(source="discrepancy.transfer.source_branch.code", read_only=True)
    source_branch_name = serializers.CharField(source="discrepancy.transfer.source_branch.name", read_only=True)
    destination_branch_code = serializers.CharField(source="discrepancy.transfer.destination_branch.code", read_only=True)
    destination_branch_name = serializers.CharField(source="discrepancy.transfer.destination_branch.name", read_only=True)
    pallet_code = serializers.CharField(source="discrepancy.pallet.scan_code", read_only=True)
    total_posted_to_unconfirmed_quantity = serializers.SerializerMethodField()
    total_recovered_quantity = serializers.SerializerMethodField()
    total_confirmed_shortage_quantity = serializers.SerializerMethodField()
    total_remaining_quantity = serializers.SerializerMethodField()
    lines = serializers.SerializerMethodField()

    def get_next_action_label(self, obj) -> str:
        return reconciliation_next_action(obj.route, obj.status, self.get_manual_decision(obj) is not None)

    def get_manual_decision_required(self, obj) -> bool:
        return TransferDiscrepancySerializer()._manual_decision_required(obj)

    def get_manual_decision(self, obj) -> dict | None:
        return TransferDiscrepancySerializer()._manual_decision_summary(obj)

    def get_source_stock_verification(self, obj) -> dict | None:
        verification = getattr(obj, "source_stock_verification", None)
        if verification is None:
            return None
        totals = get_source_verification_totals(verification)
        return {
            "id": verification.id,
            "reference": verification.reference,
            "status": verification.status,
            "status_label": verification.get_status_display(),
            "total_target_quantity": str(totals["target"]),
            "total_found_quantity": str(totals["found"]),
            "total_remaining_quantity": str(totals["remaining"]),
            "total_unresolved_quantity": str(totals["unresolved"]),
            "search_completed_at": verification.search_completed_at.isoformat() if verification.search_completed_at else None,
            "search_completed_by_worker_code": verification.search_completed_by_worker_code,
            "search_completion_note": verification.search_completion_note,
        }

    def get_transit_investigation(self, obj) -> dict | None:
        return TransferDiscrepancySerializer()._transit_investigation_summary(obj)

    def get_total_posted_to_unconfirmed_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["posted"])

    def get_total_recovered_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["recovered"])

    def get_total_confirmed_shortage_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["confirmed_shortage"])

    def get_total_remaining_quantity(self, obj) -> str:
        return str(get_discrepancy_investigation_totals(obj.discrepancy)["remaining"])

    def get_lines(self, obj) -> list[dict]:
        return [
            {
                "id": item.id,
                "product_sku": item.product.sku,
                "product_name": item.product.name,
                "missing_quantity": str(item.discrepancy_quantity),
                "recovered_quantity": str(item.recovered_quantity),
                "confirmed_shortage_quantity": str(item.confirmed_shortage_quantity),
                "remaining_quantity": str(discrepancy_line_remaining(item)),
            }
            for item in obj.discrepancy.items.select_related("product").order_by("product__sku")
        ]

    class Meta:
        model = TransferDiscrepancyReconciliation
        fields = [
            "id",
            "reference",
            "route",
            "route_label",
            "status",
            "status_label",
            "next_action_label",
            "manual_decision_required",
            "manual_decision",
            "created_at",
            "updated_at",
            "acknowledged_at",
            "acknowledged_by_worker_code",
            "completed_at",
            "completed_by_worker_code",
            "source_stock_verification",
            "transit_investigation",
            "discrepancy",
            "discrepancy_reference",
            "discrepancy_status",
            "discrepancy_confirmed_shortage_at",
            "discrepancy_confirmed_shortage_by_worker_code",
            "source_review",
            "source_review_reference",
            "source_review_status",
            "source_review_finding",
            "source_review_finding_display",
            "source_review_finding_note",
            "source_review_completed_at",
            "source_review_completed_by_worker_code",
            "transfer_reference",
            "source_branch_code",
            "source_branch_name",
            "destination_branch_code",
            "destination_branch_name",
            "pallet_code",
            "total_posted_to_unconfirmed_quantity",
            "total_recovered_quantity",
            "total_confirmed_shortage_quantity",
            "total_remaining_quantity",
            "lines",
        ]


class TransferDiscrepancySourceStockVerificationSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    next_action_label = serializers.SerializerMethodField()
    reconciliation_reference = serializers.CharField(source="reconciliation.reference", read_only=True)
    reconciliation_status = serializers.CharField(source="reconciliation.status", read_only=True)
    reconciliation_status_label = serializers.CharField(source="reconciliation.get_status_display", read_only=True)
    reconciliation_route = serializers.CharField(source="reconciliation.route", read_only=True)
    reconciliation_route_label = serializers.CharField(source="reconciliation.get_route_display", read_only=True)
    reconciliation_manual_decision = serializers.SerializerMethodField()
    source_review_reference = serializers.CharField(source="reconciliation.source_review.reference", read_only=True)
    source_review_finding = serializers.CharField(source="reconciliation.source_review.finding", read_only=True)
    source_review_finding_display = serializers.CharField(
        source="reconciliation.source_review.get_finding_display",
        read_only=True,
    )
    discrepancy_reference = serializers.CharField(source="reconciliation.discrepancy.reference", read_only=True)
    discrepancy_status = serializers.CharField(source="reconciliation.discrepancy.status", read_only=True)
    transfer_reference = serializers.CharField(source="reconciliation.discrepancy.transfer.reference", read_only=True)
    source_branch_code = serializers.CharField(
        source="reconciliation.discrepancy.transfer.source_branch.code",
        read_only=True,
    )
    source_branch_name = serializers.CharField(
        source="reconciliation.discrepancy.transfer.source_branch.name",
        read_only=True,
    )
    destination_branch_code = serializers.CharField(
        source="reconciliation.discrepancy.transfer.destination_branch.code",
        read_only=True,
    )
    destination_branch_name = serializers.CharField(
        source="reconciliation.discrepancy.transfer.destination_branch.name",
        read_only=True,
    )
    pallet_code = serializers.CharField(source="reconciliation.discrepancy.pallet.scan_code", read_only=True)
    total_target_quantity = serializers.SerializerMethodField()
    total_found_quantity = serializers.SerializerMethodField()
    total_remaining_quantity = serializers.SerializerMethodField()
    total_unresolved_quantity = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    recoveries = serializers.SerializerMethodField()

    def get_next_action_label(self, obj) -> str:
        return source_verification_next_action(obj.status)

    def get_reconciliation_manual_decision(self, obj) -> dict | None:
        return TransferDiscrepancySerializer()._manual_decision_summary(obj.reconciliation)

    def get_total_target_quantity(self, obj) -> str:
        return str(get_source_verification_totals(obj)["target"])

    def get_total_found_quantity(self, obj) -> str:
        return str(get_source_verification_totals(obj)["found"])

    def get_total_remaining_quantity(self, obj) -> str:
        return str(get_source_verification_totals(obj)["remaining"])

    def get_total_unresolved_quantity(self, obj) -> str:
        return str(get_source_verification_totals(obj)["unresolved"])

    def get_items(self, obj) -> list[dict]:
        return [
            {
                "id": item.id,
                "product_sku": item.product.sku,
                "product_name": item.product.name,
                "target_quantity": str(item.target_quantity),
                "found_quantity": str(item.found_quantity),
                "remaining_quantity": str(source_verification_item_remaining(item)),
                "unresolved_quantity": str(
                    source_verification_item_remaining(item)
                    if obj.status == TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED
                    else Decimal("0")
                ),
                "last_found_at": item.last_found_at.isoformat() if item.last_found_at else None,
            }
            for item in obj.items.select_related("product").order_by("product__sku")
        ]

    def get_recoveries(self, obj) -> list[dict]:
        return [
            {
                "id": recovery.id,
                "product_sku": recovery.product.sku,
                "product_name": recovery.product.name,
                "quantity": str(recovery.quantity),
                "destination_location_code": recovery.destination_location.code,
                "destination_location_name": recovery.destination_location.name,
                "worker_code": recovery.worker_code,
                "recovered_at": recovery.recovered_at.isoformat(),
                "client_operation_id": recovery.client_operation_id,
            }
            for recovery in obj.recoveries.select_related("product", "destination_location").order_by("-recovered_at")
        ]

    class Meta:
        model = TransferDiscrepancySourceStockVerification
        fields = [
            "id",
            "reference",
            "status",
            "status_label",
            "next_action_label",
            "created_at",
            "updated_at",
            "started_at",
            "started_by_worker_code",
            "completed_at",
            "completed_by_worker_code",
            "search_completed_at",
            "search_completed_by_worker_code",
            "search_completion_note",
            "search_completion_operation_id",
            "reconciliation",
            "reconciliation_reference",
            "reconciliation_status",
            "reconciliation_status_label",
            "reconciliation_route",
            "reconciliation_route_label",
            "reconciliation_manual_decision",
            "source_review_reference",
            "source_review_finding",
            "source_review_finding_display",
            "discrepancy_reference",
            "discrepancy_status",
            "transfer_reference",
            "source_branch_code",
            "source_branch_name",
            "destination_branch_code",
            "destination_branch_name",
            "pallet_code",
            "total_target_quantity",
            "total_found_quantity",
            "total_remaining_quantity",
            "total_unresolved_quantity",
            "items",
            "recoveries",
        ]


class TransferDiscrepancyTransitInvestigationSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    finding_label = serializers.CharField(source="get_finding_display", read_only=True)
    next_action_label = serializers.SerializerMethodField()
    reconciliation_manual_decision = serializers.SerializerMethodField()
    reconciliation_reference = serializers.CharField(source="reconciliation.reference", read_only=True)
    reconciliation_status = serializers.CharField(source="reconciliation.status", read_only=True)
    reconciliation_status_label = serializers.CharField(source="reconciliation.get_status_display", read_only=True)
    reconciliation_route = serializers.CharField(source="reconciliation.route", read_only=True)
    reconciliation_route_label = serializers.CharField(source="reconciliation.get_route_display", read_only=True)
    source_review_reference = serializers.CharField(source="reconciliation.source_review.reference", read_only=True)
    source_review_finding = serializers.CharField(source="reconciliation.source_review.finding", read_only=True)
    source_review_finding_display = serializers.CharField(
        source="reconciliation.source_review.get_finding_display",
        read_only=True,
    )
    source_review_finding_note = serializers.CharField(source="reconciliation.source_review.finding_note", read_only=True)
    discrepancy_reference = serializers.CharField(source="reconciliation.discrepancy.reference", read_only=True)
    discrepancy_status = serializers.CharField(source="reconciliation.discrepancy.status", read_only=True)
    transfer_reference = serializers.CharField(source="reconciliation.discrepancy.transfer.reference", read_only=True)
    transfer_status = serializers.CharField(source="reconciliation.discrepancy.transfer.status", read_only=True)
    source_branch_code = serializers.CharField(source="reconciliation.discrepancy.transfer.source_branch.code", read_only=True)
    source_branch_name = serializers.CharField(source="reconciliation.discrepancy.transfer.source_branch.name", read_only=True)
    destination_branch_code = serializers.CharField(
        source="reconciliation.discrepancy.transfer.destination_branch.code",
        read_only=True,
    )
    destination_branch_name = serializers.CharField(
        source="reconciliation.discrepancy.transfer.destination_branch.name",
        read_only=True,
    )
    pallet_code = serializers.CharField(source="reconciliation.discrepancy.pallet.scan_code", read_only=True)
    pallet_status = serializers.CharField(source="reconciliation.discrepancy.pallet.status", read_only=True)
    transfer_summary = serializers.SerializerMethodField()
    source_dispatch_evidence = serializers.SerializerMethodField()
    transit_route_evidence = serializers.SerializerMethodField()
    destination_receiving_evidence = serializers.SerializerMethodField()
    destination_investigation_outcome = serializers.SerializerMethodField()
    final_accounting_lines = serializers.SerializerMethodField()

    def get_next_action_label(self, obj) -> str:
        return transit_investigation_next_action(obj.status)

    def get_reconciliation_manual_decision(self, obj) -> dict | None:
        return TransferDiscrepancySerializer()._manual_decision_summary(obj.reconciliation)

    def _discrepancy(self, obj):
        return obj.reconciliation.discrepancy

    def get_transfer_summary(self, obj) -> dict:
        discrepancy = self._discrepancy(obj)
        transfer = discrepancy.transfer
        pallet = discrepancy.pallet
        return {
            "transfer_reference": transfer.reference,
            "transfer_status": transfer.status,
            "pallet_code": pallet.scan_code,
            "pallet_status": pallet.status,
            "source_branch_code": transfer.source_branch.code,
            "destination_branch_code": transfer.destination_branch.code,
            "released_at": transfer.released_at.isoformat() if transfer.released_at else None,
            "completed_at": transfer.completed_at.isoformat() if transfer.completed_at else None,
            "pallet_released_at": pallet.released_at.isoformat() if pallet.released_at else None,
            "pallet_closed_at": discrepancy.closed_at.isoformat() if discrepancy.closed_at else None,
        }

    def get_source_dispatch_evidence(self, obj) -> list[dict]:
        return TransferDiscrepancySourceReviewSerializer().get_source_dispatch_evidence(obj.reconciliation.source_review)

    def get_transit_route_evidence(self, obj) -> list[dict]:
        discrepancy = self._discrepancy(obj)
        entries = []
        if discrepancy.pallet.released_at:
            entries.append(
                {
                    "label": "Pallet released",
                    "reference": discrepancy.pallet.scan_code,
                    "timestamp": discrepancy.pallet.released_at.isoformat(),
                }
            )
        if discrepancy.transfer.released_at:
            entries.append(
                {
                    "label": "Transfer released",
                    "reference": discrepancy.transfer.reference,
                    "timestamp": discrepancy.transfer.released_at.isoformat(),
                }
            )
        if discrepancy.transfer.completed_at:
            entries.append(
                {
                    "label": "Transfer completed",
                    "reference": discrepancy.transfer.reference,
                    "timestamp": discrepancy.transfer.completed_at.isoformat(),
                }
            )
        return entries

    def get_destination_receiving_evidence(self, obj) -> list[dict]:
        return TransferDiscrepancySourceReviewSerializer().get_destination_receiving_evidence(obj.reconciliation.source_review)

    def get_destination_investigation_outcome(self, obj) -> dict:
        discrepancy = self._discrepancy(obj)
        totals = get_discrepancy_investigation_totals(discrepancy)
        return {
            "discrepancy_reference": discrepancy.reference,
            "discrepancy_status": discrepancy.status,
            "posted_to_unconfirmed": str(totals["posted"]),
            "destination_recovered": str(totals["recovered"]),
            "confirmed_shortage": str(totals["confirmed_shortage"]),
            "destination_remaining": str(totals["remaining"]),
            "recoveries": TransferDiscrepancySerializer().get_recoveries(discrepancy),
            "shortage_confirmations": TransferDiscrepancySerializer().get_shortage_confirmations(discrepancy),
        }

    def get_final_accounting_lines(self, obj) -> list[dict]:
        return TransferDiscrepancyReconciliationSerializer().get_lines(obj.reconciliation)

    class Meta:
        model = TransferDiscrepancyTransitInvestigation
        fields = [
            "id",
            "reference",
            "status",
            "status_label",
            "finding",
            "finding_label",
            "finding_note",
            "next_action_label",
            "created_at",
            "updated_at",
            "started_at",
            "started_by_worker_code",
            "completed_at",
            "completed_by_worker_code",
            "completion_operation_id",
            "reconciliation",
            "reconciliation_reference",
            "reconciliation_status",
            "reconciliation_status_label",
            "reconciliation_route",
            "reconciliation_route_label",
            "reconciliation_manual_decision",
            "source_review_reference",
            "source_review_finding",
            "source_review_finding_display",
            "source_review_finding_note",
            "discrepancy_reference",
            "discrepancy_status",
            "transfer_reference",
            "transfer_status",
            "source_branch_code",
            "source_branch_name",
            "destination_branch_code",
            "destination_branch_name",
            "pallet_code",
            "pallet_status",
            "transfer_summary",
            "source_dispatch_evidence",
            "transit_route_evidence",
            "destination_receiving_evidence",
            "destination_investigation_outcome",
            "final_accounting_lines",
        ]
