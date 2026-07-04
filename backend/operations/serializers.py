from decimal import Decimal

from rest_framework import serializers

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
        total_quantity = sum((task.quantity_to_pick for task in tasks), Decimal("0"))
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
    source_location_code = serializers.CharField(source="source_location.code", read_only=True)
    source_location_name = serializers.CharField(source="source_location.name", read_only=True)
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)
    remaining_quantity = serializers.SerializerMethodField()
    remaining_to_prepare = serializers.SerializerMethodField()

    def get_remaining_quantity(self, obj: PickingTask) -> str:
        return str(obj.quantity_to_pick - obj.quantity_picked)

    def get_remaining_to_prepare(self, obj: PickingTask) -> str:
        return str(obj.quantity_to_pick - obj.quantity_prepared)

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
            "source_location",
            "source_location_code",
            "source_location_name",
            "assigned_to",
            "assigned_to_username",
            "status",
            "quantity_to_pick",
            "quantity_picked",
            "quantity_prepared",
            "remaining_quantity",
            "remaining_to_prepare",
            "created_at",
            "updated_at",
        ]


class StockMovementSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    source_location_code = serializers.CharField(source="source_location.code", read_only=True)
    destination_location_code = serializers.CharField(source="destination_location.code", read_only=True)
    performed_by_username = serializers.CharField(source="performed_by.username", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "branch",
            "branch_code",
            "product",
            "product_sku",
            "inventory_item",
            "source_location",
            "source_location_code",
            "destination_location",
            "destination_location_code",
            "movement_type",
            "quantity",
            "reference",
            "performed_by",
            "performed_by_username",
            "created_at",
            "updated_at",
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "actor_username",
            "action_type",
            "entity_name",
            "entity_id",
            "message",
            "created_at",
        ]
