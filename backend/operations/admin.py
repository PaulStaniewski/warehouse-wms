from django.contrib import admin

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


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(DeliveryRoute)
class DeliveryRouteAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "branch", "is_active", "updated_at"]
    list_filter = ["branch", "is_active"]
    search_fields = ["code", "name", "branch__code", "branch__name"]


@admin.register(RouteRun)
class RouteRunAdmin(admin.ModelAdmin):
    list_display = [
        "route",
        "service_date",
        "run_number",
        "departure_time",
        "status",
        "orders_count",
        "pending_lines_count",
        "is_urgent",
        "is_selectable",
    ]
    list_filter = ["status", "service_date", "route__branch", "route"]
    search_fields = ["route__code", "route__name", "route__branch__code"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "external_reference",
        "branch",
        "route_run",
        "status",
        "customer_name",
        "requested_ship_date",
        "created_at",
    ]
    list_filter = ["status", "branch", "route_run", "requested_ship_date"]
    search_fields = ["external_reference", "customer_name", "route_run__route__code"]
    inlines = [OrderLineInline]


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ["order", "line_number", "product", "quantity_ordered", "quantity_picked"]
    list_filter = ["order__branch"]
    search_fields = ["order__external_reference", "product__sku", "product__name"]


class ReturnLineInline(admin.TabularInline):
    model = ReturnLine
    extra = 0


@admin.register(ReturnBatch)
class ReturnBatchAdmin(admin.ModelAdmin):
    list_display = ["reference", "branch", "status", "received_at", "created_at"]
    list_filter = ["status", "branch"]
    search_fields = ["reference"]
    inlines = [ReturnLineInline]


@admin.register(ReturnLine)
class ReturnLineAdmin(admin.ModelAdmin):
    list_display = ["return_batch", "line_number", "product", "quantity", "condition"]
    list_filter = ["condition", "return_batch__branch"]
    search_fields = ["return_batch__reference", "product__sku", "product__name"]


@admin.register(PickingTask)
class PickingTaskAdmin(admin.ModelAdmin):
    list_display = [
        "order_line",
        "branch",
        "source_location",
        "assigned_to",
        "status",
        "quantity_to_pick",
        "quantity_picked",
    ]
    list_filter = ["status", "branch", "assigned_to"]
    search_fields = [
        "order_line__order__external_reference",
        "order_line__product__sku",
        "source_location__code",
    ]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["movement_type", "product", "branch", "quantity", "reference", "performed_by", "created_at"]
    list_filter = ["movement_type", "branch", "performed_by"]
    search_fields = ["product__sku", "product__name", "reference"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["action_type", "entity_name", "entity_id", "actor", "created_at"]
    list_filter = ["action_type", "entity_name", "actor"]
    search_fields = ["entity_name", "entity_id", "message", "actor__username"]
    readonly_fields = ["created_at"]
