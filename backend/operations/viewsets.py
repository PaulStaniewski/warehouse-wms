from decimal import Decimal

import django_filters
from django.shortcuts import get_object_or_404
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet, ViewSet

from accounts.authorization import (
    branch_codes_filter,
    branch_ids_filter,
    filter_rows_for_user,
    require_any_branch_access,
    require_branch_access,
)
from operations.models import (
    AuditLog,
    DeliveryRoute,
    InterBranchTransfer,
    Order,
    OrderLine,
    PalletReceivingScan,
    PickingShortage,
    PickingTask,
    ReplenishmentRequest,
    ReturnBatch,
    ReturnLine,
    RouteRun,
    StockMovement,
    TransferDiscrepancy,
    TransferDiscrepancyManualReconciliationDecision,
    TransferDiscrepancyReconciliation,
    TransferDiscrepancyRecovery,
    TransferDiscrepancyShortageConfirmation,
    TransferDiscrepancySourceStockRecovery,
    TransferDiscrepancySourceStockVerification,
    TransferDiscrepancySourceStockVerificationItem,
    TransferDiscrepancySourceReview,
    TransferDiscrepancyTransitInvestigation,
    TransferPallet,
)
from operations.serializers import (
    AuditLogSerializer,
    DeliveryRouteSerializer,
    OrderLineSerializer,
    OrderSerializer,
    PickingTaskSerializer,
    PickingShortageSerializer,
    ReplenishmentRequestSerializer,
    ReturnBatchSerializer,
    ReturnLineSerializer,
    RouteRunSerializer,
    StockMovementSerializer,
    TransferDiscrepancyActionSerializer,
    TransferDiscrepancyReconciliationSerializer,
    TransferDiscrepancySerializer,
    TransferDiscrepancySourceStockVerificationSerializer,
    TransferDiscrepancySourceReviewSerializer,
    TransferDiscrepancyTransitInvestigationSerializer,
)
from operations.services import is_route_late, is_route_work_fully_prepared, recalculate_route_readiness
from operations.services import (
    DiscrepancyLocationMissing,
    complete_source_verification_if_finished,
    build_transfer_discrepancy_action_queue,
    discrepancy_line_remaining,
    ensure_reconciliation_for_source_review,
    ensure_source_stock_verification_for_reconciliation,
    ensure_transit_investigation_for_reconciliation,
    finalize_discrepancy_if_complete,
    get_discrepancy_investigation_totals,
    get_source_verification_totals,
    get_discrepancy_location,
    source_verification_item_remaining,
)
from warehouse.models import InventoryItem, Location, Product


class TransferDiscrepancyActionPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def format_piece_quantity(value) -> str:
    quantity = Decimal(value)
    if quantity == quantity.to_integral_value():
        return str(int(quantity))
    return str(quantity.normalize())


def unit_label(value, singular="unit", plural="units") -> str:
    return singular if Decimal(value) == 1 else plural


def actor_code(request) -> str:
    if request.user and request.user.is_authenticated:
        return request.user.username
    return str(request.data.get("worker_code", "")).strip() or "DEMO"


def audit_actor(request):
    return request.user if request.user and request.user.is_authenticated else None


def filter_branch_queryset(queryset, request, field_prefix: str, param_name: str = "branch"):
    if not request.user.is_authenticated:
        return queryset
    requested = request.query_params.get(param_name, "").strip()
    field = f"{field_prefix}__id" if field_prefix else "id"
    code_field = f"{field_prefix}__code" if field_prefix else "code"
    if requested.isdigit() or not requested:
        return queryset.filter(**{f"{field}__in": branch_ids_filter(request.user, requested)})
    return queryset.filter(**{f"{code_field}__in": branch_codes_filter(request.user, requested)})


def filter_dual_branch_queryset(queryset, request, source_path: str, destination_path: str):
    if not request.user.is_authenticated:
        return queryset
    requested = request.query_params.get("branch", "").strip()
    if requested:
        codes = branch_codes_filter(request.user, requested)
    else:
        codes = branch_codes_filter(request.user)
    return queryset.filter(
        models.Q(**{f"{source_path}__code__in": codes})
        | models.Q(**{f"{destination_path}__code__in": codes})
    ).distinct()


class AuditLogFilter(django_filters.FilterSet):
    actor = django_filters.CharFilter(method="filter_actor")
    action = django_filters.CharFilter(field_name="action_type")
    product = django_filters.CharFilter(field_name="product__sku", lookup_expr="iexact")
    cart = django_filters.CharFilter(field_name="cart__code", lookup_expr="iexact")
    location = django_filters.CharFilter(method="filter_location")
    order = django_filters.CharFilter(field_name="order__external_reference", lookup_expr="iexact")
    actor_name = django_filters.CharFilter(field_name="actor__username", lookup_expr="iexact")
    event_type = django_filters.CharFilter(field_name="event_type", lookup_expr="iexact")
    result = django_filters.CharFilter(field_name="result", lookup_expr="iexact")

    def filter_location(self, queryset, name, value):
        return queryset.filter(
            models.Q(source_location__code__iexact=value)
            | models.Q(destination_location__code__iexact=value)
            | models.Q(source_label__iexact=value)
            | models.Q(destination_label__iexact=value)
        )

    def filter_actor(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(actor_id=value)
        return queryset.filter(models.Q(actor__username__iexact=value) | models.Q(message__icontains=f"Worker {value} "))

    class Meta:
        model = AuditLog
        fields = ["actor", "actor_name", "action", "action_type", "event_type", "result", "product", "cart", "location", "order"]


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

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")


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


class PickingShortageFilter(django_filters.FilterSet):
    branch = django_filters.CharFilter(field_name="branch__code", lookup_expr="iexact")
    product = django_filters.CharFilter(field_name="product__sku", lookup_expr="iexact")
    location = django_filters.CharFilter(field_name="reported_location__code", lookup_expr="iexact")
    actor = django_filters.CharFilter(method="filter_actor")
    date_from = django_filters.DateFilter(field_name="reported_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="reported_at", lookup_expr="date__lte")

    def filter_actor(self, queryset, name, value):
        return queryset.filter(
            models.Q(reported_by__username__iexact=value) | models.Q(reported_by_worker_code__iexact=value)
        )

    class Meta:
        model = PickingShortage
        fields = ["branch", "status", "product", "location", "actor", "date_from", "date_to"]


class ReplenishmentRequestFilter(django_filters.FilterSet):
    branch = django_filters.CharFilter(field_name="branch__code", lookup_expr="iexact")
    product = django_filters.CharFilter(field_name="product__sku", lookup_expr="iexact")
    customer_alias = django_filters.CharFilter(field_name="customer_alias", lookup_expr="icontains")
    order = django_filters.CharFilter(field_name="order_reference", lookup_expr="iexact")

    class Meta:
        model = ReplenishmentRequest
        fields = ["branch", "status", "product", "customer_alias", "order"]


class RouteRunViewSet(ReadOnlyModelViewSet):
    queryset = RouteRun.objects.select_related("route", "route__branch")
    serializer_class = RouteRunSerializer
    filterset_class = RouteRunFilter
    search_fields = ["route__code", "route__name", "route__branch__code"]
    ordering_fields = ["service_date", "departure_time", "run_number", "status", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = filter_branch_queryset(queryset, self.request, "route__branch")
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
        if request.user.is_authenticated:
            require_branch_access(request.user, route_run.route.branch)
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
            actor=audit_actor(request),
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
        if request.user.is_authenticated:
            require_branch_access(request.user, route_run.route.branch)
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
            actor=audit_actor(request),
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
    filterset_fields = ["status", "external_reference", "route_run"]
    search_fields = ["external_reference", "customer_name", "branch__code", "route_run__route__code"]
    ordering_fields = ["external_reference", "status", "requested_ship_date", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = filter_branch_queryset(super().get_queryset(), self.request, "branch")
        branch = self.request.query_params.get("branch", "").strip()
        if branch:
            if branch.isdigit():
                queryset = queryset.filter(branch_id=branch)
            else:
                queryset = queryset.filter(branch__code__iexact=branch)
        return queryset


class OrderLineViewSet(ReadOnlyModelViewSet):
    queryset = OrderLine.objects.select_related("order", "product").prefetch_related("picking_tasks__source_location")
    serializer_class = OrderLineSerializer
    filterset_class = OrderLineFilter
    search_fields = ["order__external_reference", "product__sku", "product__name", "order__route_run__route__code"]
    ordering_fields = ["order", "line_number", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "order__branch")


class ReturnBatchViewSet(ReadOnlyModelViewSet):
    queryset = ReturnBatch.objects.select_related("branch")
    serializer_class = ReturnBatchSerializer
    filterset_fields = ["branch", "status"]
    search_fields = ["reference", "branch__code"]
    ordering_fields = ["reference", "status", "received_at", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")


class ReturnLineViewSet(ReadOnlyModelViewSet):
    queryset = ReturnLine.objects.select_related("return_batch", "product")
    serializer_class = ReturnLineSerializer
    filterset_fields = ["return_batch", "product"]
    search_fields = ["return_batch__reference", "product__sku", "product__name"]
    ordering_fields = ["return_batch", "line_number", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "return_batch__branch")


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

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")

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


class PickingShortageViewSet(ReadOnlyModelViewSet):
    queryset = PickingShortage.objects.select_related(
        "branch",
        "product",
        "reported_location",
        "unconfirmed_location",
        "cart",
        "order",
        "reported_by",
        "found_location",
        "found_by",
        "confirmed_missing_by",
    )
    serializer_class = PickingShortageSerializer
    filterset_class = PickingShortageFilter
    search_fields = [
        "reference",
        "product__sku",
        "product__name",
        "reported_location__code",
        "cart__code",
        "order__external_reference",
        "customer_alias_snapshot",
        "reported_by__username",
        "reported_by_worker_code",
    ]
    ordering_fields = ["reported_at", "status", "quantity", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")

    @action(detail=True, methods=["post"], url_path="found-stock")
    def found_stock(self, request, pk=None):
        quantity = Decimal(str(request.data.get("quantity", "0")))
        location_code = str(request.data.get("location_code", "")).strip()
        worker_code = str(request.data.get("worker_code", "")).strip()
        note = str(request.data.get("note", "")).strip()
        if quantity <= 0:
            return Response({"detail": "quantity must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
        if not location_code:
            return Response({"detail": "location_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            shortage = (
                PickingShortage.objects.select_for_update(of=("self",))
                .select_related("branch", "product", "reported_location", "unconfirmed_location", "order")
                .get(pk=pk)
            )
            require_branch_access(request.user, shortage.branch)
            if quantity > shortage.unresolved_quantity:
                return Response({"detail": "Found quantity exceeds unresolved shortage quantity."}, status=status.HTTP_400_BAD_REQUEST)
            destination = Location.objects.filter(branch=shortage.branch, code__iexact=location_code).first()
            if destination is None:
                return Response({"detail": "Destination location not found in this branch."}, status=status.HTTP_400_BAD_REQUEST)
            if destination.id == shortage.unconfirmed_location_id:
                return Response({"detail": "UNCONFIRMED cannot be the found stock destination."}, status=status.HTTP_400_BAD_REQUEST)

            source_item = InventoryItem.objects.select_for_update().filter(
                branch=shortage.branch, location=shortage.unconfirmed_location, product=shortage.product
            ).first()
            if source_item is None or source_item.quantity_on_hand < quantity:
                return Response({"detail": "UNCONFIRMED inventory is insufficient."}, status=status.HTTP_400_BAD_REQUEST)
            destination_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=shortage.branch,
                location=destination,
                product=shortage.product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            source_item.quantity_on_hand = F("quantity_on_hand") - quantity
            source_item.save(update_fields=["quantity_on_hand", "updated_at"])
            destination_item.quantity_on_hand = F("quantity_on_hand") + quantity
            destination_item.save(update_fields=["quantity_on_hand", "updated_at"])
            movement = StockMovement.objects.create(
                branch=shortage.branch,
                product=shortage.product,
                inventory_item=destination_item,
                source_location=shortage.unconfirmed_location,
                destination_location=destination,
                movement_type=StockMovement.MovementType.PICKING_SHORTAGE_FOUND,
                quantity=quantity,
                reference=shortage.reference,
                performed_by=request.user if request.user.is_authenticated else None,
            )
            shortage.recovered_quantity = F("recovered_quantity") + quantity
            shortage.found_location = destination
            shortage.found_by = request.user if request.user.is_authenticated else None
            shortage.found_by_worker_code = worker_code
            shortage.found_at = timezone.now()
            shortage.note = note or shortage.note
            shortage.save(update_fields=[
                "recovered_quantity",
                "found_location",
                "found_by",
                "found_by_worker_code",
                "found_at",
                "note",
                "updated_at",
            ])
            shortage.refresh_from_db()
            if shortage.unresolved_quantity <= 0 and shortage.status != PickingShortage.Status.FOUND:
                shortage.status = PickingShortage.Status.FOUND
                shortage.save(update_fields=["status", "updated_at"])

            AuditLog.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="picking_shortage_found",
                branch=shortage.branch,
                product=shortage.product,
                quantity=quantity,
                source_location=shortage.unconfirmed_location,
                destination_location=destination,
                source_label=shortage.unconfirmed_location.code,
                destination_label=destination.code,
                cart=shortage.cart,
                order=shortage.order,
                reference=shortage.reference,
                entity_name="PickingShortage",
                entity_id=str(shortage.id),
                message=(
                    f"Worker {worker_code or request.user.username} found {format_piece_quantity(quantity)} {shortage.product.sku} "
                    f"and moved it from {shortage.unconfirmed_location.code} to {destination.code}."
                ),
            )

        shortage = self.get_queryset().get(pk=shortage.pk)
        return Response({"message": "Stock found recorded.", "shortage": self.get_serializer(shortage).data})

    @action(detail=True, methods=["post"], url_path="confirm-missing")
    def confirm_missing(self, request, pk=None):
        worker_code = str(request.data.get("worker_code", "")).strip()
        note = str(request.data.get("note", "")).strip()
        with transaction.atomic():
            shortage = (
                PickingShortage.objects.select_for_update(of=("self",))
                .select_related("branch", "product", "unconfirmed_location", "order")
                .get(pk=pk)
            )
            require_branch_access(request.user, shortage.branch, leader_required=True)
            quantity = shortage.unresolved_quantity
            if quantity <= 0:
                return Response({"detail": "Shortage has no unresolved quantity."}, status=status.HTTP_400_BAD_REQUEST)
            source_item = InventoryItem.objects.select_for_update().filter(
                branch=shortage.branch, location=shortage.unconfirmed_location, product=shortage.product
            ).first()
            if source_item is None or source_item.quantity_on_hand < quantity:
                return Response({"detail": "UNCONFIRMED inventory is insufficient."}, status=status.HTTP_400_BAD_REQUEST)
            source_item.quantity_on_hand = F("quantity_on_hand") - quantity
            source_item.save(update_fields=["quantity_on_hand", "updated_at"])
            StockMovement.objects.create(
                branch=shortage.branch,
                product=shortage.product,
                inventory_item=source_item,
                source_location=shortage.unconfirmed_location,
                movement_type=StockMovement.MovementType.PICKING_SHORTAGE_CONFIRMED_MISSING,
                quantity=quantity,
                reference=shortage.reference,
                performed_by=request.user if request.user.is_authenticated else None,
            )
            shortage.confirmed_missing_quantity = F("confirmed_missing_quantity") + quantity
            shortage.confirmed_missing_by = request.user if request.user.is_authenticated else None
            shortage.confirmed_missing_by_worker_code = worker_code
            shortage.confirmed_missing_at = timezone.now()
            shortage.status = PickingShortage.Status.CONFIRMED_MISSING
            shortage.note = note or shortage.note
            shortage.save(update_fields=[
                "confirmed_missing_quantity",
                "confirmed_missing_by",
                "confirmed_missing_by_worker_code",
                "confirmed_missing_at",
                "status",
                "note",
                "updated_at",
            ])
            shortage.refresh_from_db()
            AuditLog.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="picking_shortage_confirmed_missing",
                branch=shortage.branch,
                product=shortage.product,
                quantity=quantity,
                source_location=shortage.unconfirmed_location,
                source_label=shortage.unconfirmed_location.code,
                cart=shortage.cart,
                order=shortage.order,
                reference=shortage.reference,
                entity_name="PickingShortage",
                entity_id=str(shortage.id),
                message=f"Worker {worker_code or request.user.username} confirmed {format_piece_quantity(quantity)} {shortage.product.sku} as physically missing.",
            )

        shortage = self.get_queryset().get(pk=shortage.pk)
        return Response({"message": "Physical loss confirmed.", "shortage": self.get_serializer(shortage).data})


class ReplenishmentRequestViewSet(ReadOnlyModelViewSet):
    queryset = ReplenishmentRequest.objects.select_related(
        "branch",
        "product",
        "picking_shortage",
        "picking_shortage__cart",
        "picking_shortage__reported_location",
        "picking_task",
        "picking_task__order_line__order",
        "picking_task__source_location",
        "created_by",
        "ordered_by",
    )
    serializer_class = ReplenishmentRequestSerializer
    filterset_class = ReplenishmentRequestFilter
    search_fields = [
        "reference",
        "customer_alias",
        "product__sku",
        "order_reference",
        "picking_shortage__cart__code",
        "picking_shortage__reported_by_worker_code",
        "created_by__username",
    ]
    ordering_fields = ["created_at", "status", "quantity", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")

    @action(detail=True, methods=["post"], url_path="mark-ordered-manually")
    def mark_ordered_manually(self, request, pk=None):
        with transaction.atomic():
            replenishment = self.get_queryset().select_for_update().get(pk=pk)
            require_branch_access(request.user, replenishment.branch, leader_required=True)
            if replenishment.status != ReplenishmentRequest.Status.PENDING_ORDER:
                return Response({"detail": "Only pending requests can be marked as ordered."}, status=status.HTTP_400_BAD_REQUEST)
            replenishment.status = ReplenishmentRequest.Status.ORDERED_MANUALLY
            replenishment.external_reference = str(request.data.get("external_reference", "")).strip()
            replenishment.note = str(request.data.get("note", "")).strip()
            replenishment.ordered_at = timezone.now()
            replenishment.ordered_by = request.user if request.user.is_authenticated else None
            replenishment.ordered_by_worker_code = str(request.data.get("worker_code", "")).strip()
            replenishment.save(update_fields=[
                "status",
                "external_reference",
                "note",
                "ordered_at",
                "ordered_by",
                "ordered_by_worker_code",
                "updated_at",
            ])
            AuditLog.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="replenishment_ordered",
                branch=replenishment.branch,
                product=replenishment.product,
                quantity=replenishment.quantity,
                order=(
                    replenishment.picking_shortage.order
                    if replenishment.picking_shortage_id
                    else replenishment.picking_task.order_line.order
                    if replenishment.picking_task_id
                    else None
                ),
                cart=replenishment.picking_shortage.cart if replenishment.picking_shortage_id else None,
                reference=replenishment.reference,
                entity_name="ReplenishmentRequest",
                entity_id=str(replenishment.id),
                message=f"Worker {request.user.username} marked replenishment request {replenishment.reference} as ordered manually.",
            )
        return Response({"message": "Replenishment request marked as ordered manually.", "request": self.get_serializer(replenishment).data})


class StockMovementFilter(django_filters.FilterSet):
    adjustment_direction = django_filters.CharFilter(method="filter_adjustment_direction")
    branch = django_filters.CharFilter(method="filter_branch")
    date_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    destination_location = django_filters.CharFilter(field_name="destination_location__code", lookup_expr="iexact")
    internal_transfer = django_filters.BooleanFilter(method="filter_internal_transfer")
    location = django_filters.CharFilter(method="filter_location")
    performed_by = django_filters.CharFilter(method="filter_performed_by")
    product = django_filters.CharFilter(method="filter_product")
    source_location = django_filters.CharFilter(field_name="source_location__code", lookup_expr="iexact")

    class Meta:
        model = StockMovement
        fields = [
            "branch",
            "product",
            "movement_type",
            "adjustment_direction",
            "source_location",
            "destination_location",
            "location",
            "internal_transfer",
            "date_from",
            "date_to",
            "performed_by",
        ]

    def filter_adjustment_direction(self, queryset, name, value):
        normalized = str(value).strip().lower()
        if normalized == "increase":
            return queryset.filter(destination_location__isnull=False, source_location__isnull=True)
        if normalized == "decrease":
            return queryset.filter(source_location__isnull=False, destination_location__isnull=True)
        if normalized == "unknown":
            return queryset.filter(
                models.Q(source_location__isnull=True, destination_location__isnull=True)
                | models.Q(source_location__isnull=False, destination_location__isnull=False)
            )
        return queryset

    def filter_branch(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(branch_id=value)
        return queryset.filter(branch__code__iexact=value)

    def filter_location(self, queryset, name, value):
        return queryset.filter(
            models.Q(source_location__code__iexact=value)
            | models.Q(destination_location__code__iexact=value)
        )

    def filter_performed_by(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(performed_by_id=value)
        return queryset.filter(performed_by__username__iexact=value)

    def filter_product(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(product_id=value)
        return queryset.filter(
            models.Q(product__sku__iexact=value)
            | models.Q(product__barcode__iexact=value)
            | models.Q(product__name__icontains=value)
        )

    def filter_internal_transfer(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            movement_type=StockMovement.MovementType.TRANSFER,
            source_location__isnull=False,
            destination_location__isnull=False,
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
    permission_classes = [IsAuthenticated]
    filterset_class = StockMovementFilter
    search_fields = [
        "product__sku",
        "product__name",
        "reference",
        "branch__code",
        "source_location__code",
        "destination_location__code",
        "performed_by__username",
    ]
    ordering = ["-created_at"]
    ordering_fields = ["movement_type", "quantity", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")


class StockAdjustmentViewSet(StockMovementViewSet):
    def get_queryset(self):
        return super().get_queryset().filter(movement_type=StockMovement.MovementType.ADJUSTMENT)


class AuditLogViewSet(ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related(
        "actor",
        "branch",
        "product",
        "source_location",
        "destination_location",
        "cart",
        "order",
        "route_run",
        "route_run__route",
        "transfer",
        "pallet",
        "discrepancy",
    )
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    search_fields = ["entity_name", "entity_id", "message", "actor__username"]
    ordering_fields = ["action_type", "entity_name", "created_at"]

    def _event_visible_for_branch(self, event, branch_code: str) -> bool:
        branch_code = branch_code.lower()
        if event.branch_id:
            return event.branch.code.lower() == branch_code
        entity_id = event.entity_id
        if not entity_id:
            return False
        try:
            if event.entity_name == "TransferDiscrepancy":
                discrepancy = TransferDiscrepancy.objects.select_related("transfer__destination_branch").get(pk=entity_id)
                return discrepancy.transfer.destination_branch.code.lower() == branch_code
            if event.entity_name == "TransferDiscrepancySourceReview":
                review = TransferDiscrepancySourceReview.objects.select_related("source_branch").get(pk=entity_id)
                return review.source_branch.code.lower() == branch_code
            if event.entity_name == "TransferDiscrepancySourceStockVerification":
                verification = TransferDiscrepancySourceStockVerification.objects.select_related(
                    "reconciliation__discrepancy__transfer__source_branch"
                ).get(pk=entity_id)
                return verification.reconciliation.discrepancy.transfer.source_branch.code.lower() == branch_code
            if event.entity_name in ["TransferDiscrepancyReconciliation", "TransferDiscrepancyTransitInvestigation"]:
                if event.entity_name == "TransferDiscrepancyReconciliation":
                    reconciliation = TransferDiscrepancyReconciliation.objects.select_related(
                        "discrepancy__transfer__source_branch",
                        "discrepancy__transfer__destination_branch",
                    ).get(pk=entity_id)
                else:
                    investigation = TransferDiscrepancyTransitInvestigation.objects.select_related(
                        "reconciliation__discrepancy__transfer__source_branch",
                        "reconciliation__discrepancy__transfer__destination_branch",
                    ).get(pk=entity_id)
                    reconciliation = investigation.reconciliation
                return branch_code in [
                    reconciliation.discrepancy.transfer.source_branch.code.lower(),
                    reconciliation.discrepancy.transfer.destination_branch.code.lower(),
                ]
            if event.entity_name == "TransferPallet":
                pallet = TransferPallet.objects.select_related("transfer__destination_branch").get(pk=entity_id)
                return pallet.transfer.destination_branch.code.lower() == branch_code
            if event.entity_name == "PalletReceivingScan":
                scan = PalletReceivingScan.objects.select_related("pallet__transfer__destination_branch").get(pk=entity_id)
                return scan.pallet.transfer.destination_branch.code.lower() == branch_code
            if event.entity_name == "InterBranchTransfer":
                transfer = InterBranchTransfer.objects.select_related("source_branch", "destination_branch").get(pk=entity_id)
                return branch_code in [transfer.source_branch.code.lower(), transfer.destination_branch.code.lower()]
            if event.entity_name == "RouteRun":
                route_run = RouteRun.objects.select_related("route__branch").get(pk=entity_id)
                return route_run.route.branch.code.lower() == branch_code
            if event.entity_name == "PickingTask":
                task = PickingTask.objects.select_related("branch").get(pk=entity_id)
                return task.branch.code.lower() == branch_code
            if event.entity_name == "StockMovement":
                movement = StockMovement.objects.select_related("branch").get(pk=entity_id)
                return movement.branch.code.lower() == branch_code
        except (ValueError, TransferDiscrepancy.DoesNotExist, TransferDiscrepancySourceReview.DoesNotExist,
                TransferDiscrepancySourceStockVerification.DoesNotExist, TransferDiscrepancyReconciliation.DoesNotExist,
                TransferDiscrepancyTransitInvestigation.DoesNotExist, TransferPallet.DoesNotExist,
                PalletReceivingScan.DoesNotExist, InterBranchTransfer.DoesNotExist, RouteRun.DoesNotExist,
                PickingTask.DoesNotExist, StockMovement.DoesNotExist):
            return False
        return False

    def _apply_event_search(self, queryset, request):
        search = request.query_params.get("search", "").strip()
        if not search:
            return queryset
        return queryset.filter(
            models.Q(message__icontains=search)
            | models.Q(entity_name__icontains=search)
            | models.Q(entity_id__icontains=search)
            | models.Q(reference__icontains=search)
            | models.Q(actor__username__icontains=search)
            | models.Q(product__sku__icontains=search)
            | models.Q(product__name__icontains=search)
            | models.Q(source_location__code__icontains=search)
            | models.Q(destination_location__code__icontains=search)
            | models.Q(source_label__icontains=search)
            | models.Q(destination_label__icontains=search)
            | models.Q(cart__code__icontains=search)
            | models.Q(order__external_reference__icontains=search)
            | models.Q(route_run__route__code__icontains=search)
            | models.Q(route_run__route__name__icontains=search)
            | models.Q(transfer__reference__icontains=search)
            | models.Q(pallet__scan_code__icontains=search)
            | models.Q(discrepancy__reference__icontains=search)
            | models.Q(result__icontains=search)
        )

    def _apply_branch_visibility(self, queryset, request):
        branch = request.query_params.get("branch", "").strip()
        if request.user.is_authenticated:
            if branch:
                branch_codes_filter(request.user, branch)
            else:
                allowed = {code.lower() for code in branch_codes_filter(request.user)}
                return [event for event in queryset if any(self._event_visible_for_branch(event, code) for code in allowed)]
        if not branch:
            return queryset
        return [event for event in queryset if self._event_visible_for_branch(event, branch)]

    @action(detail=False, methods=["get"])
    def current(self, request):
        since = timezone.now() - timezone.timedelta(days=30)
        queryset = self.filter_queryset(self.get_queryset().filter(created_at__gte=since).order_by("-created_at"))
        queryset = self._apply_event_search(queryset, request)
        queryset = self._apply_branch_visibility(queryset, request)
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
        queryset = self._apply_event_search(queryset, request)
        queryset = self._apply_branch_visibility(queryset, request)
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class TransferDiscrepancyActionViewSet(ViewSet):
    serializer_class = TransferDiscrepancyActionSerializer
    pagination_class = TransferDiscrepancyActionPagination

    def list(self, request):
        rows = build_transfer_discrepancy_action_queue()
        action_type = request.query_params.get("action_type", "").strip()
        branch = request.query_params.get("branch", "").strip().lower()
        search = request.query_params.get("search", "").strip().lower()

        if request.user.is_authenticated:
            rows = filter_rows_for_user(rows, request.user, branch)
            branch = ""

        if action_type:
            rows = [row for row in rows if row["action_type"] == action_type]
        if branch:
            rows = [
                row
                for row in rows
                if branch in [code.lower() for code in row.get("visible_branches", [])]
            ]
        if search:
            searchable_keys = [
                "target_reference",
                "discrepancy_reference",
                "transfer_reference",
                "pallet_reference",
                "source_branch",
                "destination_branch",
            ]
            rows = [
                row
                for row in rows
                if any(search in str(row.get(key, "")).lower() for key in searchable_keys)
            ]

        rows = sorted(rows, key=lambda row: row["waiting_since"] or row["created_at"])
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request)
        serializer = self.serializer_class(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class TransferDiscrepancyViewSet(ReadOnlyModelViewSet):
    queryset = TransferDiscrepancy.objects.select_related(
        "pallet",
        "transfer",
        "transfer__source_branch",
        "transfer__destination_branch",
    ).prefetch_related(
        "items",
        "items__product",
        "recoveries",
        "recoveries__product",
        "shortage_confirmations",
        "shortage_confirmations__product",
        "source_review",
        "reconciliation",
    )
    serializer_class = TransferDiscrepancySerializer
    filterset_fields = ["status", "pallet", "transfer"]
    search_fields = ["reference", "pallet__scan_code", "transfer__reference"]
    ordering_fields = ["reference", "status", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_authenticated:
            queryset = filter_dual_branch_queryset(
                queryset,
                self.request,
                "transfer__source_branch",
                "transfer__destination_branch",
            )
        branch = self.request.query_params.get("branch", "").strip()
        if branch:
            queryset = queryset.filter(transfer__destination_branch__code__iexact=branch)
        return queryset

    def _recovery_response(self, recovery: TransferDiscrepancyRecovery):
        discrepancy = recovery.discrepancy
        item = recovery.discrepancy_item
        totals = get_discrepancy_investigation_totals(discrepancy)
        return {
            "discrepancy_reference": discrepancy.reference,
            "status": discrepancy.status,
            "product_code": recovery.product.sku,
            "recovered_quantity": str(recovery.quantity),
            "line_recovered_quantity": str(item.recovered_quantity),
            "line_confirmed_shortage_quantity": str(item.confirmed_shortage_quantity),
            "line_remaining_quantity": str(discrepancy_line_remaining(item)),
            "total_remaining_quantity": str(totals["remaining"]),
            "destination_location_code": recovery.destination_location.code,
            "recovery_id": recovery.id,
        }

    def _shortage_confirmation_response(self, confirmation: TransferDiscrepancyShortageConfirmation):
        discrepancy = confirmation.discrepancy
        item = confirmation.discrepancy_item
        totals = get_discrepancy_investigation_totals(discrepancy)
        return {
            "discrepancy_reference": discrepancy.reference,
            "status": discrepancy.status,
            "product_code": confirmation.product.sku,
            "confirmed_quantity": str(confirmation.quantity),
            "line_recovered_quantity": str(item.recovered_quantity),
            "line_confirmed_shortage_quantity": str(item.confirmed_shortage_quantity),
            "line_remaining_quantity": str(discrepancy_line_remaining(item)),
            "total_recovered_quantity": str(totals["recovered"]),
            "total_confirmed_shortage_quantity": str(totals["confirmed_shortage"]),
            "total_remaining_quantity": str(totals["remaining"]),
            "unconfirmed_location_code": confirmation.unconfirmed_location.code,
            "confirmation_id": confirmation.id,
        }

    @action(detail=True, methods=["post"], url_path="print-report")
    def print_report(self, request, pk=None):
        printer_code = str(request.data.get("printer_code", "")).strip()
        worker_code = actor_code(request)
        if not printer_code:
            return Response({"detail": "printer_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            discrepancy = (
                TransferDiscrepancy.objects.select_for_update()
                .select_related("pallet", "transfer", "transfer__destination_branch")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(request.user, discrepancy.transfer.destination_branch)
            items = list(
                discrepancy.items.select_for_update()
                .select_related("product")
                .filter(discrepancy_type="shortage")
                .order_by("product__sku")
            )
            first_print = discrepancy.report_printed_at is None
            posted_quantity = Decimal("0")

            if first_print:
                try:
                    unconfirmed_location = get_discrepancy_location(discrepancy.transfer.destination_branch)
                except DiscrepancyLocationMissing as error:
                    return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

                now = timezone.now()
                for item in items:
                    unposted = item.discrepancy_quantity - item.posted_to_unconfirmed_quantity
                    if unposted <= 0:
                        continue

                    inventory_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                        branch=discrepancy.transfer.destination_branch,
                        location=unconfirmed_location,
                        product=item.product,
                        defaults={"quantity_on_hand": 0, "quantity_reserved": 0},
                    )
                    inventory_item.quantity_on_hand = F("quantity_on_hand") + unposted
                    inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

                    item.posted_to_unconfirmed_quantity = F("posted_to_unconfirmed_quantity") + unposted
                    item.posted_to_unconfirmed_at = now
                    item.save(update_fields=["posted_to_unconfirmed_quantity", "posted_to_unconfirmed_at", "updated_at"])

                    StockMovement.objects.create(
                        branch=discrepancy.transfer.destination_branch,
                        product=item.product,
                        inventory_item=inventory_item,
                        destination_location=unconfirmed_location,
                        movement_type=StockMovement.MovementType.RECEIVING_DISCREPANCY,
                        quantity=unposted,
                        reference=discrepancy.reference,
                        performed_by=audit_actor(request),
                    )
                    posted_quantity += unposted

                discrepancy.report_printed_at = now
                discrepancy.shortage_posted_at = now
                discrepancy.status = TransferDiscrepancy.Status.INVESTIGATING
                AuditLog.objects.create(
                    actor=audit_actor(request),
                    action_type=AuditLog.ActionType.UPDATE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=f"Worker {worker_code} printed discrepancy report {discrepancy.reference} on printer {printer_code}.",
                )
                if posted_quantity:
                    posted_quantity_label = format_piece_quantity(posted_quantity)
                    missing_unit = unit_label(posted_quantity)
                    AuditLog.objects.create(
                        action_type=AuditLog.ActionType.UPDATE,
                        entity_name="TransferDiscrepancy",
                        entity_id=str(discrepancy.id),
                        message=(
                            f"{posted_quantity_label} missing {missing_unit} from discrepancy {discrepancy.reference} "
                            f"posted to location {unconfirmed_location.code}."
                        ),
                    )
            else:
                AuditLog.objects.create(
                    actor=audit_actor(request),
                    action_type=AuditLog.ActionType.UPDATE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=f"Worker {worker_code} reprinted discrepancy report {discrepancy.reference} on printer {printer_code}.",
                )

            discrepancy.report_print_count = F("report_print_count") + 1
            discrepancy.last_report_printer_code = printer_code
            discrepancy.save(
                update_fields=[
                    "report_printed_at",
                    "report_print_count",
                    "last_report_printer_code",
                    "shortage_posted_at",
                    "status",
                    "updated_at",
                ]
            )
            discrepancy.refresh_from_db()

        serializer = self.get_serializer(discrepancy)
        return Response(
            {
                "message": "Discrepancy report printed." if first_print else "Discrepancy report reprinted.",
                "first_print": first_print,
                "posted_quantity": str(posted_quantity),
                "discrepancy": serializer.data,
            }
        )

    @action(detail=True, methods=["post"], url_path="recover-item")
    def recover_item(self, request, pk=None):
        product_code = str(request.data.get("product_code", "")).strip()
        destination_location_code = str(request.data.get("destination_location_code", "")).strip()
        worker_code = actor_code(request)
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()
        raw_quantity = str(request.data.get("quantity", "1")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        existing = (
            TransferDiscrepancyRecovery.objects.select_related(
                "discrepancy",
                "discrepancy_item",
                "product",
                "destination_location",
            )
            .filter(client_operation_id=client_operation_id)
            .first()
        )
        if existing is not None:
            return Response({"message": "Recovery already recorded.", "recovery": self._recovery_response(existing)})

        if not product_code:
            return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not destination_location_code:
            return Response({"detail": "destination_location_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not raw_quantity.isdigit():
            return Response({"detail": "Quantity must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)
        quantity = Decimal(raw_quantity)
        if quantity <= 0:
            return Response({"detail": "Quantity must be at least 1."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            discrepancy = (
                TransferDiscrepancy.objects.select_for_update()
                .select_related("transfer", "transfer__destination_branch")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(request.user, discrepancy.transfer.destination_branch)
            if discrepancy.status != TransferDiscrepancy.Status.INVESTIGATING:
                return Response(
                    {"detail": "Recovery is allowed only for investigating discrepancies."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if discrepancy.report_printed_at is None or discrepancy.shortage_posted_at is None:
                return Response(
                    {"detail": "Discrepancy report must be printed and posted before recovery."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            product = Product.objects.filter(models.Q(sku__iexact=product_code) | models.Q(barcode__iexact=product_code)).first()
            if product is None:
                return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

            item = (
                discrepancy.items.select_for_update()
                .select_related("product")
                .filter(product=product, discrepancy_type="shortage")
                .first()
            )
            if item is None:
                return Response({"detail": "This product is not part of the discrepancy."}, status=status.HTTP_400_BAD_REQUEST)
            remaining = discrepancy_line_remaining(item)
            if remaining <= 0:
                return Response(
                    {"detail": "This discrepancy line has no remaining quantity under investigation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if quantity > remaining:
                return Response(
                    {"detail": "Recovery quantity exceeds the remaining discrepancy quantity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                unconfirmed_location = get_discrepancy_location(discrepancy.transfer.destination_branch)
            except DiscrepancyLocationMissing as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

            destination_location = (
                Location.objects.select_related("branch")
                .filter(branch=discrepancy.transfer.destination_branch, code__iexact=destination_location_code)
                .first()
            )
            if destination_location is None:
                if Location.objects.filter(code__iexact=destination_location_code).exists():
                    return Response(
                        {"detail": "Destination location belongs to another branch."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {"detail": "Destination location not found in destination branch."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if destination_location.id == unconfirmed_location.id:
                return Response({"detail": "UNCONFIRMED cannot be the recovery destination."}, status=status.HTTP_400_BAD_REQUEST)

            source_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=discrepancy.transfer.destination_branch, location=unconfirmed_location, product=product)
                .first()
            )
            if source_item is None or source_item.quantity_on_hand < quantity:
                return Response(
                    {"detail": "UNCONFIRMED inventory is insufficient for this recovery."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            destination_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=discrepancy.transfer.destination_branch,
                location=destination_location,
                product=product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            source_item.quantity_on_hand = F("quantity_on_hand") - quantity
            source_item.save(update_fields=["quantity_on_hand", "updated_at"])
            destination_item.quantity_on_hand = F("quantity_on_hand") + quantity
            destination_item.save(update_fields=["quantity_on_hand", "updated_at"])

            movement = StockMovement.objects.create(
                branch=discrepancy.transfer.destination_branch,
                product=product,
                inventory_item=destination_item,
                source_location=unconfirmed_location,
                destination_location=destination_location,
                movement_type=StockMovement.MovementType.DISCREPANCY_RECOVERY,
                quantity=quantity,
                reference=discrepancy.reference,
                performed_by=audit_actor(request),
            )
            recovery = TransferDiscrepancyRecovery.objects.create(
                discrepancy=discrepancy,
                discrepancy_item=item,
                product=product,
                quantity=quantity,
                source_location=unconfirmed_location,
                destination_location=destination_location,
                worker_code=worker_code,
                client_operation_id=client_operation_id,
                stock_movement=movement,
            )
            item.recovered_quantity = F("recovered_quantity") + quantity
            item.last_recovered_at = timezone.now()
            item.save(update_fields=["recovered_quantity", "last_recovered_at", "updated_at"])
            item.refresh_from_db()

            quantity_label = format_piece_quantity(quantity)
            unit_word = unit_label(quantity)
            move_word = "it" if quantity == 1 else "them"
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancy",
                entity_id=str(discrepancy.id),
                message=(
                    f"Worker {worker_code} recovered {quantity_label} {unit_word} of {product.sku} from discrepancy "
                    f"{discrepancy.reference} and moved {move_word} from {unconfirmed_location.code} to {destination_location.code}."
                ),
            )

            finalized, final_status = finalize_discrepancy_if_complete(discrepancy, worker_code)
            if finalized and final_status == TransferDiscrepancy.Status.RESOLVED:
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.STATUS_CHANGE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=f"Discrepancy {discrepancy.reference} was resolved after all missing units were recovered.",
                )
            elif finalized and final_status == TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
                totals = get_discrepancy_investigation_totals(discrepancy)
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.STATUS_CHANGE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=(
                        f"Discrepancy {discrepancy.reference} was closed with confirmed shortage: "
                        f"{format_piece_quantity(totals['recovered'])} recovered, "
                        f"{format_piece_quantity(totals['confirmed_shortage'])} confirmed missing."
                    ),
                )

            recovery.refresh_from_db()

        return Response({"message": "Recovered item recorded.", "recovery": self._recovery_response(recovery)})

    @action(detail=True, methods=["post"], url_path="confirm-shortage")
    def confirm_shortage(self, request, pk=None):
        product_code = str(request.data.get("product_code", "")).strip()
        worker_code = actor_code(request)
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()
        raw_quantity = str(request.data.get("quantity", "1")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        existing = (
            TransferDiscrepancyShortageConfirmation.objects.select_related(
                "discrepancy",
                "discrepancy_item",
                "product",
                "unconfirmed_location",
            )
            .filter(client_operation_id=client_operation_id)
            .first()
        )
        if existing is not None:
            return Response(
                {
                    "message": "Shortage confirmation already recorded.",
                    "confirmation": self._shortage_confirmation_response(existing),
                }
            )

        if not product_code:
            return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not raw_quantity.isdigit():
            return Response({"detail": "Quantity must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)
        quantity = Decimal(raw_quantity)
        if quantity <= 0:
            return Response({"detail": "Quantity must be at least 1."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            discrepancy = (
                TransferDiscrepancy.objects.select_for_update()
                .select_related("transfer", "transfer__destination_branch")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(
                    request.user,
                    discrepancy.transfer.destination_branch,
                    leader_required=True,
                )
                if discrepancy.created_by_worker_code == request.user.username:
                    return Response(
                        {"detail": "A different leader must confirm the destination shortage."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            if discrepancy.status != TransferDiscrepancy.Status.INVESTIGATING:
                return Response(
                    {"detail": "Shortage confirmation is allowed only for investigating discrepancies."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if discrepancy.report_printed_at is None or discrepancy.shortage_posted_at is None:
                return Response(
                    {"detail": "Discrepancy report must be printed and posted before confirming shortage."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            product = Product.objects.filter(models.Q(sku__iexact=product_code) | models.Q(barcode__iexact=product_code)).first()
            if product is None:
                return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

            item = (
                discrepancy.items.select_for_update()
                .select_related("product")
                .filter(product=product, discrepancy_type="shortage")
                .first()
            )
            if item is None:
                return Response({"detail": "This product is not part of the discrepancy."}, status=status.HTTP_400_BAD_REQUEST)

            remaining = discrepancy_line_remaining(item)
            if remaining <= 0:
                return Response(
                    {"detail": "This discrepancy line has no remaining quantity under investigation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if quantity > remaining:
                return Response(
                    {"detail": "Confirmed shortage quantity exceeds the remaining discrepancy quantity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                unconfirmed_location = get_discrepancy_location(discrepancy.transfer.destination_branch)
            except DiscrepancyLocationMissing as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

            source_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=discrepancy.transfer.destination_branch, location=unconfirmed_location, product=product)
                .first()
            )
            if source_item is None or source_item.quantity_on_hand < quantity:
                return Response(
                    {"detail": "UNCONFIRMED inventory is insufficient for this shortage confirmation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            source_item.quantity_on_hand = F("quantity_on_hand") - quantity
            source_item.save(update_fields=["quantity_on_hand", "updated_at"])

            movement = StockMovement.objects.create(
                branch=discrepancy.transfer.destination_branch,
                product=product,
                inventory_item=source_item,
                source_location=unconfirmed_location,
                movement_type=StockMovement.MovementType.DISCREPANCY_SHORTAGE,
                quantity=quantity,
                reference=discrepancy.reference,
                performed_by=audit_actor(request),
            )
            confirmation = TransferDiscrepancyShortageConfirmation.objects.create(
                discrepancy=discrepancy,
                discrepancy_item=item,
                product=product,
                quantity=quantity,
                unconfirmed_location=unconfirmed_location,
                worker_code=worker_code,
                client_operation_id=client_operation_id,
                stock_movement=movement,
            )
            item.confirmed_shortage_quantity = F("confirmed_shortage_quantity") + quantity
            item.last_confirmed_shortage_at = timezone.now()
            item.save(update_fields=["confirmed_shortage_quantity", "last_confirmed_shortage_at", "updated_at"])
            item.refresh_from_db()

            quantity_label = format_piece_quantity(quantity)
            unit_word = unit_label(quantity)
            remove_word = "it" if quantity == 1 else "them"
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancy",
                entity_id=str(discrepancy.id),
                message=(
                    f"Worker {worker_code} confirmed {quantity_label} {unit_word} of {product.sku} as missing for "
                    f"discrepancy {discrepancy.reference} and removed {remove_word} from {unconfirmed_location.code}."
                ),
            )

            finalized, final_status = finalize_discrepancy_if_complete(discrepancy, worker_code)
            if finalized and final_status == TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
                totals = get_discrepancy_investigation_totals(discrepancy)
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.STATUS_CHANGE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=(
                        f"Discrepancy {discrepancy.reference} was closed with confirmed shortage: "
                        f"{format_piece_quantity(totals['recovered'])} recovered, "
                        f"{format_piece_quantity(totals['confirmed_shortage'])} confirmed missing."
                    ),
                )

            confirmation.refresh_from_db()

        return Response(
            {"message": "Shortage confirmation recorded.", "confirmation": self._shortage_confirmation_response(confirmation)}
        )


class TransferDiscrepancySourceReviewFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    branch = django_filters.CharFilter(field_name="source_branch__code", lookup_expr="iexact")

    class Meta:
        model = TransferDiscrepancySourceReview
        fields = ["status", "source_branch", "branch", "discrepancy"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(reference__icontains=value)
            | models.Q(discrepancy__reference__icontains=value)
            | models.Q(discrepancy__pallet__scan_code__icontains=value)
            | models.Q(discrepancy__transfer__reference__icontains=value)
        )


class TransferDiscrepancySourceReviewViewSet(ReadOnlyModelViewSet):
    queryset = TransferDiscrepancySourceReview.objects.select_related(
        "discrepancy",
        "discrepancy__pallet",
        "discrepancy__transfer",
        "discrepancy__transfer__source_branch",
        "discrepancy__transfer__destination_branch",
        "source_branch",
    ).prefetch_related(
        "discrepancy__items",
        "discrepancy__items__product",
        "discrepancy__pallet__items",
        "discrepancy__pallet__items__product",
        "discrepancy__pallet__receiving_scans",
        "discrepancy__recoveries",
        "discrepancy__shortage_confirmations",
        "reconciliation",
    )
    serializer_class = TransferDiscrepancySourceReviewSerializer
    filterset_class = TransferDiscrepancySourceReviewFilter
    search_fields = [
        "reference",
        "discrepancy__reference",
        "discrepancy__pallet__scan_code",
        "discrepancy__transfer__reference",
    ]
    ordering_fields = ["reference", "status", "created_at", "updated_at", "completed_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "source_branch")

    def _validate_confirmed_shortage_discrepancy(self, review):
        if review.discrepancy.status != TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
            return Response(
                {"detail": "Source review requires a final confirmed-shortage discrepancy."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @action(detail=True, methods=["post"], url_path="begin")
    def begin(self, request, pk=None):
        worker_code = actor_code(request)
        with transaction.atomic():
            review = (
                TransferDiscrepancySourceReview.objects.select_for_update()
                .select_related("discrepancy")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(request.user, review.source_branch)
            validation = self._validate_confirmed_shortage_discrepancy(review)
            if validation is not None:
                return validation
            if review.status == TransferDiscrepancySourceReview.Status.COMPLETED:
                return Response({"detail": "This source review has already been completed."}, status=status.HTTP_400_BAD_REQUEST)
            if review.status == TransferDiscrepancySourceReview.Status.INVESTIGATING:
                return Response({"message": "Source review already started.", "source_review": self.get_serializer(review).data})

            review.status = TransferDiscrepancySourceReview.Status.INVESTIGATING
            review.started_at = timezone.now()
            review.started_by_worker_code = worker_code
            review.save(update_fields=["status", "started_at", "started_by_worker_code", "updated_at"])
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancySourceReview",
                entity_id=str(review.id),
                message=f"Worker {worker_code} began source review {review.reference} for discrepancy {review.discrepancy.reference}.",
            )

        return Response({"message": "Source review started.", "source_review": self.get_serializer(review).data})

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        finding = str(request.data.get("finding", "")).strip()
        finding_note = str(request.data.get("finding_note", "")).strip()
        worker_code = actor_code(request)
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        existing = TransferDiscrepancySourceReview.objects.filter(client_operation_id=client_operation_id).first()
        if existing is not None:
            reconciliation = getattr(existing, "reconciliation", None)
            return Response(
                {
                    "message": "Source review already completed.",
                    "source_review": self.get_serializer(existing).data,
                    "reconciliation_id": reconciliation.id if reconciliation else None,
                }
            )
        if finding not in TransferDiscrepancySourceReview.Finding.values:
            return Response({"detail": "Invalid source review finding."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            review = (
                TransferDiscrepancySourceReview.objects.select_for_update()
                .select_related("discrepancy")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(request.user, review.source_branch)
            validation = self._validate_confirmed_shortage_discrepancy(review)
            if validation is not None:
                return validation
            if review.status == TransferDiscrepancySourceReview.Status.PENDING_REVIEW:
                return Response({"detail": "Begin the source review before completing it."}, status=status.HTTP_400_BAD_REQUEST)
            if review.status == TransferDiscrepancySourceReview.Status.COMPLETED:
                return Response({"detail": "This source review has already been completed."}, status=status.HTTP_400_BAD_REQUEST)

            review.status = TransferDiscrepancySourceReview.Status.COMPLETED
            review.finding = finding
            review.finding_note = finding_note
            review.completed_at = timezone.now()
            review.completed_by_worker_code = worker_code
            review.client_operation_id = client_operation_id
            review.save(
                update_fields=[
                    "status",
                    "finding",
                    "finding_note",
                    "completed_at",
                    "completed_by_worker_code",
                    "client_operation_id",
                    "updated_at",
                ]
            )
            reconciliation, _ = ensure_reconciliation_for_source_review(review)
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancySourceReview",
                entity_id=str(review.id),
                message=(
                    f"Worker {worker_code} completed source review {review.reference} with finding: "
                    f"{review.get_finding_display()}."
                ),
            )

        return Response(
            {
                "message": "Source review completed.",
                "source_review": self.get_serializer(review).data,
                "reconciliation_id": reconciliation.id,
            }
        )


class TransferDiscrepancyReconciliationFilter(django_filters.FilterSet):
    source_branch = django_filters.NumberFilter(field_name="discrepancy__transfer__source_branch_id")
    destination_branch = django_filters.NumberFilter(field_name="discrepancy__transfer__destination_branch_id")
    branch = django_filters.CharFilter(method="filter_branch")
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = TransferDiscrepancyReconciliation
        fields = ["status", "route", "source_branch", "destination_branch", "branch"]

    def filter_branch(self, queryset, name, value):
        return queryset.filter(
            models.Q(discrepancy__transfer__source_branch__code__iexact=value)
            | models.Q(discrepancy__transfer__destination_branch__code__iexact=value)
        )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(reference__icontains=value)
            | models.Q(source_review__reference__icontains=value)
            | models.Q(discrepancy__reference__icontains=value)
            | models.Q(discrepancy__pallet__scan_code__icontains=value)
            | models.Q(discrepancy__transfer__reference__icontains=value)
        )


class TransferDiscrepancyReconciliationViewSet(ReadOnlyModelViewSet):
    queryset = TransferDiscrepancyReconciliation.objects.select_related(
        "discrepancy",
        "discrepancy__pallet",
        "discrepancy__transfer",
        "discrepancy__transfer__source_branch",
        "discrepancy__transfer__destination_branch",
        "source_review",
        "manual_decision",
        "source_stock_verification",
    ).prefetch_related("discrepancy__items", "discrepancy__items__product")
    serializer_class = TransferDiscrepancyReconciliationSerializer
    filterset_class = TransferDiscrepancyReconciliationFilter
    search_fields = [
        "reference",
        "source_review__reference",
        "discrepancy__reference",
        "discrepancy__pallet__scan_code",
        "discrepancy__transfer__reference",
    ]
    ordering_fields = ["reference", "route", "status", "created_at", "updated_at", "acknowledged_at"]

    def get_queryset(self):
        return filter_dual_branch_queryset(
            super().get_queryset(),
            self.request,
            "discrepancy__transfer__source_branch",
            "discrepancy__transfer__destination_branch",
        )

    @action(detail=True, methods=["post"], url_path="acknowledge")
    def acknowledge(self, request, pk=None):
        worker_code = actor_code(request)
        with transaction.atomic():
            reconciliation = (
                TransferDiscrepancyReconciliation.objects.select_for_update()
                .select_related("discrepancy", "source_review")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_any_branch_access(
                    request.user,
                    [
                        reconciliation.discrepancy.transfer.source_branch,
                        reconciliation.discrepancy.transfer.destination_branch,
                    ],
                )
            if reconciliation.source_review.status != TransferDiscrepancySourceReview.Status.COMPLETED:
                return Response(
                    {"detail": "Reconciliation requires a completed source review."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if reconciliation.discrepancy.status != TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
                return Response(
                    {"detail": "Reconciliation requires a confirmed-shortage discrepancy."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if reconciliation.status == TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
                verification, verification_created = ensure_source_stock_verification_for_reconciliation(reconciliation)
                investigation, investigation_created = ensure_transit_investigation_for_reconciliation(reconciliation)
                return Response(
                    {
                        "message": "Reconciliation case already acknowledged.",
                        "reconciliation": self.get_serializer(reconciliation).data,
                        "source_stock_verification_id": verification.id if verification else None,
                        "source_stock_verification_created": verification_created if verification else False,
                        "transit_investigation_id": investigation.id if investigation else None,
                        "transit_investigation_created": investigation_created if investigation else False,
                    }
                )

            reconciliation.status = TransferDiscrepancyReconciliation.Status.IN_PROGRESS
            reconciliation.acknowledged_at = timezone.now()
            reconciliation.acknowledged_by_worker_code = worker_code
            reconciliation.save(
                update_fields=["status", "acknowledged_at", "acknowledged_by_worker_code", "updated_at"]
            )
            verification, verification_created = ensure_source_stock_verification_for_reconciliation(reconciliation)
            investigation, investigation_created = ensure_transit_investigation_for_reconciliation(reconciliation)
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancyReconciliation",
                entity_id=str(reconciliation.id),
                message=f"Worker {worker_code} acknowledged reconciliation case {reconciliation.reference}.",
            )

        return Response(
            {
                "message": "Reconciliation case acknowledged.",
                "reconciliation": self.get_serializer(reconciliation).data,
                "source_stock_verification_id": verification.id if verification else None,
                "source_stock_verification_created": verification_created if verification else False,
                "transit_investigation_id": investigation.id if investigation else None,
                "transit_investigation_created": investigation_created if investigation else False,
            }
        )

    def _manual_decision_response(self, decision: TransferDiscrepancyManualReconciliationDecision):
        reconciliation = decision.reconciliation
        return {
            "message": "Manual reconciliation decision recorded.",
            "reconciliation": self.get_serializer(reconciliation).data,
            "manual_decision": {
                "id": decision.id,
                "outcome": decision.outcome,
                "outcome_label": decision.get_outcome_display(),
                "decision_note": decision.decision_note,
                "decided_at": decision.decided_at.isoformat(),
                "decided_by_worker_code": decision.decided_by_worker_code,
            },
        }

    @action(detail=True, methods=["post"], url_path="complete-manual")
    def complete_manual(self, request, pk=None):
        outcome = str(request.data.get("outcome", "")).strip()
        decision_note = str(request.data.get("decision_note", "")).strip()
        worker_code = actor_code(request)
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        existing = (
            TransferDiscrepancyManualReconciliationDecision.objects.select_related(
                "reconciliation",
                "reconciliation__discrepancy",
                "reconciliation__discrepancy__pallet",
                "reconciliation__discrepancy__transfer",
                "reconciliation__discrepancy__transfer__source_branch",
                "reconciliation__discrepancy__transfer__destination_branch",
                "reconciliation__source_review",
            )
            .filter(reconciliation_id=pk, client_operation_id=client_operation_id)
            .first()
        )
        if existing is not None:
            return Response(self._manual_decision_response(existing))

        valid_outcomes = {choice.value for choice in TransferDiscrepancyManualReconciliationDecision.Outcome}
        if outcome not in valid_outcomes:
            return Response({"detail": "Invalid manual reconciliation outcome."}, status=status.HTTP_400_BAD_REQUEST)
        if not decision_note:
            return Response({"detail": "decision_note is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(decision_note) > 2000:
            return Response({"detail": "decision_note must be 2000 characters or fewer."}, status=status.HTTP_400_BAD_REQUEST)

        source_outcomes = {
            TransferDiscrepancyManualReconciliationDecision.Outcome.SOURCE_LOSS_CONFIRMED,
            TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED,
            TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
        }
        original_manual_outcomes = {
            TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED,
            TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
        }
        transit_outcomes = {
            TransferDiscrepancyManualReconciliationDecision.Outcome.TRANSIT_LOSS_CONFIRMED,
            TransferDiscrepancyManualReconciliationDecision.Outcome.UNRESOLVED_LOSS_CLOSED,
            TransferDiscrepancyManualReconciliationDecision.Outcome.ADMINISTRATIVE_ERROR,
        }

        with transaction.atomic():
            reconciliation = (
                TransferDiscrepancyReconciliation.objects.select_for_update()
                .select_related(
                    "discrepancy",
                    "discrepancy__pallet",
                    "discrepancy__transfer",
                    "discrepancy__transfer__source_branch",
                    "discrepancy__transfer__destination_branch",
                    "source_review",
                )
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_any_branch_access(
                    request.user,
                    [
                        reconciliation.discrepancy.transfer.source_branch,
                        reconciliation.discrepancy.transfer.destination_branch,
                    ],
                    leader_required=True,
                )
            if TransferDiscrepancyManualReconciliationDecision.objects.filter(
                reconciliation=reconciliation,
                client_operation_id=client_operation_id,
            ).exists():
                decision = TransferDiscrepancyManualReconciliationDecision.objects.select_related(
                    "reconciliation",
                    "reconciliation__discrepancy",
                    "reconciliation__discrepancy__pallet",
                    "reconciliation__discrepancy__transfer",
                    "reconciliation__discrepancy__transfer__source_branch",
                    "reconciliation__discrepancy__transfer__destination_branch",
                    "reconciliation__source_review",
                    "reconciliation__transit_investigation",
                ).get(reconciliation=reconciliation, client_operation_id=client_operation_id)
                return Response(self._manual_decision_response(decision))

            if reconciliation.status == TransferDiscrepancyReconciliation.Status.COMPLETED:
                return Response(
                    {"detail": "This reconciliation has already been completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if reconciliation.status == TransferDiscrepancyReconciliation.Status.PENDING_ACTION:
                return Response(
                    {"detail": "Reconciliation must be acknowledged before manual completion."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            verification = None
            if reconciliation.route == TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION:
                if outcome not in source_outcomes:
                    return Response(
                        {"detail": "This outcome is not allowed for source stock verification."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if reconciliation.status != TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED:
                    return Response(
                        {"detail": "Source verification reconciliation must require manual action."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                verification = (
                    TransferDiscrepancySourceStockVerification.objects.select_for_update()
                    .filter(reconciliation=reconciliation)
                    .first()
                )
                if verification is None:
                    return Response(
                        {"detail": "Source stock verification is required before manual completion."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if verification.status != TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED:
                    return Response(
                        {"detail": "Source stock verification must be completed with unresolved stock."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                list(verification.items.select_for_update())
                if get_source_verification_totals(verification)["unresolved"] <= 0:
                    return Response(
                        {"detail": "Source stock verification has no unresolved quantity."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if (
                    request.user.is_authenticated
                    and verification.search_completed_by_worker_code == request.user.username
                ):
                    return Response(
                        {"detail": "A different leader must record the final reconciliation outcome."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            elif reconciliation.route == TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION:
                if outcome not in transit_outcomes:
                    return Response(
                        {"detail": "This outcome is not allowed for transit investigation."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if reconciliation.status != TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED:
                    return Response(
                        {"detail": "Transit reconciliation must require manual action."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                investigation = (
                    TransferDiscrepancyTransitInvestigation.objects.select_for_update()
                    .filter(reconciliation=reconciliation)
                    .first()
                )
                if investigation is None:
                    return Response(
                        {"detail": "Completed transit investigation is required before manual completion."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if investigation.status != TransferDiscrepancyTransitInvestigation.Status.COMPLETED:
                    return Response(
                        {"detail": "Transit investigation must be completed before manual completion."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not investigation.finding or not investigation.finding_note.strip():
                    return Response(
                        {"detail": "Transit investigation finding and note are required before manual completion."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if investigation.reconciliation_id != reconciliation.id:
                    return Response(
                        {"detail": "Transit investigation does not match this reconciliation."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if (
                    request.user.is_authenticated
                    and investigation.completed_by_worker_code == request.user.username
                ):
                    return Response(
                        {"detail": "A different leader must record the final reconciliation outcome."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            elif reconciliation.route == TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION:
                if outcome not in original_manual_outcomes:
                    return Response(
                        {"detail": "This outcome is not allowed for manual reconciliation."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
                    return Response(
                        {"detail": "Manual reconciliation must be in progress before completion."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if (
                    request.user.is_authenticated
                    and reconciliation.source_review.completed_by_worker_code == request.user.username
                ):
                    return Response(
                        {"detail": "A different leader must record the final reconciliation outcome."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            else:
                return Response(
                    {"detail": "This reconciliation route cannot be manually completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            now = timezone.now()
            decision = TransferDiscrepancyManualReconciliationDecision.objects.create(
                reconciliation=reconciliation,
                outcome=outcome,
                decision_note=decision_note,
                decided_at=now,
                decided_by_worker_code=worker_code,
                client_operation_id=client_operation_id,
            )
            reconciliation.status = TransferDiscrepancyReconciliation.Status.COMPLETED
            reconciliation.completed_at = now
            reconciliation.completed_by_worker_code = worker_code
            reconciliation.save(update_fields=["status", "completed_at", "completed_by_worker_code", "updated_at"])
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancyReconciliation",
                entity_id=str(reconciliation.id),
                message=(
                    f"Worker {worker_code} completed reconciliation {reconciliation.reference} "
                    f"with final outcome: {decision.get_outcome_display()}."
                ),
            )
            reconciliation.refresh_from_db()
            decision.refresh_from_db()

        return Response(self._manual_decision_response(decision))


class TransferDiscrepancySourceStockVerificationFilter(django_filters.FilterSet):
    source_branch = django_filters.NumberFilter(field_name="reconciliation__discrepancy__transfer__source_branch_id")
    branch = django_filters.CharFilter(field_name="reconciliation__discrepancy__transfer__source_branch__code", lookup_expr="iexact")
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = TransferDiscrepancySourceStockVerification
        fields = ["status", "source_branch", "branch"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(reference__icontains=value)
            | models.Q(reconciliation__reference__icontains=value)
            | models.Q(reconciliation__source_review__reference__icontains=value)
            | models.Q(reconciliation__discrepancy__reference__icontains=value)
            | models.Q(reconciliation__discrepancy__pallet__scan_code__icontains=value)
            | models.Q(reconciliation__discrepancy__transfer__reference__icontains=value)
            | models.Q(items__product__sku__icontains=value)
        ).distinct()


class TransferDiscrepancyTransitInvestigationFilter(django_filters.FilterSet):
    source_branch = django_filters.NumberFilter(field_name="reconciliation__discrepancy__transfer__source_branch_id")
    destination_branch = django_filters.NumberFilter(field_name="reconciliation__discrepancy__transfer__destination_branch_id")
    branch = django_filters.CharFilter(method="filter_branch")
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = TransferDiscrepancyTransitInvestigation
        fields = ["status", "source_branch", "destination_branch", "branch"]

    def filter_branch(self, queryset, name, value):
        return queryset.filter(
            models.Q(reconciliation__discrepancy__transfer__source_branch__code__iexact=value)
            | models.Q(reconciliation__discrepancy__transfer__destination_branch__code__iexact=value)
        )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(reference__icontains=value)
            | models.Q(reconciliation__reference__icontains=value)
            | models.Q(reconciliation__source_review__reference__icontains=value)
            | models.Q(reconciliation__discrepancy__reference__icontains=value)
            | models.Q(reconciliation__discrepancy__pallet__scan_code__icontains=value)
            | models.Q(reconciliation__discrepancy__transfer__reference__icontains=value)
        ).distinct()


class TransferDiscrepancyTransitInvestigationViewSet(ReadOnlyModelViewSet):
    queryset = TransferDiscrepancyTransitInvestigation.objects.select_related(
        "reconciliation",
        "reconciliation__source_review",
        "reconciliation__discrepancy",
        "reconciliation__discrepancy__pallet",
        "reconciliation__discrepancy__transfer",
        "reconciliation__discrepancy__transfer__source_branch",
        "reconciliation__discrepancy__transfer__destination_branch",
    ).prefetch_related(
        "reconciliation__discrepancy__items",
        "reconciliation__discrepancy__items__product",
        "reconciliation__discrepancy__pallet__items",
        "reconciliation__discrepancy__pallet__items__product",
        "reconciliation__discrepancy__pallet__receiving_scans",
        "reconciliation__discrepancy__pallet__receiving_scans__product",
        "reconciliation__discrepancy__pallet__receiving_scans__destination_location",
    )
    serializer_class = TransferDiscrepancyTransitInvestigationSerializer
    filterset_class = TransferDiscrepancyTransitInvestigationFilter
    search_fields = [
        "reference",
        "reconciliation__reference",
        "reconciliation__source_review__reference",
        "reconciliation__discrepancy__reference",
        "reconciliation__discrepancy__pallet__scan_code",
        "reconciliation__discrepancy__transfer__reference",
    ]
    ordering_fields = ["reference", "status", "finding", "created_at", "updated_at", "completed_at"]

    def get_queryset(self):
        return filter_dual_branch_queryset(
            super().get_queryset(),
            self.request,
            "reconciliation__discrepancy__transfer__source_branch",
            "reconciliation__discrepancy__transfer__destination_branch",
        )

    def _validate_transit_workflow(self, investigation):
        reconciliation = investigation.reconciliation
        if reconciliation.route != TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION:
            return Response(
                {"detail": "This reconciliation does not require transit investigation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if reconciliation.source_review.status != TransferDiscrepancySourceReview.Status.COMPLETED:
            return Response({"detail": "Transit investigation requires a completed source review."}, status=status.HTTP_400_BAD_REQUEST)
        if reconciliation.source_review.finding != TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES:
            return Response(
                {"detail": "Transit investigation requires dispatch evidence matching expected quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if reconciliation.discrepancy.status != TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
            return Response(
                {"detail": "Transit investigation requires a confirmed-shortage discrepancy."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @action(detail=True, methods=["post"], url_path="begin")
    def begin(self, request, pk=None):
        worker_code = actor_code(request)
        with transaction.atomic():
            investigation = (
                TransferDiscrepancyTransitInvestigation.objects.select_for_update()
                .select_related("reconciliation", "reconciliation__source_review", "reconciliation__discrepancy")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                transfer = investigation.reconciliation.discrepancy.transfer
                require_any_branch_access(request.user, [transfer.source_branch, transfer.destination_branch])
            if investigation.status == TransferDiscrepancyTransitInvestigation.Status.COMPLETED:
                return Response(
                    {"detail": "This transit investigation has already been completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validation = self._validate_transit_workflow(investigation)
            if validation is not None:
                return validation
            if investigation.reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
                return Response(
                    {"detail": "Transit investigation requires an in-progress reconciliation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if investigation.status == TransferDiscrepancyTransitInvestigation.Status.INVESTIGATING:
                return Response(
                    {
                        "message": "Transit investigation already started.",
                        "transit_investigation": self.get_serializer(investigation).data,
                    }
                )
            investigation.status = TransferDiscrepancyTransitInvestigation.Status.INVESTIGATING
            investigation.started_at = timezone.now()
            investigation.started_by_worker_code = worker_code
            investigation.save(update_fields=["status", "started_at", "started_by_worker_code", "updated_at"])
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancyTransitInvestigation",
                entity_id=str(investigation.id),
                message=f"Worker {worker_code} began transit investigation {investigation.reference}.",
            )

        return Response(
            {"message": "Transit investigation started.", "transit_investigation": self.get_serializer(investigation).data}
        )

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        finding = str(request.data.get("finding", "")).strip()
        finding_note = str(request.data.get("finding_note", "")).strip()
        worker_code = actor_code(request)
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        existing = (
            TransferDiscrepancyTransitInvestigation.objects.select_related("reconciliation")
            .filter(pk=pk, completion_operation_id=client_operation_id)
            .first()
        )
        if existing is not None and existing.status == TransferDiscrepancyTransitInvestigation.Status.COMPLETED:
            return Response(
                {
                    "message": "Transit investigation completion already recorded.",
                    "transit_investigation": self.get_serializer(existing).data,
                }
            )
        valid_findings = {choice.value for choice in TransferDiscrepancyTransitInvestigation.Finding}
        if finding not in valid_findings:
            return Response({"detail": "Invalid transit investigation finding."}, status=status.HTTP_400_BAD_REQUEST)
        if not finding_note:
            return Response({"detail": "finding_note is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            investigation = (
                TransferDiscrepancyTransitInvestigation.objects.select_for_update()
                .select_related(
                    "reconciliation",
                    "reconciliation__source_review",
                    "reconciliation__discrepancy",
                    "reconciliation__discrepancy__pallet",
                    "reconciliation__discrepancy__transfer",
                    "reconciliation__discrepancy__transfer__source_branch",
                    "reconciliation__discrepancy__transfer__destination_branch",
                )
                .get(pk=pk)
            )
            reconciliation = TransferDiscrepancyReconciliation.objects.select_for_update().get(pk=investigation.reconciliation_id)
            investigation.reconciliation = reconciliation
            if request.user.is_authenticated:
                transfer = investigation.reconciliation.discrepancy.transfer
                require_any_branch_access(request.user, [transfer.source_branch, transfer.destination_branch])
            if (
                investigation.completion_operation_id == client_operation_id
                and investigation.status == TransferDiscrepancyTransitInvestigation.Status.COMPLETED
            ):
                return Response(
                    {
                        "message": "Transit investigation completion already recorded.",
                        "transit_investigation": self.get_serializer(investigation).data,
                    }
                )
            if investigation.status == TransferDiscrepancyTransitInvestigation.Status.COMPLETED:
                return Response(
                    {"detail": "This transit investigation has already been completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if investigation.status != TransferDiscrepancyTransitInvestigation.Status.INVESTIGATING:
                return Response(
                    {"detail": "Transit investigation can be completed only while investigating."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validation = self._validate_transit_workflow(investigation)
            if validation is not None:
                return validation
            if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
                return Response(
                    {"detail": "Transit investigation completion requires an in-progress reconciliation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            now = timezone.now()
            investigation.finding = finding
            investigation.finding_note = finding_note
            investigation.status = TransferDiscrepancyTransitInvestigation.Status.COMPLETED
            investigation.completed_at = now
            investigation.completed_by_worker_code = worker_code
            investigation.completion_operation_id = client_operation_id
            investigation.save(
                update_fields=[
                    "finding",
                    "finding_note",
                    "status",
                    "completed_at",
                    "completed_by_worker_code",
                    "completion_operation_id",
                    "updated_at",
                ]
            )
            reconciliation.status = TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED
            reconciliation.save(update_fields=["status", "updated_at"])
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancyTransitInvestigation",
                entity_id=str(investigation.id),
                message=(
                    f"Worker {worker_code} completed transit investigation {investigation.reference} "
                    f"with finding: {investigation.get_finding_display()}."
                ),
            )
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancyReconciliation",
                entity_id=str(reconciliation.id),
                message=(
                    f"Reconciliation {reconciliation.reference} now requires manual action after transit investigation "
                    f"{investigation.reference} was completed."
                ),
            )
            investigation.refresh_from_db()

        return Response(
            {
                "message": "Transit investigation completed.",
                "transit_investigation": self.get_serializer(investigation).data,
            }
        )


class TransferDiscrepancySourceStockVerificationViewSet(ReadOnlyModelViewSet):
    queryset = TransferDiscrepancySourceStockVerification.objects.select_related(
        "reconciliation",
        "reconciliation__source_review",
        "reconciliation__discrepancy",
        "reconciliation__discrepancy__pallet",
        "reconciliation__discrepancy__transfer",
        "reconciliation__discrepancy__transfer__source_branch",
        "reconciliation__discrepancy__transfer__destination_branch",
    ).prefetch_related("items", "items__product", "recoveries", "recoveries__product", "recoveries__destination_location")
    serializer_class = TransferDiscrepancySourceStockVerificationSerializer
    filterset_class = TransferDiscrepancySourceStockVerificationFilter
    search_fields = [
        "reference",
        "reconciliation__reference",
        "reconciliation__source_review__reference",
        "reconciliation__discrepancy__reference",
        "reconciliation__discrepancy__pallet__scan_code",
        "reconciliation__discrepancy__transfer__reference",
        "items__product__sku",
    ]
    ordering_fields = ["reference", "status", "created_at", "updated_at", "completed_at"]

    def get_queryset(self):
        return filter_branch_queryset(
            super().get_queryset(),
            self.request,
            "reconciliation__discrepancy__transfer__source_branch",
        )

    def _validate_active_source_verification(self, verification):
        reconciliation = verification.reconciliation
        if reconciliation.route != TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION:
            return Response(
                {"detail": "This reconciliation does not require source stock verification."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
            return Response(
                {"detail": "Source stock verification requires an in-progress reconciliation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def _source_recovery_response(self, recovery: TransferDiscrepancySourceStockRecovery):
        verification = recovery.verification
        item = recovery.verification_item
        totals = get_source_verification_totals(verification)
        return {
            "verification_reference": verification.reference,
            "verification_status": verification.status,
            "reconciliation_reference": verification.reconciliation.reference,
            "reconciliation_status": verification.reconciliation.status,
            "product_code": recovery.product.sku,
            "found_quantity": str(recovery.quantity),
            "line_found_quantity": str(item.found_quantity),
            "line_remaining_quantity": str(source_verification_item_remaining(item)),
            "total_found_quantity": str(totals["found"]),
            "total_remaining_quantity": str(totals["remaining"]),
            "destination_location_code": recovery.destination_location.code,
            "recovery_id": recovery.id,
        }

    @action(detail=True, methods=["post"], url_path="begin")
    def begin(self, request, pk=None):
        worker_code = actor_code(request)
        with transaction.atomic():
            verification = (
                TransferDiscrepancySourceStockVerification.objects.select_for_update()
                .select_related("reconciliation")
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(request.user, verification.reconciliation.discrepancy.transfer.source_branch)
            if verification.status == TransferDiscrepancySourceStockVerification.Status.COMPLETED:
                return Response(
                    {"detail": "This source stock verification has already been completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if verification.status == TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED:
                return Response(
                    {"detail": "This source stock verification has already been completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validation = self._validate_active_source_verification(verification)
            if validation is not None:
                return validation
            if verification.status == TransferDiscrepancySourceStockVerification.Status.INVESTIGATING:
                return Response(
                    {
                        "message": "Source stock verification already started.",
                        "verification": self.get_serializer(verification).data,
                    }
                )

            verification.status = TransferDiscrepancySourceStockVerification.Status.INVESTIGATING
            verification.started_at = timezone.now()
            verification.started_by_worker_code = worker_code
            verification.save(update_fields=["status", "started_at", "started_by_worker_code", "updated_at"])
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancySourceStockVerification",
                entity_id=str(verification.id),
                message=f"Worker {worker_code} began source stock verification {verification.reference}.",
            )

        return Response({"message": "Source stock verification started.", "verification": self.get_serializer(verification).data})

    @action(detail=True, methods=["post"], url_path="record-found")
    def record_found(self, request, pk=None):
        product_code = str(request.data.get("product_code", "")).strip()
        destination_location_code = str(request.data.get("destination_location_code", "")).strip()
        worker_code = actor_code(request)
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()
        raw_quantity = str(request.data.get("quantity", "1")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        existing = (
            TransferDiscrepancySourceStockRecovery.objects.select_related(
                "verification",
                "verification__reconciliation",
                "verification_item",
                "product",
                "destination_location",
            )
            .filter(client_operation_id=client_operation_id)
            .first()
        )
        if existing is not None:
            return Response(
                {"message": "Found source stock already recorded.", "recovery": self._source_recovery_response(existing)}
            )
        if not product_code:
            return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not destination_location_code:
            return Response({"detail": "destination_location_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not raw_quantity.isdigit():
            return Response({"detail": "Quantity must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)
        quantity = Decimal(raw_quantity)
        if quantity <= 0:
            return Response({"detail": "Quantity must be at least 1."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            verification = (
                TransferDiscrepancySourceStockVerification.objects.select_for_update()
                .select_related(
                    "reconciliation",
                    "reconciliation__discrepancy",
                    "reconciliation__discrepancy__transfer",
                    "reconciliation__discrepancy__transfer__source_branch",
                )
                .get(pk=pk)
            )
            if request.user.is_authenticated:
                require_branch_access(request.user, verification.reconciliation.discrepancy.transfer.source_branch)
            if verification.status != TransferDiscrepancySourceStockVerification.Status.INVESTIGATING:
                if verification.status in {
                    TransferDiscrepancySourceStockVerification.Status.COMPLETED,
                    TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED,
                }:
                    return Response(
                        {"detail": "This source stock verification has already been completed."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {"detail": "Found stock can be recorded only while source verification is investigating."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validation = self._validate_active_source_verification(verification)
            if validation is not None:
                return validation

            product = Product.objects.filter(models.Q(sku__iexact=product_code) | models.Q(barcode__iexact=product_code)).first()
            if product is None:
                return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

            item = verification.items.select_for_update().select_related("product").filter(product=product).first()
            if item is None:
                return Response(
                    {"detail": "This product is not part of the source stock verification."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            remaining = source_verification_item_remaining(item)
            if remaining <= 0:
                return Response(
                    {"detail": "This verification line is already fully accounted for."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if quantity > remaining:
                return Response(
                    {"detail": "Found quantity exceeds the remaining verification quantity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            source_branch = verification.reconciliation.discrepancy.transfer.source_branch
            destination_location = (
                Location.objects.select_related("branch")
                .filter(branch=source_branch, code__iexact=destination_location_code)
                .first()
            )
            if destination_location is None:
                if Location.objects.filter(code__iexact=destination_location_code).exists():
                    return Response({"detail": "Source location belongs to another branch."}, status=status.HTTP_400_BAD_REQUEST)
                return Response({"detail": "Source location not found in source branch."}, status=status.HTTP_404_NOT_FOUND)
            if destination_location.code.upper() == "UNCONFIRMED":
                return Response({"detail": "UNCONFIRMED cannot be used as a source stock recovery location."}, status=status.HTTP_400_BAD_REQUEST)

            inventory_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=source_branch,
                location=destination_location,
                product=product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            inventory_item.quantity_on_hand = F("quantity_on_hand") + quantity
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

            movement = StockMovement.objects.create(
                branch=source_branch,
                product=product,
                inventory_item=inventory_item,
                destination_location=destination_location,
                movement_type=StockMovement.MovementType.SOURCE_DISCREPANCY_RECOVERY,
                quantity=quantity,
                reference=verification.reference,
                performed_by=audit_actor(request),
            )
            recovery = TransferDiscrepancySourceStockRecovery.objects.create(
                verification=verification,
                verification_item=item,
                discrepancy=verification.reconciliation.discrepancy,
                product=product,
                quantity=quantity,
                destination_location=destination_location,
                worker_code=worker_code,
                client_operation_id=client_operation_id,
                stock_movement=movement,
            )
            item.found_quantity = F("found_quantity") + quantity
            item.last_found_at = timezone.now()
            item.save(update_fields=["found_quantity", "last_found_at", "updated_at"])
            item.refresh_from_db()

            quantity_label = format_piece_quantity(quantity)
            unit_word = unit_label(quantity)
            restore_word = "it" if quantity == 1 else "them"
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancySourceStockVerification",
                entity_id=str(verification.id),
                message=(
                    f"Worker {worker_code} found {quantity_label} {unit_word} of {product.sku} at source location "
                    f"{destination_location.code} during verification {verification.reference} and restored {restore_word} "
                    f"to inventory."
                ),
            )

            verification_completed, reconciliation_completed = complete_source_verification_if_finished(verification, worker_code)
            if verification_completed:
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.STATUS_CHANGE,
                    entity_name="TransferDiscrepancySourceStockVerification",
                    entity_id=str(verification.id),
                    message=(
                        f"Source stock verification {verification.reference} was completed after all target quantity "
                        f"was found."
                    ),
                )
            if reconciliation_completed:
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.STATUS_CHANGE,
                    entity_name="TransferDiscrepancyReconciliation",
                    entity_id=str(verification.reconciliation.id),
                    message=(
                        f"Reconciliation {verification.reconciliation.reference} was completed after all target shortage "
                        f"quantity was found at the source branch."
                    ),
                )

            recovery.refresh_from_db()

        return Response({"message": "Found source stock recorded.", "recovery": self._source_recovery_response(recovery)})

    @action(detail=True, methods=["post"], url_path="complete-search")
    def complete_search(self, request, pk=None):
        worker_code = actor_code(request)
        search_completion_note = str(request.data.get("search_completion_note", "")).strip()
        client_operation_id = str(request.data.get("client_operation_id", "")).strip()

        if not client_operation_id:
            return Response({"detail": "client_operation_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        existing = (
            TransferDiscrepancySourceStockVerification.objects.select_related("reconciliation")
            .filter(pk=pk, search_completion_operation_id=client_operation_id)
            .first()
        )
        if existing is not None and existing.status == TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED:
            return Response(
                {
                    "message": "Source search completion already recorded.",
                    "verification": self.get_serializer(existing).data,
                }
            )

        if len(search_completion_note) > 1000:
            return Response(
                {"detail": "search_completion_note must be 1000 characters or fewer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            verification = (
                TransferDiscrepancySourceStockVerification.objects.select_for_update()
                .select_related(
                    "reconciliation",
                    "reconciliation__source_review",
                    "reconciliation__discrepancy",
                    "reconciliation__discrepancy__pallet",
                    "reconciliation__discrepancy__transfer",
                    "reconciliation__discrepancy__transfer__source_branch",
                    "reconciliation__discrepancy__transfer__destination_branch",
                )
                .get(pk=pk)
            )
            list(verification.items.select_for_update().select_related("product"))
            reconciliation = (
                TransferDiscrepancyReconciliation.objects.select_for_update()
                .select_related("discrepancy", "source_review")
                .get(pk=verification.reconciliation_id)
            )
            verification.reconciliation = reconciliation
            if request.user.is_authenticated:
                require_branch_access(request.user, verification.reconciliation.discrepancy.transfer.source_branch)

            if (
                verification.search_completion_operation_id == client_operation_id
                and verification.status == TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED
            ):
                return Response(
                    {
                        "message": "Source search completion already recorded.",
                        "verification": self.get_serializer(verification).data,
                    }
                )

            if verification.status in {
                TransferDiscrepancySourceStockVerification.Status.COMPLETED,
                TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED,
            }:
                return Response(
                    {"detail": "This source stock verification has already been completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if verification.status != TransferDiscrepancySourceStockVerification.Status.INVESTIGATING:
                return Response(
                    {"detail": "Source search can be completed only while verification is investigating."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if reconciliation.route != TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION:
                return Response(
                    {"detail": "This reconciliation does not require source stock verification."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
                return Response(
                    {"detail": "Source search completion requires an in-progress reconciliation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            totals = get_source_verification_totals(verification)
            unresolved_quantity = totals["remaining"]
            if unresolved_quantity <= 0:
                return Response(
                    {"detail": "All target quantity has already been found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            now = timezone.now()
            verification.status = TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED
            verification.search_completed_at = now
            verification.search_completed_by_worker_code = worker_code
            verification.search_completion_note = search_completion_note
            verification.search_completion_operation_id = client_operation_id
            verification.save(
                update_fields=[
                    "status",
                    "search_completed_at",
                    "search_completed_by_worker_code",
                    "search_completion_note",
                    "search_completion_operation_id",
                    "updated_at",
                ]
            )

            reconciliation.status = TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED
            reconciliation.save(update_fields=["status", "updated_at"])

            unresolved_quantity_label = format_piece_quantity(unresolved_quantity)
            unit_word = unit_label(unresolved_quantity)
            remain_word = "remains" if unresolved_quantity == 1 else "remain"
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancySourceStockVerification",
                entity_id=str(verification.id),
                message=(
                    f"Worker {worker_code} completed source stock verification {verification.reference} "
                    f"with {unresolved_quantity_label} {unit_word} unresolved."
                ),
            )
            AuditLog.objects.create(
                actor=audit_actor(request),
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancyReconciliation",
                entity_id=str(reconciliation.id),
                message=(
                    f"Reconciliation {reconciliation.reference} now requires manual action because "
                    f"{unresolved_quantity_label} source-verification {unit_word} {remain_word} unresolved."
                ),
            )
            verification.refresh_from_db()

        return Response(
            {
                "message": "Source search completed with unresolved stock.",
                "verification": self.get_serializer(verification).data,
            }
        )
