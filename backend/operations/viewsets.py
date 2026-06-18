import django_filters
from rest_framework.viewsets import ReadOnlyModelViewSet

from operations.models import (
    AuditLog,
    Order,
    OrderLine,
    PickingTask,
    ReturnBatch,
    ReturnLine,
    StockMovement,
)
from operations.serializers import (
    AuditLogSerializer,
    OrderLineSerializer,
    OrderSerializer,
    PickingTaskSerializer,
    ReturnBatchSerializer,
    ReturnLineSerializer,
    StockMovementSerializer,
)


class AuditLogFilter(django_filters.FilterSet):
    action = django_filters.CharFilter(field_name="action_type")

    class Meta:
        model = AuditLog
        fields = ["actor", "action", "action_type"]


class OrderViewSet(ReadOnlyModelViewSet):
    queryset = Order.objects.select_related("branch")
    serializer_class = OrderSerializer
    filterset_fields = ["branch", "status", "external_reference"]
    search_fields = ["external_reference", "customer_name", "branch__code"]
    ordering_fields = ["external_reference", "status", "requested_ship_date", "created_at", "updated_at"]


class OrderLineViewSet(ReadOnlyModelViewSet):
    queryset = OrderLine.objects.select_related("order", "product")
    serializer_class = OrderLineSerializer
    filterset_fields = ["order", "product"]
    search_fields = ["order__external_reference", "product__sku", "product__name"]
    ordering_fields = ["order", "line_number", "created_at", "updated_at"]


class ReturnBatchViewSet(ReadOnlyModelViewSet):
    queryset = ReturnBatch.objects.select_related("branch")
    serializer_class = ReturnBatchSerializer
    filterset_fields = ["branch", "status"]
    search_fields = ["reference", "branch__code"]
    ordering_fields = ["reference", "status", "received_at", "created_at", "updated_at"]


class ReturnLineViewSet(ReadOnlyModelViewSet):
    queryset = ReturnLine.objects.select_related("return_batch", "product")
    serializer_class = ReturnLineSerializer
    filterset_fields = ["return_batch", "product"]
    search_fields = ["return_batch__reference", "product__sku", "product__name"]
    ordering_fields = ["return_batch", "line_number", "created_at", "updated_at"]


class PickingTaskViewSet(ReadOnlyModelViewSet):
    queryset = PickingTask.objects.select_related(
        "assigned_to",
        "branch",
        "order_line__order",
        "order_line__product",
        "source_location",
    )
    serializer_class = PickingTaskSerializer
    filterset_fields = ["branch", "status", "assigned_to"]
    search_fields = [
        "order_line__order__external_reference",
        "order_line__product__sku",
        "source_location__code",
        "assigned_to__username",
    ]
    ordering_fields = ["status", "created_at", "updated_at"]


class StockMovementViewSet(ReadOnlyModelViewSet):
    queryset = StockMovement.objects.select_related(
        "branch",
        "product",
        "inventory_item",
        "source_location",
        "destination_location",
        "performed_by",
    )
    serializer_class = StockMovementSerializer
    filterset_fields = ["branch", "product", "movement_type"]
    search_fields = ["product__sku", "product__name", "reference", "branch__code"]
    ordering_fields = ["movement_type", "quantity", "created_at", "updated_at"]


class AuditLogViewSet(ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("actor")
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    search_fields = ["entity_name", "entity_id", "message", "actor__username"]
    ordering_fields = ["action_type", "entity_name", "created_at"]
