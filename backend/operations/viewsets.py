import django_filters
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

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
from operations.serializers import (
    AuditLogSerializer,
    DeliveryRouteSerializer,
    OrderLineSerializer,
    OrderSerializer,
    PickingTaskSerializer,
    ReturnBatchSerializer,
    ReturnLineSerializer,
    RouteRunSerializer,
    StockMovementSerializer,
)
from operations.services import is_route_late, is_route_work_fully_prepared, recalculate_route_readiness
from warehouse.models import InventoryItem


class AuditLogFilter(django_filters.FilterSet):
    action = django_filters.CharFilter(field_name="action_type")

    class Meta:
        model = AuditLog
        fields = ["actor", "action", "action_type"]


class RouteRunFilter(django_filters.FilterSet):
    branch = django_filters.NumberFilter(field_name="route__branch_id")
    branch_code = django_filters.CharFilter(field_name="route__branch__code", lookup_expr="iexact")

    class Meta:
        model = RouteRun
        fields = ["route", "branch", "branch_code", "status", "service_date", "departure_time"]


class DeliveryRouteViewSet(ReadOnlyModelViewSet):
    queryset = DeliveryRoute.objects.select_related("branch")
    serializer_class = DeliveryRouteSerializer
    filterset_fields = ["branch", "code", "is_active"]
    search_fields = ["code", "name", "branch__code", "branch__name"]
    ordering_fields = ["branch__code", "code", "name", "created_at", "updated_at"]


class OrderLineFilter(django_filters.FilterSet):
    route_run = django_filters.NumberFilter(field_name="order__route_run_id")

    class Meta:
        model = OrderLine
        fields = ["order", "product", "route_run"]


class PickingTaskFilter(django_filters.FilterSet):
    route_run = django_filters.NumberFilter(field_name="order_line__order__route_run_id")

    class Meta:
        model = PickingTask
        fields = ["branch", "status", "assigned_to", "route_run"]


class RouteRunViewSet(ReadOnlyModelViewSet):
    queryset = RouteRun.objects.select_related("route", "route__branch")
    serializer_class = RouteRunSerializer
    filterset_class = RouteRunFilter
    search_fields = ["route__code", "route__name", "route__branch__code"]
    ordering_fields = ["service_date", "departure_time", "run_number", "status", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            return queryset.exclude(status=RouteRun.Status.CLOSED)
        return queryset

    @action(detail=False, methods=["get"])
    def archive(self, request):
        queryset = self.filter_queryset(
            self.get_queryset()
            .filter(status=RouteRun.Status.CLOSED)
            .order_by("-closed_at", "-updated_at")
        )
        date_from = parse_date(request.query_params.get("date_from", ""))
        date_to = parse_date(request.query_params.get("date_to", ""))

        if date_from is not None:
            queryset = queryset.filter(closed_at__date__gte=date_from)
        if date_to is not None:
            queryset = queryset.filter(closed_at__date__lte=date_to)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="print-documents")
    def print_documents(self, request, pk=None):
        route_run = self.get_object()
        recalculate_route_readiness(route_run)
        route_run.refresh_from_db()

        if route_run.status == RouteRun.Status.CLOSED:
            return Response({"detail": "Route run is already closed."}, status=status.HTTP_400_BAD_REQUEST)

        if not is_route_work_fully_prepared(route_run):
            return Response({"detail": "Route run is not ready to close."}, status=status.HTTP_400_BAD_REQUEST)

        was_printed = route_run.documents_printed_at is not None
        route_run.documents_printed_at = timezone.now()
        route_run.save(update_fields=["documents_printed_at", "updated_at"])
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.UPDATE,
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=(
                f"Route documents {'reprinted' if was_printed else 'printed'} "
                f"for route run {route_run.id}."
            ),
        )

        serializer = self.get_serializer(route_run)
        return Response({"message": "Route documents printed.", "route_run": serializer.data})

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        route_run = self.get_object()
        recalculate_route_readiness(route_run)
        route_run.refresh_from_db()

        if route_run.status == RouteRun.Status.CLOSED:
            return Response({"detail": "Route run is already closed."}, status=status.HTTP_400_BAD_REQUEST)

        if not is_route_work_fully_prepared(route_run):
            return Response({"detail": "Route run is not ready to close."}, status=status.HTTP_400_BAD_REQUEST)

        if route_run.documents_printed_at is None:
            return Response({"detail": "Route documents must be printed before closing."}, status=status.HTTP_400_BAD_REQUEST)

        closed_late = is_route_late(route_run)
        route_run.status = RouteRun.Status.CLOSED
        route_run.closed_at = timezone.now()
        route_run.save(update_fields=["status", "closed_at", "updated_at"])
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.STATUS_CHANGE,
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=f"Route run {route_run.id} closed {'late' if closed_late else 'on time'}.",
        )

        serializer = self.get_serializer(route_run)
        return Response({"message": "Route run closed.", "route_run": serializer.data})


class OrderViewSet(ReadOnlyModelViewSet):
    queryset = Order.objects.select_related("branch", "route_run", "route_run__route")
    serializer_class = OrderSerializer
    filterset_fields = ["branch", "status", "external_reference", "route_run"]
    search_fields = ["external_reference", "customer_name", "branch__code", "route_run__route__code"]
    ordering_fields = ["external_reference", "status", "requested_ship_date", "created_at", "updated_at"]


class OrderLineViewSet(ReadOnlyModelViewSet):
    queryset = OrderLine.objects.select_related("order", "product").prefetch_related("picking_tasks__source_location")
    serializer_class = OrderLineSerializer
    filterset_class = OrderLineFilter
    search_fields = ["order__external_reference", "product__sku", "product__name", "order__route_run__route__code"]
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
    filterset_class = PickingTaskFilter
    search_fields = [
        "order_line__order__external_reference",
        "order_line__product__sku",
        "source_location__code",
        "assigned_to__username",
    ]
    ordering_fields = ["status", "created_at", "updated_at"]

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        with transaction.atomic():
            task = (
                get_object_or_404(
                    PickingTask.objects.select_for_update().select_related(
                        "branch",
                        "order_line__product",
                        "source_location",
                    ),
                    pk=pk,
                )
            )
            location_code = str(request.data.get("location_code", "")).strip()
            product_code = str(request.data.get("product_code", "")).strip()

            if task.status == PickingTask.Status.COMPLETED:
                return Response({"detail": "Picking task is already completed."}, status=status.HTTP_400_BAD_REQUEST)

            if task.status == PickingTask.Status.CANCELLED:
                return Response({"detail": "Cancelled picking task cannot be completed."}, status=status.HTTP_400_BAD_REQUEST)

            quantity_to_pick = task.quantity_to_pick - task.quantity_picked
            if quantity_to_pick <= 0:
                return Response({"detail": "Picking task has no remaining quantity to pick."}, status=status.HTTP_400_BAD_REQUEST)

            order_line = task.order_line
            product = order_line.product

            if not location_code:
                return Response({"detail": "Location code is required."}, status=status.HTTP_400_BAD_REQUEST)

            if location_code != task.source_location.code:
                return Response({"detail": "Scanned location does not match the task source location."}, status=status.HTTP_400_BAD_REQUEST)

            if not product_code:
                return Response({"detail": "Product barcode or SKU is required."}, status=status.HTTP_400_BAD_REQUEST)

            if product_code not in {product.sku, product.barcode}:
                return Response({"detail": "Scanned product does not match the picking task product."}, status=status.HTTP_400_BAD_REQUEST)

            order_remaining = order_line.quantity_ordered - order_line.quantity_picked
            if quantity_to_pick > order_remaining:
                return Response({"detail": "Completing this task would overpick the order line."}, status=status.HTTP_400_BAD_REQUEST)

            inventory_item = (
                InventoryItem.objects.select_for_update()
                .filter(
                    branch=task.branch,
                    location=task.source_location,
                    product=product,
                )
                .first()
            )
            if inventory_item is None:
                return Response({"detail": "No inventory found at the source location."}, status=status.HTTP_400_BAD_REQUEST)

            if inventory_item.quantity_on_hand < quantity_to_pick:
                return Response({"detail": "Not enough stock at the source location."}, status=status.HTTP_400_BAD_REQUEST)

            task.quantity_picked = task.quantity_to_pick
            task.quantity_prepared = task.quantity_to_pick
            task.status = PickingTask.Status.COMPLETED
            task.save(update_fields=["quantity_picked", "quantity_prepared", "status", "updated_at"])

            order_line.quantity_picked = F("quantity_picked") + quantity_to_pick
            order_line.save(update_fields=["quantity_picked", "updated_at"])

            inventory_item.quantity_on_hand = F("quantity_on_hand") - quantity_to_pick
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

            StockMovement.objects.create(
                branch=task.branch,
                product=product,
                inventory_item=inventory_item,
                source_location=task.source_location,
                movement_type=StockMovement.MovementType.PICK,
                quantity=quantity_to_pick,
                reference=f"PICK-TASK-{task.id}",
                performed_by=None,
            )
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="PickingTask",
                entity_id=str(task.id),
                message=f"Picking task {task.id} completed.",
            )

            task.refresh_from_db()
            recalculate_route_readiness(task.order_line.order.route_run)

        serializer = self.get_serializer(task)
        return Response(
            {
                "message": "Picking task completed successfully.",
                "task": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


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

    @action(detail=False, methods=["get"])
    def current(self, request):
        since = timezone.now() - timezone.timedelta(days=30)
        queryset = self.filter_queryset(self.get_queryset().filter(created_at__gte=since).order_by("-created_at"))
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def archive(self, request):
        date_from = parse_date(request.query_params.get("date_from", ""))
        date_to = parse_date(request.query_params.get("date_to", ""))

        if date_from is None or date_to is None:
            return Response(
                {"detail": "date_from and date_to query parameters are required in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if date_from > date_to:
            return Response(
                {"detail": "date_from must be earlier than or equal to date_to."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = self.filter_queryset(
            self.get_queryset()
            .filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
            .order_by("-created_at")
        )
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
