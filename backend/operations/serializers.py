import re
from datetime import datetime
from decimal import Decimal

from django.db import models
from django.utils import timezone
from rest_framework import serializers

from operations.models import (
    AuditLog,
    CycleCountLine,
    CycleCountLocation,
    CycleCountRecount,
    CycleCountSession,
    BranchDispatchPolicy,
    DeliveryRoute,
    ExternalReturnDocument,
    ExternalReturnDocumentLine,
    Order,
    OrderLine,
    PickingShortage,
    PickingShortageAllocation,
    PickingTaskClaim,
    PickingTaskReallocation,
    PickingTask,
    ReplenishmentRequest,
    ReturnAction,
    ReturnBatch,
    ReturnLine,
    RouteRoundSchedule,
    RouteRun,
    RouteRunOverrideHistory,
    SalesCorrection,
    SalesCorrectionLine,
    Shipment,
    ShipmentLine,
    ShipmentLineQuantityAdjustment,
    ShipmentRouteAssignment,
    ShipmentStatusHistory,
    StockMovement,
    TransferPallet,
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
from operations.shipment_services import (
    derive_shipment_line_status,
    derive_shipment_operational_statuses,
    shipment_line_effective_quantity,
    shipment_line_max_removable_quantity,
    shipment_line_task_totals,
    shipment_picking_totals,
)
from operations.operational_projections import (
    route_run_quantity_progress,
    route_run_workload_projection,
    shipment_line_progress,
    shipment_operational_projection,
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
from operations.route_services import operational_identifier


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


class BranchDispatchPolicySerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = BranchDispatchPolicy
        fields = [
            "id",
            "branch",
            "branch_code",
            "max_routes_per_wave",
            "min_wave_gap_minutes",
            "created_at",
            "updated_at",
        ]


class RouteRoundScheduleSerializer(serializers.ModelSerializer):
    branch = serializers.IntegerField(source="route.branch_id", read_only=True)
    branch_code = serializers.CharField(source="route.branch.code", read_only=True)
    route_code = serializers.CharField(source="route.code", read_only=True)
    route_name = serializers.CharField(source="route.name", read_only=True)
    weekday_label = serializers.CharField(source="get_weekday_display", read_only=True)

    class Meta:
        model = RouteRoundSchedule
        fields = [
            "id",
            "route",
            "route_code",
            "route_name",
            "branch",
            "branch_code",
            "weekday",
            "weekday_label",
            "round_number",
            "cutoff_time",
            "departure_time",
            "dispatch_wave",
            "operational_label",
            "is_active",
            "created_at",
            "updated_at",
        ]


    def validate(self, attrs):
        cutoff_time = attrs.get("cutoff_time", getattr(self.instance, "cutoff_time", None))
        departure_time = attrs.get("departure_time", getattr(self.instance, "departure_time", None))
        dispatch_wave = attrs.get("dispatch_wave", getattr(self.instance, "dispatch_wave", ""))
        if cutoff_time and departure_time and cutoff_time >= departure_time:
            raise serializers.ValidationError("Cutoff must be before departure.")
        if not str(dispatch_wave or "").strip():
            raise serializers.ValidationError("Dispatch wave is required.")
        return attrs
class RouteRunSerializer(serializers.ModelSerializer):
    route_code = serializers.CharField(source="route.code", read_only=True)
    route_name = serializers.CharField(source="route.name", read_only=True)
    branch = serializers.IntegerField(source="route.branch_id", read_only=True)
    branch_code = serializers.CharField(source="route.branch.code", read_only=True)
    orders_count = serializers.SerializerMethodField()
    order_lines_count = serializers.SerializerMethodField()
    picked_lines_count = serializers.SerializerMethodField()
    pending_lines_count = serializers.SerializerMethodField()
    has_pending_work = serializers.SerializerMethodField()
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
    cutoff_at = serializers.DateTimeField(read_only=True)
    planned_departure_at = serializers.DateTimeField(read_only=True)
    operational_identifier = serializers.SerializerMethodField()
    dispatch_wave = serializers.CharField(read_only=True)
    active_workers_count = serializers.SerializerMethodField()
    unstarted_lines_count = serializers.SerializerMethodField()
    started_lines_count = serializers.SerializerMethodField()
    picked_line_bucket_count = serializers.SerializerMethodField()
    prepared_line_bucket_count = serializers.SerializerMethodField()
    total_active_lines = serializers.SerializerMethodField()
    attention_status = serializers.SerializerMethodField()
    attention_reason = serializers.SerializerMethodField()
    minutes_to_departure = serializers.SerializerMethodField()
    minutes_after_cutoff = serializers.SerializerMethodField()
    operational_weekday = serializers.SerializerMethodField()
    readiness_state = serializers.SerializerMethodField()
    remaining_pickable_quantity = serializers.SerializerMethodField()
    scanner_can_create_picking_job = serializers.SerializerMethodField()
    scanner_blocking_reason = serializers.SerializerMethodField()

    def _get_shipments(self, obj: RouteRun):
        cache_name = "_monitor_shipments"
        if not hasattr(obj, cache_name):
            setattr(
                obj,
                cache_name,
                [shipment for shipment in obj.shipments.all() if shipment.status != Shipment.Status.CANCELLED],
            )
        return getattr(obj, cache_name)

    def _get_shipment_lines(self, obj: RouteRun):
        shipments = self._get_shipments(obj)
        return [line for shipment in shipments for line in shipment.lines.all() if shipment_line_effective_quantity(line) > 0]

    def _get_picking_tasks(self, obj: RouteRun):
        cache_name = "_monitor_picking_tasks"
        if not hasattr(obj, cache_name):
            tasks = {
                task.id: task
                for shipment in self._get_shipments(obj)
                for line in shipment.lines.all()
                for task in line.order_line.picking_tasks.all()
                if task.status != PickingTask.Status.CANCELLED
            }
            if not tasks and not self._get_shipments(obj):
                tasks = {
                    task.id: task
                    for order in obj.orders.all()
                    for line in order.lines.all()
                    for task in line.picking_tasks.all()
                    if task.status != PickingTask.Status.CANCELLED
                }
            setattr(obj, cache_name, list(tasks.values()))
        return getattr(obj, cache_name)

    def get_orders_count(self, obj: RouteRun) -> int:
        shipments = self._get_shipments(obj)
        if shipments:
            return len({shipment.order_id for shipment in shipments})
        return obj.orders.count()

    def get_order_lines_count(self, obj: RouteRun) -> int:
        shipments = self._get_shipments(obj)
        if shipments:
            return sum(
                1
                for shipment in shipments
                for line in shipment.lines.all()
                if shipment_line_effective_quantity(line) > 0
            )
        return OrderLine.objects.filter(order__route_run=obj).count()

    def get_picked_lines_count(self, obj: RouteRun) -> int:
        shipments = self._get_shipments(obj)
        if shipments:
            count = 0
            for shipment in shipments:
                for line in shipment.lines.all():
                    effective_quantity = shipment_line_effective_quantity(line)
                    if effective_quantity > 0 and shipment_line_task_totals(line)["picked"] >= effective_quantity:
                        count += 1
            return count
        return OrderLine.objects.filter(order__route_run=obj, quantity_picked__gte=models.F("quantity_ordered")).count()

    def get_pending_lines_count(self, obj: RouteRun) -> int:
        return max(self.get_order_lines_count(obj) - self.get_picked_lines_count(obj), 0)

    def get_has_pending_work(self, obj: RouteRun) -> bool:
        return self.get_pending_lines_count(obj) > 0

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

    def _line_bucket(self, line) -> str:
        return shipment_line_progress(line).state

    def _line_buckets(self, obj: RouteRun) -> dict[str, int]:
        cache_name = "_monitor_line_buckets"
        if not hasattr(obj, cache_name):
            projection = route_run_workload_projection(obj)
            buckets = {
                "unstarted": projection.unstarted,
                "started": projection.started,
                "picked": projection.picked,
                "prepared": projection.prepared,
            }
            setattr(obj, cache_name, buckets)
        return getattr(obj, cache_name)

    def get_active_workers_count(self, obj: RouteRun) -> int:
        task_ids = [task.id for task in self._get_picking_tasks(obj)]
        if not task_ids:
            return 0
        return len(
            {
                claim.cart_work_participant.user_id
                for task in self._get_picking_tasks(obj)
                for claim in task.task_claims.all()
                if claim.status == PickingTaskClaim.Status.CLAIMED
                and claim.cart_work_participant.status == "active"
            }
        )

    def get_unstarted_lines_count(self, obj: RouteRun) -> int:
        return self._line_buckets(obj)["unstarted"]

    def get_started_lines_count(self, obj: RouteRun) -> int:
        return self._line_buckets(obj)["started"]

    def get_picked_line_bucket_count(self, obj: RouteRun) -> int:
        return self._line_buckets(obj)["picked"]

    def get_prepared_line_bucket_count(self, obj: RouteRun) -> int:
        return self._line_buckets(obj)["prepared"]

    def get_total_active_lines(self, obj: RouteRun) -> int:
        buckets = self._line_buckets(obj)
        return sum(buckets.values())

    def get_progress_percent(self, obj: RouteRun) -> float:
        total_quantity, picked_quantity = route_run_quantity_progress(obj)
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

    def get_operational_identifier(self, obj: RouteRun) -> str:
        return operational_identifier(obj.route, obj.service_date, obj.run_number)

    def _cutoff_dt(self, obj: RouteRun):
        if obj.cutoff_at:
            return obj.cutoff_at
        return timezone.make_aware(datetime.combine(obj.service_date, obj.order_cutoff_time), timezone.get_current_timezone())

    def _departure_dt(self, obj: RouteRun):
        if obj.planned_departure_at:
            return obj.planned_departure_at
        return timezone.make_aware(datetime.combine(obj.service_date, obj.departure_time), timezone.get_current_timezone())

    def get_minutes_to_departure(self, obj: RouteRun) -> int:
        return round((self._departure_dt(obj) - timezone.now()).total_seconds() / 60)

    def get_minutes_after_cutoff(self, obj: RouteRun) -> int:
        return round((timezone.now() - self._cutoff_dt(obj)).total_seconds() / 60)

    def get_attention_status(self, obj: RouteRun) -> str:
        if obj.status in {RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED, RouteRun.Status.DISPATCHED}:
            return "muted"
        now = timezone.now()
        if now < self._cutoff_dt(obj):
            return "neutral"
        if now >= self._departure_dt(obj):
            return "delayed"
        if self.get_prepared_line_bucket_count(obj) >= self.get_total_active_lines(obj):
            return "ready"
        return "cutoff_warning"

    def get_operational_weekday(self, obj: RouteRun) -> int:
        return obj.service_date.weekday()

    def get_readiness_state(self, obj: RouteRun) -> str:
        return "ready_to_close" if self.get_is_ready_to_close(obj) else "work_remaining"

    def get_remaining_pickable_quantity(self, obj: RouteRun) -> Decimal:
        return sum(
            (
                max(task.quantity_to_pick - task.shortage_quantity - task.quantity_picked, Decimal("0"))
                for task in self._get_picking_tasks(obj)
            ),
            Decimal("0"),
        )

    def _scanner_available_tasks(self, obj: RouteRun):
        return [
            task
            for task in self._get_picking_tasks(obj)
            if task.status not in {PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED}
            and task.quantity_picked + task.shortage_quantity < task.quantity_to_pick
            and not hasattr(task, "job_task")
        ]

    def get_scanner_can_create_picking_job(self, obj: RouteRun) -> bool:
        if obj.status in {RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED, RouteRun.Status.DISPATCHED}:
            return False
        return bool(self._scanner_available_tasks(obj))

    def get_scanner_blocking_reason(self, obj: RouteRun) -> str:
        if self.get_scanner_can_create_picking_job(obj):
            return ""
        if obj.status in {RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED, RouteRun.Status.DISPATCHED}:
            return "Route is no longer pickable"
        if self.get_is_ready_to_close(obj):
            return "Route fully prepared"
        if self.get_remaining_pickable_quantity(obj) > 0:
            return "Picking work already assigned"
        return "No remaining picking work"

    def get_attention_reason(self, obj: RouteRun) -> str:
        status = self.get_attention_status(obj)
        if status == "neutral":
            return "Cutoff has not passed."
        if status == "ready":
            return "All active work is prepared."
        if status == "cutoff_warning":
            return "Cutoff has passed and work remains before departure."
        if status == "delayed":
            return "Departure time has been reached."
        return "Route run is no longer active."

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
            "cutoff_at",
            "planned_departure_at",
            "dispatch_wave",
            "operational_identifier",
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
            "active_workers_count",
            "unstarted_lines_count",
            "started_lines_count",
            "picked_line_bucket_count",
            "prepared_line_bucket_count",
            "total_active_lines",
            "attention_status",
            "attention_reason",
            "minutes_to_departure",
            "minutes_after_cutoff",
            "operational_weekday",
            "readiness_state",
            "remaining_pickable_quantity",
            "scanner_can_create_picking_job",
            "scanner_blocking_reason",
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


class ReturnActionSerializer(serializers.ModelSerializer):
    action_type_label = serializers.CharField(source="get_action_type_display", read_only=True)
    source_pool_label = serializers.CharField(source="get_source_pool_display", read_only=True)
    employee = serializers.CharField(source="performed_by.username", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    stock_movement_reference = serializers.CharField(source="stock_movement.reference", read_only=True)
    stock_movement_id = serializers.IntegerField(source="stock_movement.id", read_only=True)

    class Meta:
        model = ReturnAction
        fields = [
            "id",
            "document",
            "line",
            "branch",
            "product",
            "product_sku",
            "product_name",
            "action_type",
            "action_type_label",
            "source_pool",
            "source_pool_label",
            "quantity",
            "employee",
            "note",
            "client_operation_id",
            "inventory_quantity_before",
            "inventory_quantity_after",
            "stock_movement",
            "stock_movement_id",
            "stock_movement_reference",
            "created_at",
            "updated_at",
        ]


class ExternalReturnDocumentLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_barcode = serializers.CharField(source="product.barcode", read_only=True)
    remaining_quantity = serializers.SerializerMethodField()
    latest_action = serializers.SerializerMethodField()
    latest_employee = serializers.SerializerMethodField()
    actions = ReturnActionSerializer(many=True, read_only=True)

    def get_remaining_quantity(self, obj) -> str:
        return str(obj.remaining_quantity)

    def _latest_action(self, obj):
        if hasattr(obj, "_latest_return_action_cache"):
            return obj._latest_return_action_cache
        action = obj.actions.select_related("performed_by").order_by("-created_at", "-id").first()
        obj._latest_return_action_cache = action
        return action

    def get_latest_action(self, obj) -> str | None:
        action = self._latest_action(obj)
        return action.action_type if action else None

    def get_latest_employee(self, obj) -> str | None:
        action = self._latest_action(obj)
        return action.performed_by.username if action and action.performed_by else None

    class Meta:
        model = ExternalReturnDocumentLine
        fields = [
            "id",
            "document",
            "line_number",
            "product",
            "product_sku",
            "product_name",
            "product_barcode",
            "expected_quantity",
            "accepted_quantity",
            "rejected_quantity",
            "on_hold_quantity",
            "remaining_quantity",
            "latest_action",
            "latest_employee",
            "actions",
            "created_at",
            "updated_at",
        ]


class ExternalReturnDocumentSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    expected_total = serializers.SerializerMethodField()
    accepted_total = serializers.SerializerMethodField()
    rejected_total = serializers.SerializerMethodField()
    on_hold_total = serializers.SerializerMethodField()
    remaining_total = serializers.SerializerMethodField()
    lines = ExternalReturnDocumentLineSerializer(many=True, read_only=True)
    actions = ReturnActionSerializer(many=True, read_only=True)

    def _lines(self, obj):
        return list(obj.lines.all())

    def get_expected_total(self, obj) -> str:
        return str(sum((line.expected_quantity for line in self._lines(obj)), Decimal("0")))

    def get_accepted_total(self, obj) -> str:
        return str(sum((line.accepted_quantity for line in self._lines(obj)), Decimal("0")))

    def get_rejected_total(self, obj) -> str:
        return str(sum((line.rejected_quantity for line in self._lines(obj)), Decimal("0")))

    def get_on_hold_total(self, obj) -> str:
        return str(sum((line.on_hold_quantity for line in self._lines(obj)), Decimal("0")))

    def get_remaining_total(self, obj) -> str:
        return str(sum((line.remaining_quantity for line in self._lines(obj)), Decimal("0")))

    class Meta:
        model = ExternalReturnDocument
        fields = [
            "id",
            "branch",
            "branch_code",
            "branch_name",
            "external_reference",
            "source_system",
            "customer_name",
            "customer_alias",
            "source_sales_document_reference",
            "external_created_at",
            "imported_at",
            "last_synced_at",
            "completed_at",
            "status",
            "status_label",
            "expected_total",
            "accepted_total",
            "rejected_total",
            "on_hold_total",
            "remaining_total",
            "lines",
            "actions",
            "created_at",
            "updated_at",
        ]


class SalesCorrectionLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    returns_location_code = serializers.CharField(source="returns_location.code", read_only=True)
    stock_movement_reference = serializers.CharField(source="stock_movement.reference", read_only=True)
    previously_corrected_quantity = serializers.SerializerMethodField()
    remaining_correctable_quantity = serializers.SerializerMethodField()

    def get_previously_corrected_quantity(self, obj) -> str:
        from operations.return_services import corrected_quantity_for_order_line

        return str(corrected_quantity_for_order_line(obj.source_order_line, exclude_correction_id=obj.correction_id))

    def get_remaining_correctable_quantity(self, obj) -> str:
        from operations.return_services import remaining_correctable_quantity

        return str(remaining_correctable_quantity(obj.source_order_line, exclude_correction_id=obj.correction_id))

    class Meta:
        model = SalesCorrectionLine
        fields = [
            "id",
            "correction",
            "product",
            "product_sku",
            "product_name",
            "source_order",
            "source_order_line",
            "customer_name_snapshot",
            "customer_alias_snapshot",
            "source_sales_document_reference",
            "sold_quantity_snapshot",
            "previously_corrected_quantity",
            "remaining_correctable_quantity",
            "corrected_quantity",
            "returns_location",
            "returns_location_code",
            "stock_movement",
            "stock_movement_reference",
            "inventory_quantity_before",
            "inventory_quantity_after",
            "created_at",
            "updated_at",
        ]


class SalesCorrectionSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    confirmed_by_username = serializers.CharField(source="confirmed_by.username", read_only=True)
    line_count = serializers.SerializerMethodField()
    total_corrected_quantity = serializers.SerializerMethodField()
    lines = SalesCorrectionLineSerializer(many=True, read_only=True)

    def get_line_count(self, obj) -> int:
        return obj.lines.count()

    def get_total_corrected_quantity(self, obj) -> str:
        return str(sum((line.corrected_quantity for line in obj.lines.all()), Decimal("0")))

    class Meta:
        model = SalesCorrection
        fields = [
            "id",
            "reference",
            "branch",
            "branch_code",
            "status",
            "status_label",
            "created_by",
            "created_by_username",
            "confirmed_by",
            "confirmed_by_username",
            "confirmed_at",
            "note",
            "line_count",
            "total_corrected_quantity",
            "lines",
            "created_at",
            "updated_at",
        ]


class SalesHistoryCandidateSerializer(serializers.Serializer):
    order = serializers.IntegerField()
    order_line = serializers.IntegerField()
    branch = serializers.IntegerField()
    branch_code = serializers.CharField()
    customer_name = serializers.CharField()
    customer_alias = serializers.CharField(allow_blank=True)
    source_sales_document_reference = serializers.CharField()
    sale_date = serializers.DateField(allow_null=True)
    product = serializers.IntegerField()
    product_sku = serializers.CharField()
    product_name = serializers.CharField()
    sold_quantity = serializers.CharField()
    previously_corrected_quantity = serializers.CharField()
    remaining_correctable_quantity = serializers.CharField()


class CorrectionActivityReportSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    confirmed_at = serializers.DateTimeField()
    employee = serializers.CharField(allow_blank=True)
    branch_code = serializers.CharField()
    correction_reference = serializers.CharField()
    customer_name = serializers.CharField()
    source_sales_document_reference = serializers.CharField()
    product_sku = serializers.CharField()
    product_name = serializers.CharField()
    corrected_quantity = serializers.CharField()
    returns_location_code = serializers.CharField(allow_blank=True)
    stock_movement = serializers.IntegerField(allow_null=True)
    summary = serializers.DictField()


class ShipmentRouteAssignmentSerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(source="changed_by.username", read_only=True)
    previous_route_label = serializers.SerializerMethodField()
    new_route_label = serializers.SerializerMethodField()

    def get_previous_route_label(self, obj: ShipmentRouteAssignment) -> str:
        return obj.previous_route_snapshot

    def get_new_route_label(self, obj: ShipmentRouteAssignment) -> str:
        return obj.new_route_snapshot

    class Meta:
        model = ShipmentRouteAssignment
        fields = [
            "id",
            "previous_route_run",
            "new_route_run",
            "previous_route_label",
            "new_route_label",
            "changed_by",
            "changed_by_username",
            "reason",
            "created_at",
        ]


class ShipmentStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(source="changed_by.username", read_only=True)

    class Meta:
        model = ShipmentStatusHistory
        fields = [
            "id",
            "previous_status",
            "new_status",
            "changed_by",
            "changed_by_username",
            "reason",
            "created_at",
        ]


class ShipmentLineQuantityAdjustmentSerializer(serializers.ModelSerializer):
    adjusted_by_username = serializers.CharField(source="adjusted_by.username", read_only=True)

    class Meta:
        model = ShipmentLineQuantityAdjustment
        fields = [
            "id",
            "shipment",
            "shipment_line",
            "quantity_removed",
            "previous_effective_quantity",
            "new_effective_quantity",
            "adjusted_by",
            "adjusted_by_username",
            "reason",
            "created_at",
        ]


class ShipmentLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    original_ordered_quantity = serializers.SerializerMethodField()
    effective_quantity = serializers.SerializerMethodField()
    removed_quantity = serializers.SerializerMethodField()
    picked_quantity = serializers.SerializerMethodField()
    controlled_quantity = serializers.SerializerMethodField()
    prepared_quantity = serializers.SerializerMethodField()
    shortage_quantity = serializers.SerializerMethodField()
    maximum_removable_quantity = serializers.SerializerMethodField()
    can_remove_quantity = serializers.SerializerMethodField()
    remove_blocked_reason = serializers.SerializerMethodField()
    service_status = serializers.SerializerMethodField()
    operational_line_state = serializers.SerializerMethodField()
    remaining_to_pick = serializers.SerializerMethodField()
    blocking_reason = serializers.SerializerMethodField()
    source_location_code = serializers.SerializerMethodField()
    source_location_name = serializers.SerializerMethodField()
    picking_pallet = serializers.SerializerMethodField()
    quantity_adjustments = ShipmentLineQuantityAdjustmentSerializer(many=True, read_only=True)

    def _tasks(self, obj: ShipmentLine):
        cache_name = "_shipment_line_tasks"
        if not hasattr(obj, cache_name):
            setattr(obj, cache_name, list(obj.order_line.picking_tasks.select_related("source_location").all()))
        return getattr(obj, cache_name)

    def get_picked_quantity(self, obj: ShipmentLine) -> str:
        return str(shipment_line_progress(obj).picked_quantity)

    def get_original_ordered_quantity(self, obj: ShipmentLine) -> str:
        return str(obj.ordered_quantity)

    def get_effective_quantity(self, obj: ShipmentLine) -> str:
        return str(shipment_line_effective_quantity(obj))

    def get_removed_quantity(self, obj: ShipmentLine) -> str:
        return str(obj.cancelled_quantity)

    def get_controlled_quantity(self, obj: ShipmentLine) -> str:
        return str(shipment_line_progress(obj).prepared_quantity)

    def get_prepared_quantity(self, obj: ShipmentLine) -> str:
        return str(shipment_line_progress(obj).prepared_quantity)

    def get_shortage_quantity(self, obj: ShipmentLine) -> str:
        return str(shipment_line_progress(obj).shortage_quantity)

    def get_maximum_removable_quantity(self, obj: ShipmentLine) -> str:
        return str(shipment_line_max_removable_quantity(obj))

    def get_can_remove_quantity(self, obj: ShipmentLine) -> bool:
        return shipment_line_max_removable_quantity(obj) > 0 and obj.shipment.status not in {
            Shipment.Status.DOCUMENTS_POSTED,
            Shipment.Status.DISPATCHED,
            Shipment.Status.COMPLETED,
            Shipment.Status.CANCELLED,
        }

    def get_remove_blocked_reason(self, obj: ShipmentLine) -> str:
        if obj.shipment.status in {
            Shipment.Status.DOCUMENTS_POSTED,
            Shipment.Status.DISPATCHED,
            Shipment.Status.COMPLETED,
            Shipment.Status.CANCELLED,
        }:
            return "Shipment is no longer eligible for quantity removal."
        if shipment_line_max_removable_quantity(obj) <= 0:
            return "No unpicked quantity remains removable."
        return ""

    def get_service_status(self, obj: ShipmentLine) -> str:
        return derive_shipment_line_status(obj)

    def get_operational_line_state(self, obj: ShipmentLine) -> str:
        return shipment_line_progress(obj).state

    def get_remaining_to_pick(self, obj: ShipmentLine) -> str:
        return str(shipment_line_progress(obj).remaining_to_pick)

    def get_blocking_reason(self, obj: ShipmentLine) -> str:
        return shipment_line_progress(obj).blocking_reason
    def get_source_location_code(self, obj: ShipmentLine) -> str | None:
        task = next(iter(self._tasks(obj)), None)
        return task.source_location.code if task else None

    def get_source_location_name(self, obj: ShipmentLine) -> str | None:
        task = next(iter(self._tasks(obj)), None)
        return task.source_location.name if task else None

    def get_picking_pallet(self, obj: ShipmentLine) -> str | None:
        return None

    class Meta:
        model = ShipmentLine
        fields = [
            "id",
            "shipment",
            "order_line",
            "line_number",
            "product",
            "product_sku",
            "product_name",
            "ordered_quantity",
            "original_ordered_quantity",
            "effective_quantity",
            "removed_quantity",
            "picked_quantity",
            "controlled_quantity",
            "prepared_quantity",
            "shortage_quantity",
            "maximum_removable_quantity",
            "can_remove_quantity",
            "remove_blocked_reason",
            "cancelled_quantity",
            "service_status",
            "operational_line_state",
            "remaining_to_pick",
            "blocking_reason",
            "source_location_code",
            "source_location_name",
            "delivery_date",
            "picking_pallet",
            "external_line_reference",
            "quantity_adjustments",
            "created_at",
            "updated_at",
        ]


class ShipmentSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    order_reference = serializers.CharField(source="order.external_reference", read_only=True)
    route_code = serializers.CharField(source="route_run.route.code", read_only=True, allow_null=True)
    route_name = serializers.CharField(source="route_run.route.name", read_only=True, allow_null=True)
    route_identifier = serializers.SerializerMethodField()
    route_time = serializers.TimeField(source="route_run.departure_time", read_only=True, allow_null=True)
    cutoff_time = serializers.TimeField(source="route_run.order_cutoff_time", read_only=True, allow_null=True)
    route_status = serializers.SerializerMethodField()
    picking_status = serializers.SerializerMethodField()
    control_status = serializers.SerializerMethodField()
    line_count = serializers.SerializerMethodField()
    ordered_quantity = serializers.SerializerMethodField()
    picked_quantity = serializers.SerializerMethodField()
    prepared_quantity = serializers.SerializerMethodField()
    shortage_quantity = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    transfer_reference = serializers.CharField(source="inter_branch_transfer.reference", read_only=True, allow_null=True)
    destination_branch_code = serializers.CharField(source="inter_branch_transfer.destination_branch.code", read_only=True, allow_null=True)
    activated_by_username = serializers.CharField(source="activated_by.username", read_only=True)
    prepared_by_username = serializers.CharField(source="prepared_by.username", read_only=True)
    cancelled_by_username = serializers.CharField(source="cancelled_by.username", read_only=True)
    documents_printed_by_username = serializers.CharField(source="documents_printed_by.username", read_only=True)
    documents_posted_by_username = serializers.CharField(source="documents_posted_by.username", read_only=True)
    lines = ShipmentLineSerializer(many=True, read_only=True)
    route_assignments = ShipmentRouteAssignmentSerializer(many=True, read_only=True)
    status_history = ShipmentStatusHistorySerializer(many=True, read_only=True)
    command_eligibility = serializers.SerializerMethodField()

    def _statuses(self, obj: Shipment) -> dict:
        cache_name = "_shipment_statuses"
        if not hasattr(obj, cache_name):
            setattr(obj, cache_name, derive_shipment_operational_statuses(obj))
        return getattr(obj, cache_name)

    def _projection(self, obj: Shipment):
        cache_name = "_shipment_operational_projection"
        if not hasattr(obj, cache_name):
            setattr(obj, cache_name, shipment_operational_projection(obj))
        return getattr(obj, cache_name)

    def get_route_identifier(self, obj: Shipment) -> str | None:
        if obj.route_run_id is None:
            return None
        return operational_identifier(obj.route_run.route, obj.route_run.service_date, obj.route_run.run_number)
    def get_route_status(self, obj: Shipment) -> str:
        return self._statuses(obj)["route_status"]

    def get_picking_status(self, obj: Shipment) -> str:
        return self._statuses(obj)["picking_status"]

    def get_control_status(self, obj: Shipment) -> str:
        return self._statuses(obj)["control_status"]

    def get_line_count(self, obj: Shipment) -> int:
        return obj.lines.count()

    def get_ordered_quantity(self, obj: Shipment) -> str:
        return str(self._projection(obj).effective_quantity)

    def get_picked_quantity(self, obj: Shipment) -> str:
        return str(self._projection(obj).picked_quantity)

    def get_prepared_quantity(self, obj: Shipment) -> str:
        return str(self._projection(obj).prepared_quantity)

    def get_shortage_quantity(self, obj: Shipment) -> str:
        return str(self._projection(obj).shortage_quantity)

    def get_progress_percent(self, obj: Shipment) -> float:
        return self._projection(obj).progress_percent

    def _eligibility(self, enabled: bool, reason: str = "") -> dict:
        return {"enabled": enabled, "reason": reason}

    def get_command_eligibility(self, obj: Shipment) -> dict:
        statuses = self._statuses(obj)
        terminal = obj.status in {
            Shipment.Status.DISPATCHED,
            Shipment.Status.COMPLETED,
            Shipment.Status.CANCELLED,
        }
        return {
            "activate": self._eligibility(
                obj.status == Shipment.Status.PENDING_ACTIVATION,
                "" if obj.status == Shipment.Status.PENDING_ACTIVATION else "Only pending shipments can be activated.",
            ),
            "post_picking_lists": self._eligibility(
                obj.status in [Shipment.Status.ACTIVE, Shipment.Status.PICKING],
                "" if obj.status in [Shipment.Status.ACTIVE, Shipment.Status.PICKING] else "Shipment must be active.",
            ),
            "prepare": self._eligibility(
                statuses["picking_status"] == "completed" and statuses["control_status"] == "completed" and not terminal,
                "Picking and control must be completed first.",
            ),
            "cancel": self._eligibility(
                obj.status not in [Shipment.Status.DISPATCHED, Shipment.Status.COMPLETED, Shipment.Status.CANCELLED]
                and obj.documents_posted_at is None
                and not (obj.route_run and obj.route_run.status == RouteRun.Status.CLOSED),
                "Shipment is no longer safely cancellable.",
            ),
            "print_documents": self._eligibility(
                obj.document_status != Shipment.DocumentStatus.NOT_AVAILABLE and obj.status != Shipment.Status.CANCELLED,
                "Documents are not available.",
            ),
            "post_documents": self._eligibility(
                obj.shipment_type == Shipment.ShipmentType.INTER_BRANCH
                and obj.status == Shipment.Status.PREPARED
                and obj.documents_posted_at is None,
                "Inter-branch shipment must be prepared and not already document-posted.",
            ),
            "confirm_picking_route": self._eligibility(
                obj.route_run is not None and not terminal,
                "Shipment must have an open route.",
            ),
            "close_route": self._eligibility(
                obj.route_run is not None and obj.route_run.status == RouteRun.Status.READY_TO_CLOSE,
                "Route must be ready to close.",
            ),
            "change_route": self._eligibility(
                obj.route_run is not None and not terminal and obj.documents_posted_at is None,
                "Route can be changed before terminal dispatch or posted documents.",
            ),
            "change_status": self._eligibility(
                obj.status
                in [
                    Shipment.Status.PENDING_ACTIVATION,
                    Shipment.Status.ACTIVE,
                    Shipment.Status.PICKING,
                    Shipment.Status.PICKED,
                    Shipment.Status.CONTROLLED,
                    Shipment.Status.EXCEPTION,
                ],
                "No manual transition is available.",
            ),
            "proforma": self._eligibility(bool(obj.order_id), "No source order is available."),
        }

    class Meta:
        model = Shipment
        fields = [
            "id",
            "reference",
            "branch",
            "branch_code",
            "order",
            "order_reference",
            "route_run",
            "route_code",
            "route_name",
            "route_identifier",
            "route_time",
            "cutoff_time",
            "route_status",
            "inter_branch_transfer",
            "transfer_reference",
            "destination_branch_code",
            "shipment_type",
            "status",
            "picking_status",
            "control_status",
            "document_status",
            "source_system",
            "external_reference",
            "external_order_reference",
            "external_status",
            "external_customer_account",
            "external_delivery_reference",
            "external_notes",
            "customer_name",
            "customer_alias",
            "recipient_account",
            "delivery_name",
            "delivery_address",
            "delivery_date",
            "payment_method",
            "line_count",
            "ordered_quantity",
            "picked_quantity",
            "prepared_quantity",
            "shortage_quantity",
            "progress_percent",
            "activated_at",
            "activated_by_username",
            "picking_lists_posted_at",
            "prepared_at",
            "prepared_by_username",
            "cancelled_at",
            "cancelled_by_username",
            "cancellation_reason",
            "documents_printed_at",
            "documents_printed_by_username",
            "document_print_count",
            "documents_posted_at",
            "documents_posted_by_username",
            "picking_route_confirmed_at",
            "external_created_at",
            "external_updated_at",
            "lines",
            "route_assignments",
            "status_history",
            "command_eligibility",
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
    adjustment_direction = serializers.SerializerMethodField()
    adjustment_location = serializers.SerializerMethodField()
    adjustment_location_code = serializers.SerializerMethodField()
    adjustment_reason_label = serializers.CharField(source="get_adjustment_reason_display", read_only=True)
    adjustment_quantity = serializers.SerializerMethodField()
    cycle_count_line_id = serializers.IntegerField(source="cycle_count_line.id", read_only=True)
    cycle_count_session_id = serializers.IntegerField(source="cycle_count_line.session_id", read_only=True)
    cycle_count_session_reference = serializers.CharField(source="cycle_count_line.session.reference", read_only=True)
    cycle_count_recount_id = serializers.IntegerField(source="cycle_count_recount.id", read_only=True)
    cycle_count_recount_reference = serializers.CharField(source="cycle_count_recount.reference", read_only=True)
    client_operation_id = serializers.CharField(source="scanner_quick_transfer_operation.client_operation_id", read_only=True)
    status = serializers.SerializerMethodField()
    origin = serializers.SerializerMethodField()

    def get_adjustment_direction(self, obj) -> str | None:
        if obj.movement_type != StockMovement.MovementType.ADJUSTMENT:
            return None
        if obj.adjustment_direction:
            return obj.adjustment_direction
        if obj.destination_location_id and not obj.source_location_id:
            return "increase"
        if obj.source_location_id and not obj.destination_location_id:
            return "decrease"
        return "unknown"

    def get_adjustment_location(self, obj) -> int | None:
        if obj.movement_type != StockMovement.MovementType.ADJUSTMENT:
            return None
        location = obj.destination_location or obj.source_location
        return location.id if location else None

    def get_adjustment_location_code(self, obj) -> str | None:
        if obj.movement_type != StockMovement.MovementType.ADJUSTMENT:
            return None
        location = obj.destination_location or obj.source_location
        return location.code if location else None

    def get_adjustment_quantity(self, obj) -> str | None:
        if obj.movement_type != StockMovement.MovementType.ADJUSTMENT:
            return None
        return str(abs(obj.quantity))

    def get_status(self, obj) -> str:
        return "completed"

    def get_origin(self, obj) -> str:
        if obj.movement_type == StockMovement.MovementType.TRANSFER and obj.source_location_id and obj.destination_location_id:
            return "Scanner Quick Transfer"
        if obj.movement_type == StockMovement.MovementType.RETURN_RECEIPT:
            return "External Return Document"
        if obj.movement_type == StockMovement.MovementType.SALES_CORRECTION_RECEIPT:
            return "Sales Correction"
        if obj.movement_type == StockMovement.MovementType.ADJUSTMENT and obj.cycle_count_line_id:
            return "Cycle Count variance reconciliation"
        if obj.movement_type == StockMovement.MovementType.ADJUSTMENT and obj.adjustment_direction and obj.adjustment_reason:
            return "Manual WMS stock adjustment"
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
            "adjustment_direction",
            "adjustment_location",
            "adjustment_location_code",
            "adjustment_reason",
            "adjustment_reason_label",
            "adjustment_note",
            "adjustment_quantity",
            "cycle_count_line_id",
            "cycle_count_session_id",
            "cycle_count_session_reference",
            "cycle_count_recount_id",
            "cycle_count_recount_reference",
            "client_operation_id",
            "quantity",
            "quantity_before",
            "quantity_after",
            "reference",
            "performed_by",
            "performed_by_username",
            "status",
            "origin",
            "created_at",
            "updated_at",
        ]


class CycleCountRecountSerializer(serializers.ModelSerializer):
    session_reference = serializers.CharField(source="session.reference", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    requested_by_username = serializers.CharField(source="requested_by.username", read_only=True)
    started_by_username = serializers.CharField(source="started_by.username", read_only=True)
    counted_by_username = serializers.CharField(source="counted_by.username", read_only=True)
    accepted_by_username = serializers.CharField(source="accepted_by.username", read_only=True)
    cancelled_by_username = serializers.CharField(source="cancelled_by.username", read_only=True)
    variance_quantity = serializers.SerializerMethodField()
    is_executable = serializers.SerializerMethodField()
    is_acceptable = serializers.SerializerMethodField()
    is_cancellable = serializers.SerializerMethodField()

    def get_variance_quantity(self, obj) -> str | None:
        variance = obj.variance_quantity
        return str(variance) if variance is not None else None

    def get_is_executable(self, obj) -> bool:
        return obj.status in [CycleCountRecount.Status.REQUESTED, CycleCountRecount.Status.IN_PROGRESS]

    def get_is_acceptable(self, obj) -> bool:
        return obj.status == CycleCountRecount.Status.SUBMITTED and not obj.movement_after_baseline

    def get_is_cancellable(self, obj) -> bool:
        return obj.status in [CycleCountRecount.Status.REQUESTED, CycleCountRecount.Status.IN_PROGRESS, CycleCountRecount.Status.SUBMITTED]

    class Meta:
        model = CycleCountRecount
        fields = [
            "id",
            "reference",
            "original_line",
            "session",
            "session_reference",
            "branch",
            "branch_code",
            "location",
            "location_code",
            "location_name",
            "product",
            "product_sku",
            "product_name",
            "status",
            "status_label",
            "reason",
            "requested_by",
            "requested_by_username",
            "requested_at",
            "started_by",
            "started_by_username",
            "started_at",
            "baseline_quantity",
            "baseline_at",
            "counted_quantity",
            "counted_by",
            "counted_by_username",
            "counted_at",
            "variance_quantity",
            "movement_after_baseline",
            "accepted_by",
            "accepted_by_username",
            "accepted_at",
            "cancelled_by",
            "cancelled_by_username",
            "cancelled_at",
            "review_note",
            "is_executable",
            "is_acceptable",
            "is_cancellable",
            "created_at",
            "updated_at",
        ]


class CycleCountLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True)
    counted_by_username = serializers.CharField(source="counted_by.username", read_only=True)
    reconciled_by_username = serializers.CharField(source="reconciled_by.username", read_only=True)
    variance_quantity = serializers.SerializerMethodField()
    variance_status = serializers.SerializerMethodField()
    reconciliation_status_label = serializers.SerializerMethodField()
    adjustment_id = serializers.SerializerMethodField()
    adjustment_reference = serializers.SerializerMethodField()
    can_apply_adjustment = serializers.SerializerMethodField()
    adjustment_conflict_reason = serializers.SerializerMethodField()
    recounts = CycleCountRecountSerializer(many=True, read_only=True)
    active_recount = serializers.SerializerMethodField()
    accepted_recount = serializers.SerializerMethodField()
    effective_counted_quantity = serializers.SerializerMethodField()
    effective_variance_quantity = serializers.SerializerMethodField()
    effective_result_source = serializers.SerializerMethodField()

    def get_variance_quantity(self, obj) -> str | None:
        variance = obj.variance_quantity
        return str(variance) if variance is not None else None

    def get_variance_status(self, obj) -> str:
        variance = obj.variance_quantity
        if variance is None:
            return "not_counted"
        if variance > 0:
            return "positive"
        if variance < 0:
            return "negative"
        return "zero"

    def get_reconciliation_status_label(self, obj) -> str:
        return obj.get_reconciliation_status_display() if obj.reconciliation_status else "Not reviewed"

    def get_adjustment_id(self, obj) -> int | None:
        movement = getattr(obj, "reconciliation_stock_movement", None)
        return movement.id if movement else None

    def get_adjustment_reference(self, obj) -> str | None:
        movement = getattr(obj, "reconciliation_stock_movement", None)
        return movement.reference if movement else None

    def _active_recounts(self, obj):
        return [
            recount for recount in obj.recounts.all()
            if recount.status in [
                CycleCountRecount.Status.REQUESTED,
                CycleCountRecount.Status.IN_PROGRESS,
                CycleCountRecount.Status.SUBMITTED,
            ]
        ]

    def _accepted_recounts(self, obj):
        return [recount for recount in obj.recounts.all() if recount.status == CycleCountRecount.Status.ACCEPTED]

    def _effective_recount(self, obj):
        accepted = self._accepted_recounts(obj)
        return sorted(accepted, key=lambda recount: recount.accepted_at or recount.updated_at, reverse=True)[0] if accepted else None

    def get_active_recount(self, obj) -> dict | None:
        active = self._active_recounts(obj)
        if not active:
            return None
        return CycleCountRecountSerializer(active[0]).data

    def get_accepted_recount(self, obj) -> dict | None:
        recount = self._effective_recount(obj)
        return CycleCountRecountSerializer(recount).data if recount else None

    def get_effective_counted_quantity(self, obj) -> str | None:
        recount = self._effective_recount(obj)
        if recount:
            return str(recount.counted_quantity) if recount.counted_quantity is not None else None
        return str(obj.counted_quantity) if obj.counted_quantity is not None else None

    def get_effective_variance_quantity(self, obj) -> str | None:
        recount = self._effective_recount(obj)
        if recount:
            variance = recount.variance_quantity
            return str(variance) if variance is not None else None
        variance = obj.variance_quantity
        return str(variance) if variance is not None else None

    def get_effective_result_source(self, obj) -> str:
        return "accepted_recount" if self._effective_recount(obj) else "original_count"

    def get_can_apply_adjustment(self, obj) -> bool:
        return self.get_adjustment_conflict_reason(obj) is None

    def get_adjustment_conflict_reason(self, obj) -> str | None:
        if obj.cycle_count_location.status != CycleCountLocation.Status.SUBMITTED:
            return "Location has not been submitted."
        variance = obj.variance_quantity
        if variance is None:
            return "Line has not been counted."
        if variance == 0:
            return "Line has no variance."
        if obj.reconciliation_status != CycleCountLine.ReconciliationStatus.PENDING_REVIEW:
            return "Line has already been reconciled."
        if self._active_recounts(obj):
            return "Active recount must be completed or cancelled first."
        accepted = self._effective_recount(obj)
        if accepted and accepted.movement_after_baseline:
            return "Accepted recount has movement after its baseline."
        if not accepted and obj.movement_after_snapshot:
            return "Inventory moved after the cycle count snapshot."
        return None

    class Meta:
        model = CycleCountLine
        fields = [
            "id",
            "session",
            "cycle_count_location",
            "branch",
            "location",
            "location_code",
            "product",
            "product_sku",
            "product_name",
            "expected_quantity",
            "counted_quantity",
            "variance_quantity",
            "variance_status",
            "reconciliation_status",
            "reconciliation_status_label",
            "reconciled_by",
            "reconciled_by_username",
            "reconciled_at",
            "resolution_note",
            "adjustment_id",
            "adjustment_reference",
            "can_apply_adjustment",
            "adjustment_conflict_reason",
            "recounts",
            "active_recount",
            "accepted_recount",
            "effective_counted_quantity",
            "effective_variance_quantity",
            "effective_result_source",
            "counted_by",
            "counted_by_username",
            "counted_at",
            "is_expected",
            "movement_after_snapshot",
            "created_at",
            "updated_at",
        ]


class CycleCountLocationSerializer(serializers.ModelSerializer):
    location_code = serializers.CharField(source="location.code", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    started_by_username = serializers.CharField(source="started_by.username", read_only=True)
    submitted_by_username = serializers.CharField(source="submitted_by.username", read_only=True)
    lines = CycleCountLineSerializer(many=True, read_only=True)
    expected_lines_count = serializers.SerializerMethodField()
    counted_lines_count = serializers.SerializerMethodField()
    variance_lines_count = serializers.SerializerMethodField()
    unexpected_lines_count = serializers.SerializerMethodField()

    def get_expected_lines_count(self, obj) -> int:
        return obj.lines.filter(is_expected=True).count()

    def get_counted_lines_count(self, obj) -> int:
        return obj.lines.filter(counted_quantity__isnull=False).count()

    def get_variance_lines_count(self, obj) -> int:
        return sum(1 for line in obj.lines.all() if line.variance_quantity not in [None, 0])

    def get_unexpected_lines_count(self, obj) -> int:
        return obj.lines.filter(is_expected=False).count()

    class Meta:
        model = CycleCountLocation
        fields = [
            "id",
            "session",
            "branch",
            "location",
            "location_code",
            "location_name",
            "status",
            "started_by",
            "started_by_username",
            "submitted_by",
            "submitted_by_username",
            "started_at",
            "submitted_at",
            "expected_lines_count",
            "counted_lines_count",
            "variance_lines_count",
            "unexpected_lines_count",
            "lines",
            "created_at",
            "updated_at",
        ]


class CycleCountSessionSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    opened_by_username = serializers.CharField(source="opened_by.username", read_only=True)
    reviewed_by_username = serializers.CharField(source="reviewed_by.username", read_only=True)
    cancelled_by_username = serializers.CharField(source="cancelled_by.username", read_only=True)
    locations = CycleCountLocationSerializer(many=True, read_only=True)
    locations_count = serializers.SerializerMethodField()
    submitted_locations_count = serializers.SerializerMethodField()
    lines_count = serializers.SerializerMethodField()
    counted_lines_count = serializers.SerializerMethodField()
    variance_lines_count = serializers.SerializerMethodField()
    unexpected_lines_count = serializers.SerializerMethodField()
    positive_variance_quantity = serializers.SerializerMethodField()
    negative_variance_quantity = serializers.SerializerMethodField()
    movement_warning_count = serializers.SerializerMethodField()
    pending_variance_count = serializers.SerializerMethodField()
    applied_adjustment_count = serializers.SerializerMethodField()
    no_adjustment_resolution_count = serializers.SerializerMethodField()
    zero_variance_count = serializers.SerializerMethodField()
    reconciliation_complete = serializers.SerializerMethodField()
    can_close = serializers.SerializerMethodField()
    active_recount_count = serializers.SerializerMethodField()
    submitted_recount_count = serializers.SerializerMethodField()
    accepted_recount_count = serializers.SerializerMethodField()

    def _lines(self, obj):
        return list(obj.lines.all())

    def get_locations_count(self, obj) -> int:
        return obj.locations.count()

    def get_submitted_locations_count(self, obj) -> int:
        return obj.locations.filter(status=CycleCountLocation.Status.SUBMITTED).count()

    def get_lines_count(self, obj) -> int:
        return obj.lines.count()

    def get_counted_lines_count(self, obj) -> int:
        return obj.lines.filter(counted_quantity__isnull=False).count()

    def get_variance_lines_count(self, obj) -> int:
        return sum(1 for line in self._lines(obj) if line.variance_quantity not in [None, 0])

    def get_unexpected_lines_count(self, obj) -> int:
        return obj.lines.filter(is_expected=False).count()

    def get_positive_variance_quantity(self, obj) -> str:
        total = sum((line.variance_quantity for line in self._lines(obj) if line.variance_quantity and line.variance_quantity > 0), 0)
        return str(total)

    def get_negative_variance_quantity(self, obj) -> str:
        total = sum((line.variance_quantity for line in self._lines(obj) if line.variance_quantity and line.variance_quantity < 0), 0)
        return str(abs(total))

    def get_movement_warning_count(self, obj) -> int:
        return obj.lines.filter(movement_after_snapshot=True).count()

    def get_pending_variance_count(self, obj) -> int:
        return obj.lines.filter(reconciliation_status=CycleCountLine.ReconciliationStatus.PENDING_REVIEW).count()

    def get_applied_adjustment_count(self, obj) -> int:
        return obj.lines.filter(reconciliation_status=CycleCountLine.ReconciliationStatus.ADJUSTMENT_APPLIED).count()

    def get_no_adjustment_resolution_count(self, obj) -> int:
        return obj.lines.filter(reconciliation_status=CycleCountLine.ReconciliationStatus.NO_ADJUSTMENT_REQUIRED).count()

    def get_zero_variance_count(self, obj) -> int:
        return obj.lines.filter(reconciliation_status=CycleCountLine.ReconciliationStatus.NO_VARIANCE).count()

    def get_reconciliation_complete(self, obj) -> bool:
        if obj.status == CycleCountSession.Status.CLOSED:
            return True
        return not obj.lines.filter(reconciliation_status=CycleCountLine.ReconciliationStatus.PENDING_REVIEW).exists()

    def get_can_close(self, obj) -> bool:
        if obj.status != CycleCountSession.Status.AWAITING_REVIEW:
            return False
        if obj.locations.exclude(status=CycleCountLocation.Status.SUBMITTED).exists():
            return False
        if obj.recounts.filter(status__in=[
            CycleCountRecount.Status.REQUESTED,
            CycleCountRecount.Status.IN_PROGRESS,
            CycleCountRecount.Status.SUBMITTED,
        ]).exists():
            return False
        return self.get_reconciliation_complete(obj)

    def get_active_recount_count(self, obj) -> int:
        return obj.recounts.filter(status__in=[CycleCountRecount.Status.REQUESTED, CycleCountRecount.Status.IN_PROGRESS]).count()

    def get_submitted_recount_count(self, obj) -> int:
        return obj.recounts.filter(status=CycleCountRecount.Status.SUBMITTED).count()

    def get_accepted_recount_count(self, obj) -> int:
        return obj.recounts.filter(status=CycleCountRecount.Status.ACCEPTED).count()

    class Meta:
        model = CycleCountSession
        fields = [
            "id",
            "branch",
            "branch_code",
            "reference",
            "name",
            "note",
            "status",
            "created_by",
            "created_by_username",
            "opened_by",
            "opened_by_username",
            "reviewed_by",
            "reviewed_by_username",
            "cancelled_by",
            "cancelled_by_username",
            "snapshot_at",
            "opened_at",
            "submitted_at",
            "reviewed_at",
            "cancelled_at",
            "locations_count",
            "submitted_locations_count",
            "lines_count",
            "counted_lines_count",
            "variance_lines_count",
            "unexpected_lines_count",
            "positive_variance_quantity",
            "negative_variance_quantity",
            "movement_warning_count",
            "pending_variance_count",
            "applied_adjustment_count",
            "no_adjustment_resolution_count",
            "zero_variance_count",
            "reconciliation_complete",
            "can_close",
            "active_recount_count",
            "submitted_recount_count",
            "accepted_recount_count",
            "locations",
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
    source = serializers.SerializerMethodField()
    event_type_label = serializers.SerializerMethodField()
    event_category = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    related_links = serializers.SerializerMethodField()

    CATEGORY_PREFIXES = [
        ("cycle_count", "Cycle Counts"),
        ("receive", "Receiving"),
        ("inter_branch", "Transfers"),
        ("mm_", "Transfers"),
        ("picking", "Picking"),
        ("pick", "Picking"),
        ("control", "Picking"),
        ("replenishment", "Inventory"),
        ("stock_adjustment", "Stock Adjustments"),
        ("scanner_quick_transfer", "Inventory"),
        ("transfer", "Transfers"),
        ("route", "Routes"),
    ]

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
        return operational_identifier(obj.route_run.route, obj.route_run.service_date, obj.route_run.run_number)

    def get_source(self, obj) -> str:
        return "current" if obj.created_at >= timezone.now() - timezone.timedelta(days=30) else "archive"

    def get_event_type_label(self, obj) -> str:
        value = obj.event_type or obj.action_type
        return value.replace("_", " ").title() if value else "Event"

    def get_event_category(self, obj) -> str:
        value = obj.event_type or obj.entity_name or obj.action_type
        lowered = value.lower()
        if obj.discrepancy_id or "discrepancy" in lowered:
            return "Discrepancies"
        if obj.route_run_id:
            return "Routes"
        if obj.transfer_id or obj.pallet_id:
            return "Transfers"
        for prefix, category in self.CATEGORY_PREFIXES:
            if lowered.startswith(prefix):
                return category
        if obj.product_id or obj.source_location_id or obj.destination_location_id:
            return "Inventory"
        if obj.actor_id and lowered in ["login", "logout"]:
            return "Authentication/System"
        return "Other"

    def get_metadata(self, obj) -> list[dict]:
        fields = [
            ("Action type", obj.action_type),
            ("Event type", obj.event_type),
            ("Result", obj.result),
            ("Reference", obj.reference),
            ("Entity", obj.entity_name),
            ("Entity ID", obj.entity_id),
            ("Product", obj.product.sku if obj.product_id and obj.product else None),
            ("Quantity", obj.quantity),
            ("Expected quantity", obj.expected_quantity),
            ("Checked quantity", obj.checked_quantity),
            ("Source location", obj.source_location.code if obj.source_location_id and obj.source_location else obj.source_label),
            ("Destination location", obj.destination_location.code if obj.destination_location_id and obj.destination_location else obj.destination_label),
            ("Cart", obj.cart.code if obj.cart_id and obj.cart else None),
            ("Order", obj.order.external_reference if obj.order_id and obj.order else None),
            ("Route run", self.get_route_run_label(obj)),
            ("Transfer", obj.transfer.reference if obj.transfer_id and obj.transfer else None),
            ("Pallet", obj.pallet.scan_code if obj.pallet_id and obj.pallet else None),
            ("Discrepancy", obj.discrepancy.reference if obj.discrepancy_id and obj.discrepancy else None),
        ]
        return [
            {"label": label, "value": str(value)}
            for label, value in fields
            if value not in [None, ""]
        ]

    def get_related_links(self, obj) -> list[dict]:
        links = []
        if obj.route_run_id:
            links.append({"label": "Route documents", "url": f"/wms/route-runs/{obj.route_run_id}/documents"})
        if obj.transfer_id:
            links.append({"label": "Stock transfer", "url": f"/wms/stock-transfers/{obj.transfer_id}"})
        if obj.discrepancy_id:
            links.append({"label": "Discrepancy", "url": f"/wms/discrepancies/{obj.discrepancy_id}"})
        if obj.product_id:
            links.append({"label": "Product", "url": f"/wms/products"})
        if obj.source_location_id:
            links.append({"label": "Source location", "url": f"/wms/locations/{obj.source_location_id}"})
        if obj.destination_location_id and obj.destination_location_id != obj.source_location_id:
            links.append({"label": "Destination location", "url": f"/wms/locations/{obj.destination_location_id}"})
        if obj.entity_name == "CycleCountSession" and obj.entity_id:
            links.append({"label": "Cycle Count", "url": f"/wms/cycle-counts/{obj.entity_id}"})
        if obj.entity_name == "TransferDiscrepancySourceReview" and obj.entity_id:
            links.append({"label": "Source Review", "url": f"/wms/source-discrepancy-reviews/{obj.entity_id}"})
        if obj.entity_name == "TransferDiscrepancyReconciliation" and obj.entity_id:
            links.append({"label": "Reconciliation", "url": f"/wms/discrepancy-reconciliations/{obj.entity_id}"})
        if obj.entity_name == "TransferDiscrepancyTransitInvestigation" and obj.entity_id:
            links.append({"label": "Transit Investigation", "url": f"/wms/transit-investigations/{obj.entity_id}"})
        if obj.entity_name == "TransferDiscrepancySourceStockVerification" and obj.entity_id:
            links.append({"label": "Source Stock", "url": f"/wms/source-stock-verifications/{obj.entity_id}"})
        seen = set()
        unique_links = []
        for link in links:
            key = (link["label"], link["url"])
            if key not in seen:
                seen.add(key)
                unique_links.append(link)
        return unique_links

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
            "source",
            "event_type_label",
            "event_category",
            "metadata",
            "related_links",
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


class CycleCountReviewQueueItemSerializer(serializers.Serializer):
    key = serializers.CharField()
    item_type = serializers.CharField()
    item_type_label = serializers.CharField()
    priority = serializers.IntegerField()
    branch = serializers.IntegerField()
    branch_code = serializers.CharField()
    session = serializers.IntegerField()
    session_reference = serializers.CharField()
    session_status = serializers.CharField()
    line = serializers.IntegerField(allow_null=True)
    recount = serializers.IntegerField(allow_null=True)
    recount_reference = serializers.CharField(allow_blank=True)
    location = serializers.IntegerField(allow_null=True)
    location_code = serializers.CharField(allow_blank=True)
    product = serializers.IntegerField(allow_null=True)
    product_sku = serializers.CharField(allow_blank=True)
    product_name = serializers.CharField(allow_blank=True)
    expected_quantity = serializers.CharField(allow_blank=True)
    original_counted_quantity = serializers.CharField(allow_blank=True)
    effective_counted_quantity = serializers.CharField(allow_blank=True)
    effective_variance = serializers.CharField(allow_blank=True)
    movement_after_snapshot = serializers.BooleanField()
    movement_after_baseline = serializers.BooleanField()
    is_stale = serializers.BooleanField()
    reconciliation_status = serializers.CharField(allow_blank=True)
    recount_status = serializers.CharField(allow_blank=True)
    waiting_since = serializers.DateTimeField()
    valid_actions = serializers.ListField(child=serializers.CharField())
    detail_url = serializers.CharField()


class CycleCountReviewQueueSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    variance_pending_review = serializers.IntegerField()
    stale_variance = serializers.IntegerField()
    recount_requested = serializers.IntegerField()
    recount_in_progress = serializers.IntegerField()
    recount_waiting_review = serializers.IntegerField()
    accepted_recount_pending_reconciliation = serializers.IntegerField()
    session_waiting_close = serializers.IntegerField()


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


class InventoryExceptionCategorySerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField()
    count = serializers.IntegerField()
    urgent_count = serializers.IntegerField()
    oldest_waiting_since = serializers.DateTimeField(allow_null=True)
    available = serializers.BooleanField()
    owner = serializers.CharField()
    urgency = serializers.CharField()
    included_statuses = serializers.ListField(child=serializers.CharField())


class InventoryExceptionTopItemSerializer(serializers.Serializer):
    key = serializers.CharField()
    category_key = serializers.CharField()
    category_label = serializers.CharField()
    reference = serializers.CharField()
    reason = serializers.CharField()
    status = serializers.CharField()
    waiting_since = serializers.DateTimeField(allow_null=True)
    destination = serializers.CharField()
    priority = serializers.IntegerField()


class InventoryExceptionSummarySerializer(serializers.Serializer):
    total_actionable = serializers.IntegerField()
    active_categories = serializers.IntegerField()
    leader_only_count = serializers.IntegerField()
    oldest_waiting_since = serializers.DateTimeField(allow_null=True)
    categories = InventoryExceptionCategorySerializer(many=True)
    immediate_attention = InventoryExceptionTopItemSerializer(many=True)


class TransportOverviewSummarySerializer(serializers.Serializer):
    active_route_runs = serializers.IntegerField()
    preparing_route_runs = serializers.IntegerField()
    ready_to_close_route_runs = serializers.IntegerField()
    transfers_in_transit = serializers.IntegerField()
    pallets_awaiting_receipt = serializers.IntegerField()
    unresolved_discrepancy_transfers = serializers.IntegerField()
    transit_investigations = serializers.IntegerField()


class TransportOverviewRouteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    route_code = serializers.CharField()
    route_name = serializers.CharField()
    branch_code = serializers.CharField()
    service_date = serializers.DateField()
    run_number = serializers.IntegerField()
    status = serializers.CharField()
    order_count = serializers.IntegerField()
    line_count = serializers.IntegerField()
    picked_line_count = serializers.IntegerField()
    pending_line_count = serializers.IntegerField()
    progress_percent = serializers.FloatField()
    departure_time = serializers.TimeField()
    ready_at = serializers.DateTimeField(allow_null=True)
    documents_printed_at = serializers.DateTimeField(allow_null=True)
    destination = serializers.CharField()


class TransportAttentionItemSerializer(serializers.Serializer):
    key = serializers.CharField()
    item_type = serializers.CharField()
    label = serializers.CharField()
    reference = serializers.CharField()
    source_branch_code = serializers.CharField(allow_blank=True)
    destination_branch_code = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    waiting_since = serializers.DateTimeField(allow_null=True)
    destination = serializers.CharField()
    priority = serializers.IntegerField()


class TransportOverviewSerializer(serializers.Serializer):
    summary = TransportOverviewSummarySerializer()
    active_routes = TransportOverviewRouteSerializer(many=True)
    attention_items = TransportAttentionItemSerializer(many=True)
