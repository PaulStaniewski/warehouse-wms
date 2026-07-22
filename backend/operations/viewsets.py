from datetime import datetime
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
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet, ViewSet

from accounts.authorization import (
    branch_codes_filter,
    branch_ids_filter,
    filter_rows_for_user,
    require_any_branch_access,
    require_branch_access,
)
from accounts.models import UserBranchMembership
from operations.models import (
    AuditLog,
    BranchDispatchPolicy,
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
    PickingShortage,
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
    ShipmentRouteAssignment,
    ShipmentStatusHistory,
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
    BranchDispatchPolicySerializer,
    CycleCountLocationSerializer,
    CycleCountRecountSerializer,
    CycleCountReviewQueueItemSerializer,
    CycleCountSessionSerializer,
    DeliveryRouteSerializer,
    CorrectionActivityReportSerializer,
    ExternalReturnDocumentSerializer,
    InventoryExceptionSummarySerializer,
    OrderLineSerializer,
    OrderSerializer,
    PickingTaskSerializer,
    PickingShortageSerializer,
    ReplenishmentRequestSerializer,
    ReturnBatchSerializer,
    ReturnLineSerializer,
    RouteRoundScheduleSerializer,
    RouteRunSerializer,
    SalesCorrectionSerializer,
    SalesHistoryCandidateSerializer,
    ShipmentSerializer,
    StockMovementSerializer,
    TransportOverviewSerializer,
    TransferDiscrepancyActionSerializer,
    TransferDiscrepancyReconciliationSerializer,
    TransferDiscrepancySerializer,
    TransferDiscrepancySourceStockVerificationSerializer,
    TransferDiscrepancySourceReviewSerializer,
    TransferDiscrepancyTransitInvestigationSerializer,
)
from operations.operational_projections import active_route_run_queryset
from operations.route_services import (
    aware_datetime,
    create_route_run_from_schedule,
    manual_change_shipment_route,
    override_route_run,
    operational_identifier,
    validate_dispatch_schedule,
)
from operations.shipment_services import (
    activate_shipment,
    cancel_shipment,
    change_shipment_route,
    change_shipment_status,
    close_shipment_route,
    close_route_run,
    confirm_picking_route,
    post_inter_branch_documents,
    post_picking_lists,
    prepare_shipment,
    print_shipment_documents,
    remove_shipment_line_quantity,
    sync_shipment_status_from_work,
)
from operations.return_services import (
    Conflict,
    apply_return_action,
    confirm_sales_correction,
    corrected_quantity_for_order_line,
    parse_positive_quantity,
    remaining_correctable_quantity,
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
from warehouse.models import Branch, InventoryItem, Location, Product


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


def filter_branch_code_queryset(queryset, request, field_prefix: str, param_name: str = "branch"):
    requested = request.query_params.get(param_name, "").strip()
    if requested:
        if requested.isdigit():
            if not Branch.objects.filter(pk=int(requested)).exists():
                raise ValidationError({param_name: ["Unknown branch."]})
        elif not Branch.objects.filter(code__iexact=requested).exists():
            raise ValidationError({param_name: ["Unknown branch code."]})
    return filter_branch_queryset(queryset, request, field_prefix, param_name)


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
    date_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

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
        fields = [
            "actor",
            "actor_name",
            "action",
            "action_type",
            "event_type",
            "result",
            "product",
            "cart",
            "location",
            "order",
            "date_from",
            "date_to",
        ]


class RouteRunFilter(django_filters.FilterSet):
    branch = django_filters.NumberFilter(field_name="route__branch_id")
    branch_code = django_filters.CharFilter(field_name="route__branch__code", lookup_expr="iexact")

    class Meta:
        model = RouteRun
        fields = ["route", "branch", "branch_code", "status", "service_date", "departure_time"]


class DeliveryRouteViewSet(ReadOnlyModelViewSet):
    queryset = DeliveryRoute.objects.select_related("branch")
    serializer_class = DeliveryRouteSerializer
    filterset_fields = ["code", "is_active"]
    search_fields = ["code", "name", "branch__code", "branch__name"]
    ordering_fields = ["branch__code", "code", "name", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_code_queryset(super().get_queryset(), self.request, "branch")


class BranchDispatchPolicyViewSet(ModelViewSet):
    queryset = BranchDispatchPolicy.objects.select_related("branch")
    serializer_class = BranchDispatchPolicySerializer

    def get_queryset(self):
        return filter_branch_code_queryset(super().get_queryset(), self.request, "branch")

    def perform_create(self, serializer):
        branch = serializer.validated_data["branch"]
        require_branch_access(self.request.user, branch, leader_required=True)
        serializer.save()

    def perform_update(self, serializer):
        branch = serializer.instance.branch
        require_branch_access(self.request.user, branch, leader_required=True)
        instance = serializer.save()
        validate_dispatch_schedule(instance.branch)


class RouteRoundScheduleViewSet(ModelViewSet):
    queryset = RouteRoundSchedule.objects.select_related("route", "route__branch")
    serializer_class = RouteRoundScheduleSerializer
    filterset_fields = ["route", "weekday", "is_active", "dispatch_wave"]
    search_fields = ["route__code", "route__name", "operational_label", "dispatch_wave"]
    ordering_fields = ["weekday", "departure_time", "route__code", "round_number"]

    def get_queryset(self):
        return filter_branch_code_queryset(super().get_queryset(), self.request, "route__branch")

    def perform_create(self, serializer):
        route = serializer.validated_data["route"]
        require_branch_access(self.request.user, route.branch, leader_required=True)
        instance = serializer.save()
        try:
            validate_dispatch_schedule(route.branch)
        except Exception:
            instance.delete()
            raise
        AuditLog.objects.create(
            actor=self.request.user,
            action_type=AuditLog.ActionType.CREATE,
            event_type="route_schedule_created",
            branch=route.branch,
            reference=route.code,
            entity_name="RouteRoundSchedule",
            entity_id=str(instance.id),
            message=f"{self.request.user.username} created a route round schedule for {route.code}.",
        )

    def perform_update(self, serializer):
        route = serializer.validated_data.get("route", serializer.instance.route)
        require_branch_access(self.request.user, route.branch, leader_required=True)
        instance = serializer.save()
        validate_dispatch_schedule(instance.route.branch)
        AuditLog.objects.create(
            actor=self.request.user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="route_schedule_changed",
            branch=instance.route.branch,
            reference=instance.route.code,
            entity_name="RouteRoundSchedule",
            entity_id=str(instance.id),
            message=f"{self.request.user.username} changed a route round schedule for {instance.route.code}.",
        )


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
    queryset = RouteRun.objects.select_related("route", "route__branch", "schedule").prefetch_related(
        "shipments",
        "shipments__lines",
        "shipments__lines__order_line__picking_tasks__job_task",
        "shipments__lines__order_line__picking_tasks__task_claims__cart_work_participant",
        "orders__lines__picking_tasks__job_task",
        "orders__lines__picking_tasks__task_claims__cart_work_participant",
    )
    serializer_class = RouteRunSerializer
    filterset_class = RouteRunFilter
    search_fields = ["route__code", "route__name", "route__branch__code"]
    ordering_fields = ["service_date", "departure_time", "run_number", "status", "created_at", "updated_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = filter_branch_queryset(queryset, self.request, "route__branch")
        if self.action == "list":
            return active_route_run_queryset(queryset)
        return queryset

    @action(detail=True, methods=["post"], url_path="override-times")
    def override_times(self, request, pk=None):
        route_run = self.get_object()
        try:
            cutoff_at = datetime.fromisoformat(str(request.data.get("cutoff_at", "")))
            departure_at = datetime.fromisoformat(str(request.data.get("planned_departure_at", "")))
        except ValueError:
            return Response({"detail": "cutoff_at and planned_departure_at must be ISO datetimes."}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.is_naive(cutoff_at):
            cutoff_at = timezone.make_aware(cutoff_at, timezone.get_current_timezone())
        if timezone.is_naive(departure_at):
            departure_at = timezone.make_aware(departure_at, timezone.get_current_timezone())
        route_run, _history = override_route_run(
            request.user,
            route_run,
            cutoff_at=cutoff_at,
            planned_departure_at=departure_at,
            dispatch_wave=str(request.data.get("dispatch_wave", "")).strip(),
        )
        return Response({"message": "Route run timing updated.", "route_run": self.get_serializer(route_run).data})

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
        result = close_route_run(
            request.user,
            self.get_object().id,
            str(request.data.get("printer_code", "WMS-ROUTE")).strip() or "WMS-ROUTE",
        )
        route_run = RouteRun.objects.get(pk=result["route_run_id"])
        return Response(
            {
                "message": (
                    f"Route {result['operational_identifier']} was already closed."
                    if result["replayed"]
                    else f"Route {result['operational_identifier']} closed. "
                    f"{result['document_count']} Shipment documents were printed."
                ),
                "route_run": self.get_serializer(route_run).data,
                **result,
            }
        )

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


class ExternalReturnDocumentFilter(django_filters.FilterSet):
    branch = django_filters.CharFilter(method="filter_branch")
    external_reference = django_filters.CharFilter(field_name="external_reference", lookup_expr="iexact")
    customer = django_filters.CharFilter(field_name="customer_name", lookup_expr="icontains")
    source_sales_document = django_filters.CharFilter(field_name="source_sales_document_reference", lookup_expr="icontains")
    product = django_filters.CharFilter(method="filter_product")
    employee = django_filters.CharFilter(method="filter_employee")
    date_from = django_filters.DateFilter(field_name="imported_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="imported_at", lookup_expr="date__lte")

    def filter_branch(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(branch_id=value)
        return queryset.filter(branch__code__iexact=value)

    def filter_product(self, queryset, name, value):
        return queryset.filter(
            models.Q(lines__product__sku__iexact=value)
            | models.Q(lines__product__barcode__iexact=value)
            | models.Q(lines__product__name__icontains=value)
        ).distinct()

    def filter_employee(self, queryset, name, value):
        return queryset.filter(actions__performed_by__username__iexact=value).distinct()

    class Meta:
        model = ExternalReturnDocument
        fields = [
            "branch",
            "external_reference",
            "status",
            "customer",
            "source_sales_document",
            "product",
            "employee",
            "date_from",
            "date_to",
        ]


class ExternalReturnDocumentViewSet(ReadOnlyModelViewSet):
    queryset = ExternalReturnDocument.objects.select_related("branch").prefetch_related(
        "lines",
        "lines__product",
        "lines__actions",
        "lines__actions__performed_by",
        "actions",
        "actions__performed_by",
        "actions__product",
        "actions__stock_movement",
    )
    serializer_class = ExternalReturnDocumentSerializer
    filterset_class = ExternalReturnDocumentFilter
    search_fields = ["external_reference", "customer_name", "source_sales_document_reference", "lines__product__sku"]
    ordering = ["-imported_at", "-created_at"]
    ordering_fields = ["external_reference", "status", "imported_at", "external_created_at", "created_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        reference = str(request.query_params.get("external_reference", "")).strip()
        if not reference:
            return Response({"detail": "external_reference is required."}, status=status.HTTP_400_BAD_REQUEST)
        document = self.get_queryset().filter(external_reference__iexact=reference).first()
        if document is None:
            return Response({"detail": "Return document not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>\d+)/actions")
    def record_action(self, request, pk=None, line_id=None):
        document = self.get_object()
        action, replayed = apply_return_action(
            user=request.user,
            document_id=document.id,
            line_id=line_id,
            action_type=str(request.data.get("action_type", "")).strip(),
            quantity=request.data.get("quantity"),
            note=request.data.get("note", ""),
            client_operation_id=request.data.get("client_operation_id"),
        )
        document.refresh_from_db()
        serializer = self.get_serializer(document)
        return Response(
            {
                "message": "Return action replayed." if replayed else "Return action recorded.",
                "action_id": action.id,
                "document": serializer.data,
            }
        )


class SalesCorrectionFilter(django_filters.FilterSet):
    branch = django_filters.CharFilter(method="filter_branch")
    customer = django_filters.CharFilter(field_name="lines__customer_name_snapshot", lookup_expr="icontains")
    source_sales_document = django_filters.CharFilter(field_name="lines__source_sales_document_reference", lookup_expr="icontains")
    product = django_filters.CharFilter(method="filter_product")
    employee = django_filters.CharFilter(field_name="confirmed_by__username", lookup_expr="iexact")
    date_from = django_filters.DateFilter(field_name="confirmed_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="confirmed_at", lookup_expr="date__lte")

    def filter_branch(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(branch_id=value)
        return queryset.filter(branch__code__iexact=value)

    def filter_product(self, queryset, name, value):
        return queryset.filter(
            models.Q(lines__product__sku__iexact=value)
            | models.Q(lines__product__barcode__iexact=value)
            | models.Q(lines__product__name__icontains=value)
        ).distinct()

    class Meta:
        model = SalesCorrection
        fields = ["branch", "status", "customer", "source_sales_document", "product", "employee", "date_from", "date_to"]


class SalesCorrectionViewSet(ModelViewSet):
    queryset = SalesCorrection.objects.select_related("branch", "created_by", "confirmed_by").prefetch_related(
        "lines",
        "lines__product",
        "lines__source_order",
        "lines__source_order_line",
        "lines__returns_location",
        "lines__stock_movement",
    )
    serializer_class = SalesCorrectionSerializer
    filterset_class = SalesCorrectionFilter
    http_method_names = ["get", "post", "head", "options"]
    search_fields = [
        "reference",
        "lines__customer_name_snapshot",
        "lines__source_sales_document_reference",
        "lines__product__sku",
    ]
    ordering = ["-created_at"]
    ordering_fields = ["reference", "status", "confirmed_at", "created_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")

    def create(self, request, *args, **kwargs):
        branch_value = str(request.data.get("branch") or request.data.get("branch_code") or "").strip()
        if not branch_value:
            return Response({"detail": "branch is required."}, status=status.HTTP_400_BAD_REQUEST)
        if branch_value.isdigit():
            branch = get_object_or_404(Branch, pk=branch_value)
        else:
            branch = get_object_or_404(Branch, code__iexact=branch_value)
        require_branch_access(request.user, branch)
        correction = SalesCorrection.objects.create(
            branch=branch,
            created_by=request.user,
            note=str(request.data.get("note", "")).strip(),
        )
        AuditLog.objects.create(
            actor=request.user,
            action_type=AuditLog.ActionType.CREATE,
            event_type="sales_correction_draft_created",
            branch=branch,
            reference=correction.reference,
            entity_name="SalesCorrection",
            entity_id=str(correction.id),
            message=f"{request.user.username} created sales correction draft {correction.reference}.",
        )
        return Response(self.get_serializer(correction).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="sales-history")
    def sales_history(self, request):
        product_code = str(request.query_params.get("product", "")).strip()
        branch_value = str(request.query_params.get("branch", "")).strip()
        if not product_code:
            return Response({"detail": "product is required."}, status=status.HTTP_400_BAD_REQUEST)
        product = Product.objects.filter(
            models.Q(sku__iexact=product_code) | models.Q(barcode__iexact=product_code)
        ).first()
        if product is None:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        queryset = OrderLine.objects.select_related("order", "order__branch", "product").filter(
            product=product,
            order__status=Order.Status.COMPLETED,
        )
        queryset = filter_branch_queryset(queryset, request, "order__branch")
        if branch_value:
            queryset = queryset.filter(
                models.Q(order__branch_id=branch_value) if branch_value.isdigit() else models.Q(order__branch__code__iexact=branch_value)
            )

        rows = []
        for line in queryset.order_by("-order__requested_ship_date", "-order__created_at", "line_number")[:100]:
            corrected = corrected_quantity_for_order_line(line)
            remaining = line.quantity_ordered - corrected
            if remaining <= 0:
                continue
            rows.append(
                {
                    "order": line.order_id,
                    "order_line": line.id,
                    "branch": line.order.branch_id,
                    "branch_code": line.order.branch.code,
                    "customer_name": line.order.customer_name,
                    "customer_alias": line.order.customer_alias,
                    "source_sales_document_reference": line.order.external_reference,
                    "sale_date": line.order.requested_ship_date,
                    "product": product.id,
                    "product_sku": product.sku,
                    "product_name": product.name,
                    "sold_quantity": str(line.quantity_ordered),
                    "previously_corrected_quantity": str(corrected),
                    "remaining_correctable_quantity": str(remaining),
                }
            )
        return Response(SalesHistoryCandidateSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"], url_path="add-line")
    def add_line(self, request, pk=None):
        correction = self.get_object()
        require_branch_access(request.user, correction.branch)
        if correction.status != SalesCorrection.Status.DRAFT:
            return Response({"detail": "Completed sales corrections are read-only."}, status=status.HTTP_400_BAD_REQUEST)
        source_line_id = request.data.get("source_order_line")
        quantity = parse_positive_quantity(request.data.get("quantity"))
        source_line = get_object_or_404(OrderLine.objects.select_related("order", "product"), pk=source_line_id)
        if source_line.order.branch_id != correction.branch_id:
            raise PermissionDenied("You do not have access to this branch or operation.")
        if source_line.order.status != Order.Status.COMPLETED:
            return Response({"detail": "Only completed sales can be corrected."}, status=status.HTTP_400_BAD_REQUEST)
        if correction.lines.filter(source_order_line=source_line).exists():
            return Response({"detail": "This sales line is already in the correction draft."}, status=status.HTTP_400_BAD_REQUEST)
        remaining = remaining_correctable_quantity(source_line)
        if quantity > remaining:
            return Response({"detail": "Quantity exceeds remaining correctable quantity."}, status=status.HTTP_400_BAD_REQUEST)
        line = SalesCorrectionLine.objects.create(
            correction=correction,
            product=source_line.product,
            source_order=source_line.order,
            source_order_line=source_line,
            customer_name_snapshot=source_line.order.customer_name,
            customer_alias_snapshot=source_line.order.customer_alias,
            source_sales_document_reference=source_line.order.external_reference,
            sold_quantity_snapshot=source_line.quantity_ordered,
            corrected_quantity=quantity,
        )
        return Response(SalesCorrectionSerializer(correction).data)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>\d+)/update")
    def update_line(self, request, pk=None, line_id=None):
        correction = self.get_object()
        require_branch_access(request.user, correction.branch)
        if correction.status != SalesCorrection.Status.DRAFT:
            return Response({"detail": "Completed sales corrections are read-only."}, status=status.HTTP_400_BAD_REQUEST)
        line = get_object_or_404(correction.lines.select_related("source_order_line"), pk=line_id)
        quantity = parse_positive_quantity(request.data.get("quantity"))
        if quantity > remaining_correctable_quantity(line.source_order_line, exclude_correction_id=correction.id):
            return Response({"detail": "Quantity exceeds remaining correctable quantity."}, status=status.HTTP_400_BAD_REQUEST)
        line.corrected_quantity = quantity
        line.save(update_fields=["corrected_quantity", "updated_at"])
        return Response(SalesCorrectionSerializer(correction).data)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>\d+)/remove")
    def remove_line(self, request, pk=None, line_id=None):
        correction = self.get_object()
        require_branch_access(request.user, correction.branch)
        if correction.status != SalesCorrection.Status.DRAFT:
            return Response({"detail": "Completed sales corrections are read-only."}, status=status.HTTP_400_BAD_REQUEST)
        line = get_object_or_404(correction.lines, pk=line_id)
        line.delete()
        return Response(SalesCorrectionSerializer(correction).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        correction, replayed = confirm_sales_correction(
            user=request.user,
            correction_id=self.get_object().id,
            client_operation_id=request.data.get("client_operation_id"),
        )
        correction.refresh_from_db()
        return Response(
            {
                "message": "Sales correction confirmation replayed." if replayed else "Sales correction confirmed.",
                "correction": self.get_serializer(correction).data,
            }
        )

    @action(detail=False, methods=["get"], url_path="activity-report")
    def activity_report(self, request):
        queryset = SalesCorrectionLine.objects.select_related(
            "correction",
            "correction__branch",
            "correction__confirmed_by",
            "product",
            "returns_location",
            "stock_movement",
        ).filter(correction__status=SalesCorrection.Status.COMPLETED)
        queryset = filter_branch_queryset(queryset, request, "correction__branch")
        employee = str(request.query_params.get("employee", "")).strip()
        if employee:
            queryset = queryset.filter(correction__confirmed_by__username__iexact=employee)
        correction_reference = str(request.query_params.get("correction_reference", "")).strip()
        if correction_reference:
            queryset = queryset.filter(correction__reference__iexact=correction_reference)
        customer = str(request.query_params.get("customer", "")).strip()
        if customer:
            queryset = queryset.filter(customer_name_snapshot__icontains=customer)
        source_sales_document = str(request.query_params.get("source_sales_document", "")).strip()
        if source_sales_document:
            queryset = queryset.filter(source_sales_document_reference__icontains=source_sales_document)
        product = str(request.query_params.get("product", "")).strip()
        if product:
            queryset = queryset.filter(models.Q(product__sku__iexact=product) | models.Q(product__barcode__iexact=product))
        date_from = parse_date(request.query_params.get("date_from", ""))
        date_to = parse_date(request.query_params.get("date_to", ""))
        if date_from:
            queryset = queryset.filter(correction__confirmed_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(correction__confirmed_at__date__lte=date_to)

        rows_queryset = queryset.order_by("-correction__confirmed_at", "-id")
        summary = {
            "completed_corrections": rows_queryset.values("correction_id").distinct().count(),
            "correction_lines": rows_queryset.count(),
            "total_corrected_quantity": str(
                rows_queryset.aggregate(total=models.Sum("corrected_quantity"))["total"] or Decimal("0")
            ),
        }
        rows = [
            {
                "id": line.id,
                "confirmed_at": line.correction.confirmed_at,
                "employee": line.correction.confirmed_by.username if line.correction.confirmed_by else "",
                "branch_code": line.correction.branch.code,
                "correction_reference": line.correction.reference,
                "customer_name": line.customer_name_snapshot,
                "source_sales_document_reference": line.source_sales_document_reference,
                "product_sku": line.product.sku,
                "product_name": line.product.name,
                "corrected_quantity": str(line.corrected_quantity),
                "returns_location_code": line.returns_location.code if line.returns_location else "",
                "stock_movement": line.stock_movement_id,
                "summary": summary,
            }
            for line in rows_queryset[:200]
        ]
        return Response({"summary": summary, "results": CorrectionActivityReportSerializer(rows, many=True).data})


class ShipmentFilter(django_filters.FilterSet):
    branch = django_filters.CharFilter(method="filter_branch")
    shipment_status = django_filters.CharFilter(field_name="status", lookup_expr="iexact")
    picking_status = django_filters.CharFilter(method="filter_picking_status")
    route = django_filters.CharFilter(method="filter_route")
    delivery_date = django_filters.DateFilter(field_name="delivery_date")
    customer = django_filters.CharFilter(method="filter_customer")
    payment_method = django_filters.CharFilter(field_name="payment_method", lookup_expr="icontains")
    external_reference = django_filters.CharFilter(field_name="external_reference", lookup_expr="icontains")

    def filter_branch(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(branch_id=value)
        return queryset.filter(branch__code__iexact=value)

    def filter_picking_status(self, queryset, name, value):
        value = str(value).strip()
        if value == "not_started":
            return queryset.filter(lines__order_line__picking_tasks__isnull=True).distinct()
        if value == "shortage":
            return queryset.filter(lines__order_line__picking_tasks__shortage_quantity__gt=0).distinct()
        if value == "completed":
            return queryset.exclude(lines__order_line__picking_tasks__isnull=True).exclude(
                lines__order_line__picking_tasks__quantity_picked__lt=models.F("lines__order_line__picking_tasks__quantity_to_pick")
            ).distinct()
        if value == "in_progress":
            return queryset.filter(
                lines__order_line__picking_tasks__status__in=[
                    PickingTask.Status.OPEN,
                    PickingTask.Status.ASSIGNED,
                    PickingTask.Status.IN_PROGRESS,
                    PickingTask.Status.PICKED,
                ]
            ).distinct()
        return queryset

    def filter_route(self, queryset, name, value):
        route_query = models.Q(route_run__route__code__iexact=value)
        if str(value).isdigit():
            route_query |= models.Q(route_run_id=value)
        return queryset.filter(route_query)

    def filter_customer(self, queryset, name, value):
        return queryset.filter(
            models.Q(customer_name__icontains=value)
            | models.Q(customer_alias__icontains=value)
            | models.Q(external_customer_account__icontains=value)
        )

    class Meta:
        model = Shipment
        fields = [
            "branch",
            "shipment_status",
            "picking_status",
            "route",
            "delivery_date",
            "customer",
            "payment_method",
            "external_reference",
            "shipment_type",
            "document_status",
        ]


class ShipmentViewSet(ReadOnlyModelViewSet):
    queryset = Shipment.objects.select_related(
        "branch",
        "order",
        "route_run",
        "route_run__route",
        "route_run__route__branch",
        "inter_branch_transfer",
        "inter_branch_transfer__source_branch",
        "inter_branch_transfer__destination_branch",
        "activated_by",
        "prepared_by",
        "cancelled_by",
        "documents_printed_by",
        "documents_posted_by",
    ).prefetch_related(
        "lines",
        "lines__product",
        "lines__order_line",
        "lines__order_line__picking_tasks",
        "lines__order_line__picking_tasks__source_location",
        "lines__quantity_adjustments",
        "lines__quantity_adjustments__adjusted_by",
        "route_assignments",
        "route_assignments__changed_by",
        "status_history",
        "status_history__changed_by",
    )
    serializer_class = ShipmentSerializer
    filterset_class = ShipmentFilter
    search_fields = [
        "reference",
        "external_reference",
        "external_order_reference",
        "customer_name",
        "customer_alias",
        "recipient_account",
        "delivery_name",
        "external_notes",
        "order__external_reference",
        "route_run__route__code",
    ]
    ordering = ["-created_at"]
    ordering_fields = [
        "reference",
        "status",
        "delivery_date",
        "customer_alias",
        "payment_method",
        "created_at",
        "updated_at",
        "route_run__departure_time",
        "route_run__order_cutoff_time",
    ]

    def get_queryset(self):
        queryset = filter_branch_queryset(super().get_queryset(), self.request, "branch")
        branch = self.request.query_params.get("branch", "").strip()
        if branch:
            queryset = queryset.filter(models.Q(branch_id=branch) if branch.isdigit() else models.Q(branch__code__iexact=branch))
        return queryset.distinct()

    def retrieve(self, request, *args, **kwargs):
        shipment = self.get_object()
        sync_shipment_status_from_work(shipment)
        shipment.refresh_from_db()
        return Response(self.get_serializer(shipment).data)

    @action(detail=False, methods=["get"], url_path="route-targets")
    def route_targets(self, request):
        branch_value = str(request.query_params.get("branch", "")).strip()
        scope = str(request.query_params.get("scope", "today")).strip().lower()
        current_route_run_id = str(request.query_params.get("exclude_route_run", "")).strip()
        search = str(request.query_params.get("search", "")).strip()
        operational_date = parse_date(str(request.query_params.get("operational_date", "")).strip()) or timezone.localdate()
        queryset = RouteRun.objects.select_related("route", "route__branch").exclude(
            status__in=[RouteRun.Status.CLOSED, RouteRun.Status.DISPATCHED, RouteRun.Status.CANCELLED]
        )
        queryset = filter_branch_queryset(queryset, request, "route__branch")
        if branch_value:
            queryset = queryset.filter(
                models.Q(route__branch_id=branch_value) if branch_value.isdigit() else models.Q(route__branch__code__iexact=branch_value)
            )
        if current_route_run_id.isdigit():
            queryset = queryset.exclude(id=current_route_run_id)

        if scope == "week":
            week_start = operational_date - timezone.timedelta(days=operational_date.weekday())
            week_end = week_start + timezone.timedelta(days=6)
            queryset = queryset.filter(service_date__range=(week_start, week_end))
        else:
            queryset = queryset.filter(service_date=operational_date)

        if search:
            queryset = queryset.filter(
                models.Q(route__code__icontains=search)
                | models.Q(route__name__icontains=search)
                | models.Q(route__branch__code__icontains=search)
                | models.Q(operational_identifier__icontains=search)
            )

        queryset = queryset.annotate(active_shipment_count=models.Count("shipments", filter=~models.Q(shipments__status=Shipment.Status.CANCELLED)))
        data = [
            {
                "id": route_run.id,
                "target_type": "route_run",
                "route_run": route_run.id,
                "schedule": route_run.schedule_id,
                "creates_route_run": False,
                "label": operational_identifier(route_run.route, route_run.service_date, route_run.run_number),
                "operational_identifier": operational_identifier(route_run.route, route_run.service_date, route_run.run_number),
                "branch_code": route_run.route.branch.code,
                "route": route_run.route_id,
                "route_code": route_run.route.code,
                "route_name": route_run.route.name,
                "service_date": route_run.service_date,
                "weekday": route_run.service_date.strftime("%A"),
                "round_number": route_run.run_number,
                "cutoff_at": route_run.cutoff_at,
                "planned_departure_at": route_run.planned_departure_at,
                "departure_time": route_run.departure_time,
                "dispatch_wave": route_run.dispatch_wave,
                "status": route_run.status,
                "shipment_count": route_run.active_shipment_count,
            }
            for route_run in queryset.order_by("service_date", "departure_time", "route__code", "run_number")[:100]
        ]
        existing_keys = {(row["route"], row["service_date"], row["round_number"]) for row in data}
        if scope == "week":
            week_start = operational_date - timezone.timedelta(days=operational_date.weekday())
            date_range = [week_start + timezone.timedelta(days=offset) for offset in range(7)]
        else:
            date_range = [operational_date]
        schedules = RouteRoundSchedule.objects.select_related("route", "route__branch").filter(is_active=True)
        schedules = filter_branch_queryset(schedules, request, "route__branch")
        if branch_value:
            schedules = schedules.filter(models.Q(route__branch_id=branch_value) if branch_value.isdigit() else models.Q(route__branch__code__iexact=branch_value))
        if search:
            schedules = schedules.filter(
                models.Q(route__code__icontains=search)
                | models.Q(route__name__icontains=search)
                | models.Q(operational_label__icontains=search)
            )
        for day in date_range:
            for schedule in schedules.filter(weekday=day.weekday()).order_by("departure_time", "route__code", "round_number"):
                key = (schedule.route_id, day, schedule.round_number)
                if key in existing_keys:
                    continue
                identifier = operational_identifier(schedule.route, day, schedule.round_number)
                data.append(
                    {
                        "id": f"schedule-{schedule.id}-{day.isoformat()}",
                        "target_type": "schedule_slot",
                        "route_run": None,
                        "schedule": schedule.id,
                        "creates_route_run": True,
                        "label": f"{identifier} / {day} / {schedule.departure_time}",
                        "operational_identifier": identifier,
                        "branch_code": schedule.route.branch.code,
                        "route": schedule.route_id,
                        "route_code": schedule.route.code,
                        "route_name": schedule.route.name,
                        "service_date": day,
                        "weekday": day.strftime("%A"),
                        "round_number": schedule.round_number,
                        "cutoff_at": aware_datetime(day, schedule.cutoff_time),
                        "planned_departure_at": aware_datetime(day, schedule.departure_time),
                        "departure_time": schedule.departure_time,
                        "dispatch_wave": schedule.dispatch_wave,
                        "status": "scheduled",
                        "shipment_count": 0,
                    }
                )
        return Response({"results": data})

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        shipment, replayed = activate_shipment(request.user, self.get_object().id, request.data.get("client_operation_id"))
        return Response({"message": "Shipment activation replayed." if replayed else "Shipment activated.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"], url_path="post-picking-lists")
    def post_picking_lists(self, request, pk=None):
        shipment, created_count = post_picking_lists(request.user, self.get_object().id, request.data.get("client_operation_id"))
        return Response({"message": f"Picking work posted. Created {created_count} task(s).", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"])
    def prepare(self, request, pk=None):
        shipment, replayed = prepare_shipment(request.user, self.get_object().id)
        return Response({"message": "Shipment was already prepared." if replayed else "Shipment prepared.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        shipment, replayed = cancel_shipment(request.user, self.get_object().id, str(request.data.get("reason", "")))
        return Response({"message": "Shipment was already cancelled." if replayed else "Shipment cancelled.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"], url_path="print-documents")
    def print_documents(self, request, pk=None):
        shipment = print_shipment_documents(request.user, self.get_object().id, str(request.data.get("printer", "")))
        return Response({"message": "Shipment documents printed.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"], url_path="post-documents")
    def post_documents(self, request, pk=None):
        shipment, replayed = post_inter_branch_documents(request.user, self.get_object().id)
        return Response({"message": "Shipment documents already posted." if replayed else "Shipment documents posted.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["get"], url_path="picking-route-preview")
    def picking_route_preview(self, request, pk=None):
        shipment = self.get_object()
        tasks = PickingTask.objects.select_related("order_line__product", "source_location").filter(order_line__shipment_line__shipment=shipment).order_by(
            "source_location__code", "order_line__line_number"
        )
        return Response({"results": PickingTaskSerializer(tasks, many=True).data})

    @action(detail=True, methods=["post"], url_path="confirm-picking-route")
    def confirm_picking_route(self, request, pk=None):
        shipment, replayed = confirm_picking_route(request.user, self.get_object().id)
        return Response({"message": "Picking route was already confirmed." if replayed else "Picking route confirmed.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["get"], url_path="proforma-preview")
    def proforma_preview(self, request, pk=None):
        shipment = self.get_object()
        order = Order.objects.select_related("branch", "route_run", "route_run__route").prefetch_related("lines__product").get(pk=shipment.order_id)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="print-proforma")
    def print_proforma(self, request, pk=None):
        shipment = self.get_object()
        require_branch_access(request.user, shipment.branch)
        AuditLog.objects.create(
            actor=request.user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="shipment_proforma_printed",
            branch=shipment.branch,
            order=shipment.order,
            route_run=shipment.route_run,
            reference=shipment.reference,
            entity_name="Shipment",
            entity_id=str(shipment.id),
            message=f"{request.user.username} printed proforma preview for shipment {shipment.reference}.",
        )
        return Response({"message": "Proforma printed.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"], url_path="close-route")
    def close_route(self, request, pk=None):
        shipment, result = close_shipment_route(
            request.user,
            self.get_object().id,
            str(request.data.get("printer_code", "WMS-ROUTE")).strip() or "WMS-ROUTE",
        )
        return Response(
            {
                "message": (
                    f"Route {result['operational_identifier']} was already closed."
                    if result["replayed"]
                    else f"Route {result['operational_identifier']} closed. "
                    f"{result['document_count']} Shipment documents were printed."
                ),
                "shipment": self.get_serializer(shipment).data,
                **result,
            }
        )

    @action(detail=True, methods=["post"], url_path="change-route")
    def change_route(self, request, pk=None):
        route_run_id = request.data.get("route_run")
        schedule_id = request.data.get("schedule")
        if not route_run_id and not schedule_id:
            return Response({"detail": "route_run or schedule is required."}, status=status.HTTP_400_BAD_REQUEST)
        shipment = self.get_object()
        if schedule_id:
            operational_date = parse_date(str(request.data.get("operational_date", "")))
            if operational_date is None:
                return Response({"detail": "operational_date is required for schedule targets."}, status=status.HTTP_400_BAD_REQUEST)
            schedule = get_object_or_404(RouteRoundSchedule.objects.select_related("route", "route__branch"), pk=schedule_id)
            shipment, replayed = manual_change_shipment_route(
                request.user,
                shipment,
                schedule=schedule,
                operational_date=operational_date,
                client_operation_id=request.data.get("client_operation_id"),
            )
        else:
            route_run = get_object_or_404(RouteRun.objects.select_related("route", "route__branch"), pk=route_run_id)
            shipment, replayed = manual_change_shipment_route(
                request.user,
                shipment,
                route_run=route_run,
                client_operation_id=request.data.get("client_operation_id"),
            )
        return Response({"message": "Shipment already uses this route." if replayed else "Shipment route changed.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"], url_path="change-status")
    def change_status(self, request, pk=None):
        shipment = change_shipment_status(
            request.user,
            self.get_object().id,
            str(request.data.get("status", "")).strip(),
            str(request.data.get("reason", "")),
            request.data.get("client_operation_id"),
        )
        return Response({"message": "Shipment status changed.", "shipment": self.get_serializer(shipment).data})

    @action(detail=True, methods=["post"], url_path="lines/(?P<line_id>[^/.]+)/remove-quantity")
    def remove_line_quantity(self, request, pk=None, line_id=None):
        try:
            quantity = Decimal(str(request.data.get("quantity", "")))
        except Exception:
            return Response({"detail": "quantity must be a decimal value."}, status=status.HTTP_400_BAD_REQUEST)
        shipment, line, adjustment, replayed = remove_shipment_line_quantity(
            request.user,
            self.get_object().id,
            line_id,
            quantity,
            str(request.data.get("reason", "")),
            request.data.get("client_operation_id"),
        )
        shipment.refresh_from_db()
        return Response(
            {
                "message": "Quantity removal replayed." if replayed else "Shipment line quantity removed.",
                "shipment": self.get_serializer(shipment).data,
                "line_id": line.id,
                "adjustment_id": adjustment.id,
            }
        )


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
    adjustment_reason = django_filters.CharFilter(field_name="adjustment_reason")
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
            "adjustment_reason",
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
            return queryset.filter(
                models.Q(adjustment_direction=StockMovement.AdjustmentDirection.INCREASE)
                | models.Q(adjustment_direction="", destination_location__isnull=False, source_location__isnull=True)
            )
        if normalized == "decrease":
            return queryset.filter(
                models.Q(adjustment_direction=StockMovement.AdjustmentDirection.DECREASE)
                | models.Q(adjustment_direction="", source_location__isnull=False, destination_location__isnull=True)
            )
        if normalized == "unknown":
            return queryset.filter(
                models.Q(adjustment_direction="", source_location__isnull=True, destination_location__isnull=True)
                | models.Q(adjustment_direction="", source_location__isnull=False, destination_location__isnull=False)
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
        "cycle_count_line",
        "cycle_count_line__session",
        "cycle_count_recount",
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
        "adjustment_reason",
        "adjustment_note",
    ]
    ordering = ["-created_at"]
    ordering_fields = ["movement_type", "quantity", "created_at", "updated_at"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")


class StockAdjustmentViewSet(StockMovementViewSet):
    def get_queryset(self):
        return super().get_queryset().filter(movement_type=StockMovement.MovementType.ADJUSTMENT)

    def _resolve_product(self, value):
        if value in [None, ""]:
            return None
        normalized = str(value).strip()
        if normalized.isdigit():
            return Product.objects.filter(pk=normalized).first()
        return Product.objects.filter(
            models.Q(sku__iexact=normalized) | models.Q(barcode__iexact=normalized)
        ).first()

    def _resolve_location(self, value):
        if value in [None, ""]:
            return None
        normalized = str(value).strip()
        queryset = Location.objects.select_related("branch")
        if normalized.isdigit():
            return queryset.filter(pk=normalized).first()
        return queryset.filter(code__iexact=normalized).order_by("branch__code").first()

    def create(self, request):
        product = self._resolve_product(request.data.get("product"))
        location = self._resolve_location(request.data.get("location"))
        direction = str(request.data.get("direction", "")).strip().lower()
        reason = str(request.data.get("reason_code", request.data.get("adjustment_reason", ""))).strip()
        note = str(request.data.get("note", "")).strip()
        branch_value = str(request.data.get("branch", "")).strip()

        if product is None:
            return Response({"product": ["Product was not found."]}, status=status.HTTP_400_BAD_REQUEST)
        if location is None:
            return Response({"location": ["Location was not found."]}, status=status.HTTP_400_BAD_REQUEST)
        if branch_value:
            if branch_value.isdigit() and str(location.branch_id) != branch_value:
                return Response({"branch": ["Location does not belong to the requested branch."]}, status=status.HTTP_400_BAD_REQUEST)
            if not branch_value.isdigit() and location.branch.code.lower() != branch_value.lower():
                return Response({"branch": ["Location does not belong to the requested branch."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            require_branch_access(request.user, location.branch, leader_required=True)
        except Exception:
            raise

        if direction not in StockMovement.AdjustmentDirection.values:
            return Response({"direction": ["Direction must be increase or decrease."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            quantity = Decimal(str(request.data.get("quantity", "")))
        except Exception:
            return Response({"quantity": ["Quantity must be a valid number."]}, status=status.HTTP_400_BAD_REQUEST)
        if quantity <= 0:
            return Response({"quantity": ["Quantity must be greater than zero."]}, status=status.HTTP_400_BAD_REQUEST)
        if reason not in StockMovement.AdjustmentReason.values:
            return Response({"reason_code": ["Select a valid stock adjustment reason."]}, status=status.HTTP_400_BAD_REQUEST)
        if not note:
            return Response({"note": ["Explanation note is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if len(note) < 5:
            return Response({"note": ["Explanation note must be at least 5 characters."]}, status=status.HTTP_400_BAD_REQUEST)
        if reason == StockMovement.AdjustmentReason.OTHER and len(note) < 10:
            return Response({"note": ["Other adjustments require a more descriptive note."]}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            inventory_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=location.branch, location=location, product=product)
                .first()
            )
            if inventory_item is None:
                if direction == StockMovement.AdjustmentDirection.DECREASE:
                    return Response(
                        {"quantity": ["Cannot decrease stock because no inventory exists at this location."]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                inventory_item = InventoryItem.objects.create(
                    branch=location.branch,
                    location=location,
                    product=product,
                    quantity_on_hand=Decimal("0"),
                    quantity_reserved=Decimal("0"),
                )

            quantity_before = inventory_item.quantity_on_hand
            if direction == StockMovement.AdjustmentDirection.INCREASE:
                quantity_after = quantity_before + quantity
                inventory_item.quantity_on_hand = quantity_after
            else:
                quantity_after = quantity_before - quantity
                if quantity_after < 0:
                    return Response(
                        {"quantity": ["Decrease would make stock negative."]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                inventory_item.quantity_on_hand = quantity_after
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

            movement = StockMovement.objects.create(
                branch=location.branch,
                product=product,
                inventory_item=inventory_item,
                source_location=location if direction == StockMovement.AdjustmentDirection.DECREASE else None,
                destination_location=location if direction == StockMovement.AdjustmentDirection.INCREASE else None,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                quantity=quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                adjustment_direction=direction,
                adjustment_reason=reason,
                adjustment_note=note,
                performed_by=request.user,
            )
            movement.reference = f"ADJ-{location.branch.code}-{movement.id:06d}"
            movement.save(update_fields=["reference", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="stock_adjustment_created",
                branch=location.branch,
                product=product,
                quantity=quantity,
                source_location=location if direction == StockMovement.AdjustmentDirection.DECREASE else None,
                destination_location=location if direction == StockMovement.AdjustmentDirection.INCREASE else None,
                source_label=location.code if direction == StockMovement.AdjustmentDirection.DECREASE else "",
                destination_label=location.code if direction == StockMovement.AdjustmentDirection.INCREASE else "",
                reference=movement.reference,
                result=direction,
                entity_name="StockMovement",
                entity_id=str(movement.id),
                message=(
                    f"Worker {request.user.username} recorded stock adjustment {movement.reference}: "
                    f"{direction} {quantity} {product.sku} at {location.code} "
                    f"from {quantity_before} to {quantity_after}."
                ),
            )

        movement = self.get_queryset().get(pk=movement.pk)
        return Response(self.get_serializer(movement).data, status=status.HTTP_201_CREATED)


class CycleCountSessionFilter(django_filters.FilterSet):
    branch = django_filters.CharFilter(method="filter_branch")
    date_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    created_by = django_filters.CharFilter(method="filter_created_by")

    class Meta:
        model = CycleCountSession
        fields = ["branch", "status", "date_from", "date_to", "created_by"]

    def filter_branch(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(branch_id=value)
        return queryset.filter(branch__code__iexact=value)

    def filter_created_by(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(created_by_id=value)
        return queryset.filter(created_by__username__iexact=value)


class CycleCountSessionViewSet(ReadOnlyModelViewSet):
    queryset = (
        CycleCountSession.objects.select_related("branch", "created_by", "opened_by", "reviewed_by", "cancelled_by")
        .prefetch_related(
            "locations",
            "locations__location",
            "locations__started_by",
            "locations__submitted_by",
            "locations__lines",
            "locations__lines__product",
            "locations__lines__counted_by",
            "locations__lines__reconciled_by",
            "locations__lines__reconciliation_stock_movement",
            "locations__lines__recounts",
            "locations__lines__recounts__requested_by",
            "locations__lines__recounts__started_by",
            "locations__lines__recounts__counted_by",
            "locations__lines__recounts__accepted_by",
            "locations__lines__recounts__cancelled_by",
            "lines",
            "lines__product",
            "lines__location",
            "lines__counted_by",
            "lines__reconciled_by",
            "lines__reconciliation_stock_movement",
            "lines__recounts",
            "lines__recounts__requested_by",
            "lines__recounts__started_by",
            "lines__recounts__counted_by",
            "lines__recounts__accepted_by",
            "lines__recounts__cancelled_by",
            "recounts",
            "recounts__requested_by",
            "recounts__counted_by",
            "recounts__accepted_by",
            "recounts__cancelled_by",
        )
    )
    serializer_class = CycleCountSessionSerializer
    permission_classes = [IsAuthenticated]
    filterset_class = CycleCountSessionFilter
    search_fields = ["reference", "name", "note", "branch__code", "created_by__username"]
    ordering = ["-created_at"]
    ordering_fields = ["created_at", "opened_at", "submitted_at", "reviewed_at", "status"]

    def get_queryset(self):
        return filter_branch_queryset(super().get_queryset(), self.request, "branch")

    def _line_for_reconciliation(self, session, line_id):
        line = (
            CycleCountLine.objects.select_for_update(of=("self",))
            .select_related("session", "cycle_count_location", "branch", "location", "product")
            .filter(pk=line_id, session=session, branch=session.branch)
            .first()
        )
        if line is None:
            return None, Response({"detail": "Cycle count line was not found in this session."}, status=status.HTTP_404_NOT_FOUND)
        if line.cycle_count_location.status != CycleCountLocation.Status.SUBMITTED:
            return None, Response({"detail": "Only submitted cycle count locations can be reconciled."}, status=status.HTTP_400_BAD_REQUEST)
        variance = line.variance_quantity
        if variance is None:
            return None, Response({"detail": "Cycle count line has not been counted."}, status=status.HTTP_400_BAD_REQUEST)
        if variance == 0:
            if line.reconciliation_status != CycleCountLine.ReconciliationStatus.NO_VARIANCE:
                line.reconciliation_status = CycleCountLine.ReconciliationStatus.NO_VARIANCE
                line.save(update_fields=["reconciliation_status", "updated_at"])
            return None, Response({"detail": "Line has no variance to reconcile."}, status=status.HTTP_400_BAD_REQUEST)
        if line.reconciliation_status != CycleCountLine.ReconciliationStatus.PENDING_REVIEW:
            return None, Response({"detail": "Cycle count line has already been reconciled."}, status=status.HTTP_409_CONFLICT)
        if CycleCountRecount.objects.filter(
            original_line=line,
            status__in=[
                CycleCountRecount.Status.REQUESTED,
                CycleCountRecount.Status.IN_PROGRESS,
                CycleCountRecount.Status.SUBMITTED,
            ],
        ).exists():
            return None, Response({"detail": "Active recount must be completed or cancelled before reconciling this line."}, status=status.HTTP_409_CONFLICT)
        return line, None

    def _accepted_recount(self, line):
        return (
            CycleCountRecount.objects.select_for_update(of=("self",))
            .filter(original_line=line, status=CycleCountRecount.Status.ACCEPTED)
            .order_by("-accepted_at", "-updated_at")
            .first()
        )

    def _effective_values(self, line):
        recount = self._accepted_recount(line)
        if recount is not None:
            return {
                "baseline_quantity": recount.baseline_quantity,
                "baseline_at": recount.baseline_at,
                "counted_quantity": recount.counted_quantity,
                "variance": recount.variance_quantity,
                "recount": recount,
            }
        return {
            "baseline_quantity": line.expected_quantity,
            "baseline_at": line.session.snapshot_at,
            "counted_quantity": line.counted_quantity,
            "variance": line.variance_quantity,
            "recount": None,
        }

    def _movement_after_snapshot_exists(self, line):
        if not line.session.snapshot_at:
            return False
        return StockMovement.objects.filter(
            branch=line.branch,
            product=line.product,
            created_at__gt=line.session.snapshot_at,
        ).filter(
            models.Q(source_location=line.location) | models.Q(destination_location=line.location)
        ).exclude(cycle_count_line=line).exists()

    def _refresh_movement_warning(self, line):
        has_movement = self._movement_after_snapshot_exists(line)
        if has_movement and not line.movement_after_snapshot:
            line.movement_after_snapshot = True
            line.save(update_fields=["movement_after_snapshot", "updated_at"])
        return has_movement

    def _movement_after_recount_baseline_exists(self, recount):
        return StockMovement.objects.filter(
            branch=recount.branch,
            product=recount.product,
            created_at__gt=recount.baseline_at,
        ).filter(
            models.Q(source_location=recount.location) | models.Q(destination_location=recount.location)
        ).exclude(cycle_count_recount=recount).exists()

    def create(self, request):
        branch_code = str(request.data.get("branch", "")).strip()
        location_ids = request.data.get("location_ids", [])
        if not branch_code:
            return Response({"branch": ["Branch is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(location_ids, list) or not location_ids:
            return Response({"location_ids": ["At least one location is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if len(location_ids) != len(set(str(location_id) for location_id in location_ids)):
            return Response({"location_ids": ["Duplicate locations are not allowed."]}, status=status.HTTP_400_BAD_REQUEST)
        branch = Branch.objects.filter(code__iexact=branch_code).first()
        if branch is None:
            return Response({"branch": ["Branch was not found."]}, status=status.HTTP_400_BAD_REQUEST)
        require_branch_access(request.user, branch, leader_required=True)
        locations = list(Location.objects.filter(id__in=location_ids, branch=branch).order_by("code"))
        if len(locations) != len(location_ids):
            return Response({"location_ids": ["All locations must belong to the active branch."]}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            session = CycleCountSession.objects.create(
                branch=branch,
                name=str(request.data.get("name", "")).strip(),
                note=str(request.data.get("note", "")).strip(),
                created_by=request.user,
            )
            session.reference = f"CC-{branch.code}-{session.id:06d}"
            session.save(update_fields=["reference", "updated_at"])
            CycleCountLocation.objects.bulk_create(
                [CycleCountLocation(session=session, branch=branch, location=location) for location in locations]
            )
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.CREATE,
                event_type="cycle_count_created",
                branch=branch,
                reference=session.reference,
                entity_name="CycleCountSession",
                entity_id=str(session.id),
                message=f"Worker {request.user.username} created cycle count session {session.reference}.",
            )

        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="open")
    def open(self, request, pk=None):
        with transaction.atomic():
            session = (
                CycleCountSession.objects.select_for_update()
                .select_related("branch")
                .prefetch_related("locations", "locations__location")
                .get(pk=pk)
            )
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status == CycleCountSession.Status.OPEN:
                return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)
            if session.status != CycleCountSession.Status.DRAFT:
                return Response({"detail": "Only draft cycle count sessions can be opened."}, status=status.HTTP_400_BAD_REQUEST)
            count_locations = list(session.locations.select_related("location").all())
            if not count_locations:
                return Response({"detail": "Cycle count session has no locations."}, status=status.HTTP_400_BAD_REQUEST)
            snapshot_at = timezone.now()
            existing_line_count = CycleCountLine.objects.filter(session=session).count()
            if existing_line_count == 0:
                inventory_items = InventoryItem.objects.select_related("product", "location").filter(
                    branch=session.branch,
                    location_id__in=[count_location.location_id for count_location in count_locations],
                    quantity_on_hand__gt=0,
                )
                location_by_id = {count_location.location_id: count_location for count_location in count_locations}
                CycleCountLine.objects.bulk_create(
                    [
                        CycleCountLine(
                            session=session,
                            cycle_count_location=location_by_id[item.location_id],
                            branch=session.branch,
                            location=item.location,
                            product=item.product,
                            expected_quantity=item.quantity_on_hand,
                            is_expected=True,
                        )
                        for item in inventory_items
                    ]
                )
            session.status = CycleCountSession.Status.OPEN
            session.snapshot_at = snapshot_at
            session.opened_at = snapshot_at
            session.opened_by = request.user
            session.save(update_fields=["status", "snapshot_at", "opened_at", "opened_by", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                event_type="cycle_count_opened",
                branch=session.branch,
                reference=session.reference,
                entity_name="CycleCountSession",
                entity_id=str(session.id),
                message=f"Worker {request.user.username} opened cycle count session {session.reference} and created the expected stock snapshot.",
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status != CycleCountSession.Status.AWAITING_REVIEW:
                return Response({"detail": "Only sessions awaiting review can be closed."}, status=status.HTTP_400_BAD_REQUEST)
            if session.locations.exclude(status=CycleCountLocation.Status.SUBMITTED).exists():
                return Response({"detail": "All cycle count locations must be submitted before closing."}, status=status.HTTP_400_BAD_REQUEST)
            pending_lines = CycleCountLine.objects.filter(
                session=session,
                reconciliation_status=CycleCountLine.ReconciliationStatus.PENDING_REVIEW,
            ).count()
            if pending_lines:
                return Response(
                    {"detail": "All variance lines must be reconciled before closing.", "pending_variance_count": pending_lines},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            active_recounts = CycleCountRecount.objects.filter(
                session=session,
                status__in=[
                    CycleCountRecount.Status.REQUESTED,
                    CycleCountRecount.Status.IN_PROGRESS,
                    CycleCountRecount.Status.SUBMITTED,
                ],
            ).count()
            if active_recounts:
                return Response(
                    {"detail": "All recounts must be accepted or cancelled before closing.", "active_recount_count": active_recounts},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            session.status = CycleCountSession.Status.CLOSED
            session.reviewed_at = timezone.now()
            session.reviewed_by = request.user
            session.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                event_type="cycle_count_closed",
                branch=session.branch,
                reference=session.reference,
                entity_name="CycleCountSession",
                entity_id=str(session.id),
                message=f"Worker {request.user.username} closed reconciled cycle count session {session.reference}. Closing did not create additional stock mutations.",
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>[^/.]+)/apply-adjustment")
    def apply_adjustment(self, request, pk=None, line_id=None):
        note = str(request.data.get("note", "")).strip()
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status != CycleCountSession.Status.AWAITING_REVIEW:
                return Response({"detail": "Cycle count session must be awaiting review."}, status=status.HTTP_400_BAD_REQUEST)
            line, error_response = self._line_for_reconciliation(session, line_id)
            if error_response is not None:
                return error_response
            effective = self._effective_values(line)
            variance = effective["variance"]
            recount = effective["recount"]
            if variance is None or variance == 0:
                return Response({"detail": "Effective result has no variance to adjust."}, status=status.HTTP_400_BAD_REQUEST)
            if recount is None and self._refresh_movement_warning(line):
                return Response(
                    {"detail": "Inventory moved after the cycle count snapshot. Resolve this variance without automatic adjustment or recount later."},
                    status=status.HTTP_409_CONFLICT,
                )
            if recount is not None:
                if self._movement_after_recount_baseline_exists(recount):
                    recount.movement_after_baseline = True
                    recount.save(update_fields=["movement_after_baseline", "updated_at"])
                    return Response(
                        {"detail": "Inventory moved after the accepted recount baseline. Request another recount or resolve without adjustment."},
                        status=status.HTTP_409_CONFLICT,
                    )
            inventory_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=session.branch, location=line.location, product=line.product)
                .first()
            )
            if inventory_item is None:
                if effective["baseline_quantity"] != 0:
                    return Response({"detail": "Current inventory no longer matches the cycle count snapshot."}, status=status.HTTP_409_CONFLICT)
                if variance < 0:
                    return Response({"detail": "Decrease would make stock negative."}, status=status.HTTP_400_BAD_REQUEST)
                inventory_item = InventoryItem.objects.create(
                    branch=session.branch,
                    location=line.location,
                    product=line.product,
                    quantity_on_hand=Decimal("0"),
                    quantity_reserved=Decimal("0"),
                )
            quantity_before = inventory_item.quantity_on_hand
            if quantity_before != effective["baseline_quantity"]:
                if recount is not None:
                    recount.movement_after_baseline = True
                    recount.save(update_fields=["movement_after_baseline", "updated_at"])
                    detail = "Current inventory no longer matches the accepted recount baseline."
                else:
                    line.movement_after_snapshot = True
                    line.save(update_fields=["movement_after_snapshot", "updated_at"])
                    detail = "Current inventory no longer matches the cycle count snapshot."
                return Response({"detail": detail}, status=status.HTTP_409_CONFLICT)
            direction = (
                StockMovement.AdjustmentDirection.INCREASE
                if variance > 0
                else StockMovement.AdjustmentDirection.DECREASE
            )
            quantity = abs(variance)
            quantity_after = quantity_before + variance
            if quantity_after < 0:
                return Response({"detail": "Decrease would make stock negative."}, status=status.HTTP_400_BAD_REQUEST)
            inventory_item.quantity_on_hand = quantity_after
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])
            adjustment_note = (
                f"Cycle Count {session.reference}; location {line.location.code}; product {line.product.sku}; "
                f"{'recount baseline' if recount else 'expected'} {effective['baseline_quantity']}; "
                f"counted {effective['counted_quantity']}; variance {variance}."
            )
            if recount:
                adjustment_note = f"{adjustment_note} Recount {recount.reference} was accepted as effective evidence."
            if note:
                adjustment_note = f"{adjustment_note} Leader note: {note}"
            movement = StockMovement.objects.create(
                branch=session.branch,
                product=line.product,
                inventory_item=inventory_item,
                source_location=line.location if direction == StockMovement.AdjustmentDirection.DECREASE else None,
                destination_location=line.location if direction == StockMovement.AdjustmentDirection.INCREASE else None,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                quantity=quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                adjustment_direction=direction,
                adjustment_reason=StockMovement.AdjustmentReason.COUNT_CORRECTION,
                adjustment_note=adjustment_note,
                cycle_count_line=line,
                cycle_count_recount=recount,
                performed_by=request.user,
            )
            movement.reference = f"ADJ-CC-{session.branch.code}-{movement.id:06d}"
            movement.save(update_fields=["reference", "updated_at"])
            line.reconciliation_status = CycleCountLine.ReconciliationStatus.ADJUSTMENT_APPLIED
            line.reconciled_by = request.user
            line.reconciled_at = timezone.now()
            line.resolution_note = note
            line.save(update_fields=[
                "reconciliation_status",
                "reconciled_by",
                "reconciled_at",
                "resolution_note",
                "updated_at",
            ])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="cycle_count_variance_adjustment_applied",
                branch=session.branch,
                product=line.product,
                quantity=quantity,
                source_location=line.location if direction == StockMovement.AdjustmentDirection.DECREASE else None,
                destination_location=line.location if direction == StockMovement.AdjustmentDirection.INCREASE else None,
                source_label=line.location.code if direction == StockMovement.AdjustmentDirection.DECREASE else "",
                destination_label=line.location.code if direction == StockMovement.AdjustmentDirection.INCREASE else "",
                reference=movement.reference,
                result=direction,
                entity_name="CycleCountLine",
                entity_id=str(line.id),
                message=(
                    f"Worker {request.user.username} applied cycle count variance adjustment {movement.reference} "
                    f"for {session.reference}{f' using recount {recount.reference}' if recount else ''}: "
                    f"{direction} {quantity} {line.product.sku} at {line.location.code} "
                    f"from {quantity_before} to {quantity_after}."
                ),
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>[^/.]+)/resolve-without-adjustment")
    def resolve_without_adjustment(self, request, pk=None, line_id=None):
        note = str(request.data.get("note", "")).strip()
        if len(note) < 5:
            return Response({"note": ["A meaningful explanation is required."]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status != CycleCountSession.Status.AWAITING_REVIEW:
                return Response({"detail": "Cycle count session must be awaiting review."}, status=status.HTTP_400_BAD_REQUEST)
            line, error_response = self._line_for_reconciliation(session, line_id)
            if error_response is not None:
                return error_response
            self._refresh_movement_warning(line)
            variance = line.variance_quantity
            line.reconciliation_status = CycleCountLine.ReconciliationStatus.NO_ADJUSTMENT_REQUIRED
            line.reconciled_by = request.user
            line.reconciled_at = timezone.now()
            line.resolution_note = note
            line.save(update_fields=[
                "reconciliation_status",
                "reconciled_by",
                "reconciled_at",
                "resolution_note",
                "movement_after_snapshot",
                "updated_at",
            ])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="cycle_count_variance_resolved_without_adjustment",
                branch=session.branch,
                product=line.product,
                quantity=abs(variance),
                source_location=line.location,
                source_label=line.location.code,
                reference=session.reference,
                result=CycleCountLine.ReconciliationStatus.NO_ADJUSTMENT_REQUIRED,
                entity_name="CycleCountLine",
                entity_id=str(line.id),
                message=(
                    f"Worker {request.user.username} resolved cycle count variance for {session.reference} "
                    f"without stock adjustment: {line.product.sku} at {line.location.code}, variance {variance}."
                ),
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>[^/.]+)/request-recount")
    def request_recount(self, request, pk=None, line_id=None):
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 5:
            return Response({"reason": ["A meaningful recount reason is required."]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status in [CycleCountSession.Status.CLOSED, CycleCountSession.Status.CANCELLED]:
                return Response({"detail": "Closed or cancelled sessions cannot be recounted."}, status=status.HTTP_400_BAD_REQUEST)
            if session.status != CycleCountSession.Status.AWAITING_REVIEW:
                return Response({"detail": "Cycle count session must be awaiting review."}, status=status.HTTP_400_BAD_REQUEST)
            line = (
                CycleCountLine.objects.select_for_update(of=("self",))
                .select_related("cycle_count_location", "branch", "location", "product")
                .filter(pk=line_id, session=session, branch=session.branch)
                .first()
            )
            if line is None:
                return Response({"detail": "Cycle count line was not found in this session."}, status=status.HTTP_404_NOT_FOUND)
            if line.cycle_count_location.status != CycleCountLocation.Status.SUBMITTED:
                return Response({"detail": "Only submitted cycle count locations can be recounted."}, status=status.HTTP_400_BAD_REQUEST)
            if line.reconciliation_status in [
                CycleCountLine.ReconciliationStatus.ADJUSTMENT_APPLIED,
                CycleCountLine.ReconciliationStatus.NO_ADJUSTMENT_REQUIRED,
            ]:
                return Response({"detail": "Final reconciled lines cannot be recounted."}, status=status.HTTP_409_CONFLICT)
            if line.variance_quantity == 0:
                return Response({"detail": "Zero variance lines do not require recount."}, status=status.HTTP_400_BAD_REQUEST)
            if CycleCountRecount.objects.filter(
                original_line=line,
                status__in=[
                    CycleCountRecount.Status.REQUESTED,
                    CycleCountRecount.Status.IN_PROGRESS,
                    CycleCountRecount.Status.SUBMITTED,
                ],
            ).exists():
                return Response({"detail": "An active recount already exists for this line."}, status=status.HTTP_409_CONFLICT)
            now = timezone.now()
            inventory_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=session.branch, location=line.location, product=line.product)
                .first()
            )
            baseline_quantity = inventory_item.quantity_on_hand if inventory_item else Decimal("0")
            recount = CycleCountRecount.objects.create(
                original_line=line,
                session=session,
                branch=session.branch,
                location=line.location,
                product=line.product,
                reason=reason,
                requested_by=request.user,
                requested_at=now,
                baseline_quantity=baseline_quantity,
                baseline_at=now,
                movement_after_baseline=False,
            )
            recount.reference = f"CCR-{session.branch.code}-{recount.id:06d}"
            recount.save(update_fields=["reference", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.CREATE,
                event_type="cycle_count_recount_requested",
                branch=session.branch,
                product=line.product,
                source_location=line.location,
                source_label=line.location.code,
                reference=recount.reference,
                entity_name="CycleCountRecount",
                entity_id=str(recount.id),
                message=f"Worker {request.user.username} requested recount {recount.reference} for {session.reference} at {line.location.code}.",
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path=r"recounts/(?P<recount_id>[^/.]+)/accept")
    def accept_recount(self, request, pk=None, recount_id=None):
        note = str(request.data.get("note", "")).strip()
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status != CycleCountSession.Status.AWAITING_REVIEW:
                return Response({"detail": "Cycle count session must be awaiting review."}, status=status.HTTP_400_BAD_REQUEST)
            recount = (
                CycleCountRecount.objects.select_for_update(of=("self",))
                .select_related("original_line", "product", "location")
                .filter(pk=recount_id, session=session, branch=session.branch)
                .first()
            )
            if recount is None:
                return Response({"detail": "Recount was not found in this session."}, status=status.HTTP_404_NOT_FOUND)
            if recount.status != CycleCountRecount.Status.SUBMITTED:
                return Response({"detail": "Only submitted recounts can be accepted."}, status=status.HTTP_400_BAD_REQUEST)
            if self._movement_after_recount_baseline_exists(recount):
                recount.movement_after_baseline = True
                recount.save(update_fields=["movement_after_baseline", "updated_at"])
                return Response(
                    {"detail": "Inventory moved after the recount baseline. Request another recount or resolve without adjustment."},
                    status=status.HTTP_409_CONFLICT,
                )
            recount.status = CycleCountRecount.Status.ACCEPTED
            recount.accepted_by = request.user
            recount.accepted_at = timezone.now()
            recount.review_note = note
            recount.save(update_fields=["status", "accepted_by", "accepted_at", "review_note", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="cycle_count_recount_accepted",
                branch=session.branch,
                product=recount.product,
                source_location=recount.location,
                source_label=recount.location.code,
                reference=recount.reference,
                entity_name="CycleCountRecount",
                entity_id=str(recount.id),
                message=f"Worker {request.user.username} accepted recount {recount.reference} for {session.reference}.",
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @action(detail=True, methods=["post"], url_path=r"recounts/(?P<recount_id>[^/.]+)/cancel")
    def cancel_recount(self, request, pk=None, recount_id=None):
        note = str(request.data.get("note", "")).strip()
        if len(note) < 5:
            return Response({"note": ["A meaningful cancellation note is required."]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            recount = (
                CycleCountRecount.objects.select_for_update(of=("self",))
                .select_related("product", "location")
                .filter(pk=recount_id, session=session, branch=session.branch)
                .first()
            )
            if recount is None:
                return Response({"detail": "Recount was not found in this session."}, status=status.HTTP_404_NOT_FOUND)
            if recount.status not in [
                CycleCountRecount.Status.REQUESTED,
                CycleCountRecount.Status.IN_PROGRESS,
                CycleCountRecount.Status.SUBMITTED,
            ]:
                return Response({"detail": "Only active or submitted recounts can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
            recount.status = CycleCountRecount.Status.CANCELLED
            recount.cancelled_by = request.user
            recount.cancelled_at = timezone.now()
            recount.review_note = note
            recount.save(update_fields=["status", "cancelled_by", "cancelled_at", "review_note", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="cycle_count_recount_cancelled",
                branch=session.branch,
                product=recount.product,
                source_location=recount.location,
                source_label=recount.location.code,
                reference=recount.reference,
                entity_name="CycleCountRecount",
                entity_id=str(recount.id),
                message=f"Worker {request.user.username} cancelled recount {recount.reference} for {session.reference}.",
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        with transaction.atomic():
            session = CycleCountSession.objects.select_for_update().select_related("branch").get(pk=pk)
            require_branch_access(request.user, session.branch, leader_required=True)
            if session.status not in [CycleCountSession.Status.DRAFT, CycleCountSession.Status.OPEN]:
                return Response({"detail": "Only draft or open sessions can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
            session.status = CycleCountSession.Status.CANCELLED
            session.cancelled_at = timezone.now()
            session.cancelled_by = request.user
            session.locations.exclude(status=CycleCountLocation.Status.SUBMITTED).update(status=CycleCountLocation.Status.CANCELLED)
            session.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                event_type="cycle_count_cancelled",
                branch=session.branch,
                reference=session.reference,
                entity_name="CycleCountSession",
                entity_id=str(session.id),
                message=f"Worker {request.user.username} cancelled cycle count session {session.reference}.",
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)


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

    def get_object(self):
        event = super().get_object()
        if self.request.user.is_authenticated:
            allowed = {code.lower() for code in branch_codes_filter(self.request.user)}
            if not any(self._event_visible_for_branch(event, code) for code in allowed):
                raise PermissionDenied("You do not have access to this event.")
        return event

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


class InventoryExceptionSummaryViewSet(ViewSet):
    serializer_class = InventoryExceptionSummarySerializer
    permission_classes = [IsAuthenticated]

    def _leader_branch_codes(self, request, branch_codes):
        if request.user.is_superuser:
            return set(branch_codes)
        return set(
            UserBranchMembership.objects.filter(
                user=request.user,
                branch__code__in=branch_codes,
                role=UserBranchMembership.Role.LEADER,
            ).values_list("branch__code", flat=True)
        )

    def _aggregate(self, queryset, waiting_field="created_at"):
        return queryset.aggregate(
            count=models.Count("id"),
            oldest=models.Min(waiting_field),
        )

    def _category(self, *, key, label, description, queryset, statuses, owner, urgent_count=0, urgency="normal"):
        aggregate = self._aggregate(queryset)
        return {
            "key": key,
            "label": label,
            "description": description,
            "count": aggregate["count"] or 0,
            "urgent_count": urgent_count,
            "oldest_waiting_since": aggregate["oldest"],
            "available": True,
            "owner": owner,
            "urgency": urgency if urgent_count else "normal",
            "included_statuses": list(statuses),
        }

    def _top_item(self, *, key, category_key, category_label, reference, reason, status_value, waiting_since, destination, priority):
        return {
            "key": key,
            "category_key": category_key,
            "category_label": category_label,
            "reference": reference,
            "reason": reason,
            "status": status_value,
            "waiting_since": waiting_since,
            "destination": destination,
            "priority": priority,
        }

    def _cycle_count_rows(self, request, branch_codes):
        leader_codes = self._leader_branch_codes(request, branch_codes)
        if not leader_codes:
            return [], leader_codes
        builder = CycleCountReviewQueueViewSet()
        return builder._build_rows(leader_codes), leader_codes

    def _summary_response(self, request):
        requested_branch = request.query_params.get("branch", "").strip()
        branch_codes = branch_codes_filter(request.user, requested_branch)

        picking_shortage_statuses = [PickingShortage.Status.OPEN]
        transfer_discrepancy_statuses = [TransferDiscrepancy.Status.OPEN, TransferDiscrepancy.Status.INVESTIGATING]
        source_review_statuses = [
            TransferDiscrepancySourceReview.Status.PENDING_REVIEW,
            TransferDiscrepancySourceReview.Status.INVESTIGATING,
        ]
        reconciliation_statuses = [
            TransferDiscrepancyReconciliation.Status.PENDING_ACTION,
            TransferDiscrepancyReconciliation.Status.IN_PROGRESS,
            TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED,
        ]
        source_stock_statuses = [
            TransferDiscrepancySourceStockVerification.Status.PENDING_VERIFICATION,
            TransferDiscrepancySourceStockVerification.Status.INVESTIGATING,
        ]
        replenishment_statuses = [ReplenishmentRequest.Status.PENDING_ORDER]

        picking_shortages = PickingShortage.objects.filter(
            branch__code__in=branch_codes,
            status__in=picking_shortage_statuses,
        )
        discrepancies = TransferDiscrepancy.objects.filter(
            transfer__destination_branch__code__in=branch_codes,
            status__in=transfer_discrepancy_statuses,
        )
        source_reviews = TransferDiscrepancySourceReview.objects.filter(
            source_branch__code__in=branch_codes,
            status__in=source_review_statuses,
        )
        reconciliations = TransferDiscrepancyReconciliation.objects.filter(
            discrepancy__transfer__destination_branch__code__in=branch_codes,
            status__in=reconciliation_statuses,
        ) | TransferDiscrepancyReconciliation.objects.filter(
            discrepancy__transfer__source_branch__code__in=branch_codes,
            status__in=reconciliation_statuses,
        )
        source_stock = TransferDiscrepancySourceStockVerification.objects.filter(
            reconciliation__discrepancy__transfer__source_branch__code__in=branch_codes,
            status__in=source_stock_statuses,
        )
        replenishment = ReplenishmentRequest.objects.filter(
            branch__code__in=branch_codes,
            status__in=replenishment_statuses,
        )

        manual_reconciliation_count = reconciliations.filter(
            status=TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED,
        ).count()

        action_rows = filter_rows_for_user(build_transfer_discrepancy_action_queue(), request.user, requested_branch)
        action_waiting = [row.get("waiting_since") or row.get("created_at") for row in action_rows]
        action_category = {
            "key": "action_queue",
            "label": "Action Queue",
            "description": "Next transfer-discrepancy actions generated by the existing workflow queue.",
            "count": len(action_rows),
            "urgent_count": sum(1 for row in action_rows if row.get("action_type") == "record_final_reconciliation_outcome"),
            "oldest_waiting_since": min(action_waiting) if action_waiting else None,
            "available": True,
            "owner": "Transfer discrepancy workflow",
            "urgency": "high" if any(row.get("action_type") == "record_final_reconciliation_outcome" for row in action_rows) else "normal",
            "included_statuses": ["derived_open_actions"],
        }

        cycle_rows, leader_codes = self._cycle_count_rows(request, branch_codes)
        cycle_waiting = [row.get("waiting_since") for row in cycle_rows if row.get("waiting_since")]
        cycle_urgent = sum(
            1
            for row in cycle_rows
            if row.get("item_type") in ["stale_variance", "recount_waiting_review", "accepted_recount_pending_reconciliation"]
        )

        categories = [
            self._category(
                key="picking_shortages",
                label="Picking Shortages",
                description="Open scanner-reported picking shortages waiting for stock search or confirmation.",
                queryset=picking_shortages,
                statuses=picking_shortage_statuses,
                owner="Picking workflow",
            ),
            self._category(
                key="transfer_discrepancies",
                label="Transfer Discrepancies",
                description="Destination transfer discrepancies still being reviewed before source ownership is decided.",
                queryset=discrepancies,
                statuses=transfer_discrepancy_statuses,
                owner="Transfer receiving workflow",
            ),
            self._category(
                key="source_reviews",
                label="Source Reviews",
                description="Source-branch reviews for confirmed transfer shortages.",
                queryset=source_reviews,
                statuses=source_review_statuses,
                owner="Source investigation workflow",
            ),
            self._category(
                key="reconciliations",
                label="Reconciliations",
                description="Transfer discrepancy reconciliations waiting for acknowledgment, investigation or final action.",
                queryset=reconciliations.distinct(),
                statuses=reconciliation_statuses,
                owner="Reconciliation workflow",
                urgent_count=manual_reconciliation_count,
                urgency="high",
            ),
            self._category(
                key="source_stock",
                label="Source Stock",
                description="Source stock verifications still pending or under investigation.",
                queryset=source_stock,
                statuses=source_stock_statuses,
                owner="Source stock verification workflow",
            ),
            self._category(
                key="replenishment",
                label="Replenishment",
                description="Customer replenishment requests still waiting for an external order.",
                queryset=replenishment,
                statuses=replenishment_statuses,
                owner="Replenishment workflow",
            ),
            action_category,
        ]

        if leader_codes:
            categories.append({
                "key": "cycle_count_review",
                "label": "Cycle Count Review",
                "description": "Leader review items from the dedicated Cycle Count Review Queue.",
                "count": len(cycle_rows),
                "urgent_count": cycle_urgent,
                "oldest_waiting_since": min(cycle_waiting) if cycle_waiting else None,
                "available": True,
                "owner": "Cycle Count review workflow",
                "urgency": "high" if cycle_urgent else "normal",
                "included_statuses": ["review_queue_items"],
            })

        top_items = []
        for shortage in picking_shortages.select_related("product").order_by("reported_at")[:3]:
            top_items.append(self._top_item(
                key=f"picking-shortage-{shortage.id}",
                category_key="picking_shortages",
                category_label="Picking Shortages",
                reference=shortage.reference or f"Picking shortage {shortage.id}",
                reason=f"{shortage.product.sku} shortage remains open",
                status_value=shortage.status,
                waiting_since=shortage.reported_at,
                destination="/wms/picking-shortages",
                priority=40,
            ))

        for reconciliation in reconciliations.select_related("discrepancy").filter(
            status=TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED,
        ).order_by("updated_at")[:3]:
            top_items.append(self._top_item(
                key=f"reconciliation-{reconciliation.id}",
                category_key="reconciliations",
                category_label="Reconciliations",
                reference=reconciliation.reference or f"Reconciliation {reconciliation.id}",
                reason="Manual reconciliation action required",
                status_value=reconciliation.status,
                waiting_since=reconciliation.updated_at,
                destination=f"/wms/discrepancy-reconciliations/{reconciliation.id}",
                priority=20,
            ))

        for row in sorted(cycle_rows, key=lambda item: (item["priority"], item.get("waiting_since") or timezone.now()))[:4]:
            if row["item_type"] in ["stale_variance", "recount_waiting_review", "accepted_recount_pending_reconciliation"]:
                top_items.append(self._top_item(
                    key=f"cycle-count-{row['key']}",
                    category_key="cycle_count_review",
                    category_label="Cycle Count Review",
                    reference=row["session_reference"],
                    reason=row["item_type_label"],
                    status_value=row["item_type"],
                    waiting_since=row.get("waiting_since"),
                    destination=row["detail_url"],
                    priority=row["priority"],
                ))

        top_items = sorted(
            top_items,
            key=lambda item: (item["priority"], item["waiting_since"] or timezone.now()),
        )[:8]
        visible_categories = [category for category in categories if category["available"]]
        oldest_values = [category["oldest_waiting_since"] for category in visible_categories if category["oldest_waiting_since"]]
        data = {
            "total_actionable": sum(category["count"] for category in visible_categories),
            "active_categories": sum(1 for category in visible_categories if category["count"] > 0),
            "leader_only_count": len(cycle_rows),
            "oldest_waiting_since": min(oldest_values) if oldest_values else None,
            "categories": visible_categories,
            "immediate_attention": top_items,
        }
        serializer = self.serializer_class(data)
        return Response(serializer.data)

    def list(self, request):
        return self._summary_response(request)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return self._summary_response(request)


class TransportOverviewViewSet(ViewSet):
    serializer_class = TransportOverviewSerializer
    permission_classes = [IsAuthenticated]

    ACTIVE_ROUTE_STATUSES = [
        RouteRun.Status.OPEN,
        RouteRun.Status.SYNCING,
        RouteRun.Status.PICKING,
        RouteRun.Status.READY_TO_CLOSE,
    ]
    PREPARING_ROUTE_STATUSES = [
        RouteRun.Status.OPEN,
        RouteRun.Status.SYNCING,
        RouteRun.Status.PICKING,
    ]
    ACTIVE_TRANSFER_STATUSES = [
        InterBranchTransfer.Status.RELEASED,
        InterBranchTransfer.Status.IN_TRANSIT,
        InterBranchTransfer.Status.RECEIVING,
    ]
    PALLET_AWAITING_RECEIPT_STATUSES = [
        TransferPallet.Status.IN_TRANSIT,
        TransferPallet.Status.RECEIVING,
    ]
    UNRESOLVED_DISCREPANCY_STATUSES = [
        TransferDiscrepancy.Status.OPEN,
        TransferDiscrepancy.Status.INVESTIGATING,
        TransferDiscrepancy.Status.CONFIRMED_SHORTAGE,
    ]
    ACTIVE_TRANSIT_INVESTIGATION_STATUSES = [
        TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION,
        TransferDiscrepancyTransitInvestigation.Status.INVESTIGATING,
    ]

    def _branch_codes(self, request):
        requested = request.query_params.get("branch", "").strip()
        return branch_codes_filter(request.user, requested), requested

    def _route_queryset(self, branch_codes):
        return RouteRun.objects.select_related("route", "route__branch").filter(
            route__branch__code__in=branch_codes,
        )

    def _dual_branch_query(self, branch_codes, source_field, destination_field):
        return models.Q(**{f"{source_field}__code__in": branch_codes}) | models.Q(
            **{f"{destination_field}__code__in": branch_codes}
        )

    def _route_progress(self, run):
        line_count = run.line_count or 0
        if not line_count:
            return 0
        return round((run.picked_line_count / line_count) * 100, 1)

    def _active_route_row(self, run):
        return {
            "id": run.id,
            "route_code": run.route.code,
            "route_name": run.route.name,
            "branch_code": run.route.branch.code,
            "service_date": run.service_date,
            "run_number": run.run_number,
            "status": run.status,
            "order_count": run.order_count,
            "line_count": run.line_count,
            "picked_line_count": run.picked_line_count,
            "pending_line_count": run.pending_line_count,
            "progress_percent": self._route_progress(run),
            "departure_time": run.departure_time,
            "ready_at": run.ready_at,
            "documents_printed_at": run.documents_printed_at,
            "destination": f"/wms/routes-monitor",
        }

    def _attention_item(self, *, key, item_type, label, reference, status_value, waiting_since, destination, priority, source="", destination_branch=""):
        return {
            "key": key,
            "item_type": item_type,
            "label": label,
            "reference": reference,
            "source_branch_code": source,
            "destination_branch_code": destination_branch,
            "status": status_value,
            "waiting_since": waiting_since,
            "destination": destination,
            "priority": priority,
        }

    def _active_routes(self, route_queryset):
        return (
            route_queryset.filter(status__in=self.ACTIVE_ROUTE_STATUSES)
            .annotate(
                order_count=models.Count("orders", distinct=True),
                line_count=models.Count("orders__lines", distinct=True),
                picked_line_count=models.Count(
                    "orders__lines",
                    filter=models.Q(orders__lines__quantity_picked__gte=models.F("orders__lines__quantity_ordered")),
                    distinct=True,
                ),
                pending_line_count=models.Count(
                    "orders__lines",
                    filter=models.Q(orders__lines__quantity_picked__lt=models.F("orders__lines__quantity_ordered")),
                    distinct=True,
                ),
            )
            .order_by(
                models.Case(
                    models.When(status=RouteRun.Status.READY_TO_CLOSE, then=0),
                    default=1,
                    output_field=models.IntegerField(),
                ),
                "service_date",
                "departure_time",
                "route__code",
                "run_number",
            )
        )

    def _attention_items(self, branch_codes, active_routes):
        items = []
        for run in active_routes.filter(status=RouteRun.Status.READY_TO_CLOSE).order_by("service_date", "departure_time")[:5]:
            items.append(self._attention_item(
                key=f"route-ready-{run.id}",
                item_type="route_ready_to_close",
                label="Route ready to close",
                reference=f"{run.route.code} / run {run.run_number}",
                source=run.route.branch.code,
                status_value=run.status,
                waiting_since=run.ready_at or run.updated_at,
                destination="/wms/routes-monitor",
                priority=30,
            ))

        investigations = (
            TransferDiscrepancyTransitInvestigation.objects.select_related(
                "reconciliation__discrepancy__transfer",
                "reconciliation__discrepancy__transfer__source_branch",
                "reconciliation__discrepancy__transfer__destination_branch",
            )
            .filter(status__in=self.ACTIVE_TRANSIT_INVESTIGATION_STATUSES)
            .filter(self._dual_branch_query(
                branch_codes,
                "reconciliation__discrepancy__transfer__source_branch",
                "reconciliation__discrepancy__transfer__destination_branch",
            ))
            .order_by("created_at")
        )
        for investigation in investigations[:5]:
            transfer = investigation.reconciliation.discrepancy.transfer
            items.append(self._attention_item(
                key=f"transit-investigation-{investigation.id}",
                item_type="transit_investigation",
                label="Transit investigation",
                reference=investigation.reference,
                source=transfer.source_branch.code,
                destination_branch=transfer.destination_branch.code,
                status_value=investigation.status,
                waiting_since=investigation.started_at or investigation.created_at,
                destination=f"/wms/transit-investigations/{investigation.id}",
                priority=10 if investigation.status == TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION else 15,
            ))

        discrepancies = (
            TransferDiscrepancy.objects.select_related(
                "pallet",
                "transfer",
                "transfer__source_branch",
                "transfer__destination_branch",
            )
            .filter(status__in=self.UNRESOLVED_DISCREPANCY_STATUSES)
            .filter(self._dual_branch_query(branch_codes, "transfer__source_branch", "transfer__destination_branch"))
            .order_by("created_at")
        )
        for discrepancy in discrepancies[:5]:
            items.append(self._attention_item(
                key=f"transport-discrepancy-{discrepancy.id}",
                item_type="transport_discrepancy",
                label="Unresolved transport discrepancy",
                reference=discrepancy.reference,
                source=discrepancy.transfer.source_branch.code,
                destination_branch=discrepancy.transfer.destination_branch.code,
                status_value=discrepancy.status,
                waiting_since=discrepancy.updated_at or discrepancy.created_at,
                destination=f"/wms/discrepancies/{discrepancy.id}",
                priority=20,
            ))

        return sorted(items, key=lambda item: (item["priority"], item["waiting_since"] or timezone.now()))[:8]

    def _summary_response(self, request):
        branch_codes, _requested = self._branch_codes(request)
        route_queryset = self._route_queryset(branch_codes)
        active_routes = self._active_routes(route_queryset)
        transfer_branch_query = self._dual_branch_query(branch_codes, "source_branch", "destination_branch")
        discrepancy_branch_query = self._dual_branch_query(branch_codes, "transfer__source_branch", "transfer__destination_branch")
        transit_branch_query = self._dual_branch_query(
            branch_codes,
            "reconciliation__discrepancy__transfer__source_branch",
            "reconciliation__discrepancy__transfer__destination_branch",
        )

        transfers_in_transit = InterBranchTransfer.objects.filter(
            transfer_branch_query,
            status__in=self.ACTIVE_TRANSFER_STATUSES,
        )
        pallets_awaiting_receipt = TransferPallet.objects.filter(
            transfer__source_branch__code__in=branch_codes,
            status__in=self.PALLET_AWAITING_RECEIPT_STATUSES,
        ) | TransferPallet.objects.filter(
            transfer__destination_branch__code__in=branch_codes,
            status__in=self.PALLET_AWAITING_RECEIPT_STATUSES,
        )
        unresolved_discrepancies = TransferDiscrepancy.objects.filter(
            discrepancy_branch_query,
            status__in=self.UNRESOLVED_DISCREPANCY_STATUSES,
        )
        transit_investigations = TransferDiscrepancyTransitInvestigation.objects.filter(
            transit_branch_query,
            status__in=self.ACTIVE_TRANSIT_INVESTIGATION_STATUSES,
        )

        route_rows = [self._active_route_row(run) for run in active_routes[:10]]
        data = {
            "summary": {
                "active_route_runs": route_queryset.filter(status__in=self.ACTIVE_ROUTE_STATUSES).count(),
                "preparing_route_runs": route_queryset.filter(status__in=self.PREPARING_ROUTE_STATUSES).count(),
                "ready_to_close_route_runs": route_queryset.filter(status=RouteRun.Status.READY_TO_CLOSE).count(),
                "transfers_in_transit": transfers_in_transit.distinct().count(),
                "pallets_awaiting_receipt": pallets_awaiting_receipt.distinct().count(),
                "unresolved_discrepancy_transfers": unresolved_discrepancies.values("transfer_id").distinct().count(),
                "transit_investigations": transit_investigations.distinct().count(),
            },
            "active_routes": route_rows,
            "attention_items": self._attention_items(branch_codes, active_routes),
        }
        serializer = self.serializer_class(data)
        return Response(serializer.data)

    def list(self, request):
        return self._summary_response(request)


class CycleCountReviewQueueViewSet(ViewSet):
    serializer_class = CycleCountReviewQueueItemSerializer
    pagination_class = TransferDiscrepancyActionPagination
    permission_classes = [IsAuthenticated]

    ITEM_LABELS = {
        "variance_pending_review": "Variance pending review",
        "stale_variance": "Stale variance",
        "recount_requested": "Recount requested",
        "recount_in_progress": "Recount in progress",
        "recount_waiting_review": "Recount waiting for review",
        "accepted_recount_pending_reconciliation": "Accepted recount pending reconciliation",
        "session_waiting_close": "Session ready to close",
    }
    PRIORITY = {
        "stale_variance": 10,
        "recount_waiting_review": 20,
        "accepted_recount_pending_reconciliation": 30,
        "variance_pending_review": 40,
        "session_waiting_close": 50,
        "recount_requested": 60,
        "recount_in_progress": 70,
    }
    ACTIVE_RECOUNT_STATUSES = [
        CycleCountRecount.Status.REQUESTED,
        CycleCountRecount.Status.IN_PROGRESS,
        CycleCountRecount.Status.SUBMITTED,
    ]

    def _leader_branch_codes(self, request):
        requested = request.query_params.get("branch", "").strip()
        if request.user.is_superuser:
            return branch_codes_filter(request.user, requested)
        queryset = UserBranchMembership.objects.filter(user=request.user, role=UserBranchMembership.Role.LEADER)
        if requested:
            queryset = queryset.filter(branch__code__iexact=requested)
        codes = set(queryset.values_list("branch__code", flat=True))
        if requested and not codes:
            raise PermissionDenied("This queue requires a Leader role in the requested branch.")
        if not codes:
            raise PermissionDenied("This queue requires a Leader role.")
        return codes

    def _movement_after_baseline(self, branch, product, location, baseline_at, exclude_recount=None):
        if not baseline_at:
            return False
        queryset = StockMovement.objects.filter(branch=branch, product=product, created_at__gt=baseline_at).filter(
            models.Q(source_location=location) | models.Q(destination_location=location)
        )
        if exclude_recount is not None:
            queryset = queryset.exclude(cycle_count_recount=exclude_recount)
        return queryset.exists()

    def _inventory_quantities(self, lines):
        keys = {(line.branch_id, line.location_id, line.product_id) for line in lines}
        if not keys:
            return {}
        query = models.Q()
        for branch_id, location_id, product_id in keys:
            query |= models.Q(branch_id=branch_id, location_id=location_id, product_id=product_id)
        return {
            (item.branch_id, item.location_id, item.product_id): item.quantity_on_hand
            for item in InventoryItem.objects.filter(query)
        }

    def _base_line_row(self, line, item_type, waiting_since, actions, *, recount=None, effective_counted=None, effective_variance=None, is_stale=False, movement_after_baseline=False):
        return {
            "key": f"{item_type}-{line.id}-{recount.id if recount else 'line'}",
            "item_type": item_type,
            "item_type_label": self.ITEM_LABELS[item_type],
            "priority": self.PRIORITY[item_type],
            "branch": line.branch_id,
            "branch_code": line.branch.code,
            "session": line.session_id,
            "session_reference": line.session.reference,
            "session_status": line.session.status,
            "line": line.id,
            "recount": recount.id if recount else None,
            "recount_reference": recount.reference if recount else "",
            "location": line.location_id,
            "location_code": line.location.code,
            "product": line.product_id,
            "product_sku": line.product.sku,
            "product_name": line.product.name,
            "expected_quantity": str(line.expected_quantity),
            "original_counted_quantity": str(line.counted_quantity) if line.counted_quantity is not None else "",
            "effective_counted_quantity": str(effective_counted if effective_counted is not None else line.counted_quantity or ""),
            "effective_variance": str(effective_variance if effective_variance is not None else line.variance_quantity or ""),
            "movement_after_snapshot": line.movement_after_snapshot,
            "movement_after_baseline": movement_after_baseline,
            "is_stale": is_stale,
            "reconciliation_status": line.reconciliation_status,
            "recount_status": recount.status if recount else "",
            "waiting_since": waiting_since,
            "valid_actions": actions,
            "detail_url": f"/wms/cycle-counts/{line.session_id}",
        }

    def _build_rows(self, branch_codes):
        lines = list(
            CycleCountLine.objects.select_related(
                "session",
                "branch",
                "location",
                "product",
                "cycle_count_location",
            )
            .prefetch_related("recounts")
            .filter(
                branch__code__in=branch_codes,
                session__status=CycleCountSession.Status.AWAITING_REVIEW,
                cycle_count_location__status=CycleCountLocation.Status.SUBMITTED,
                reconciliation_status=CycleCountLine.ReconciliationStatus.PENDING_REVIEW,
            )
            .order_by("session__reference", "location__code", "product__sku")
        )
        inventory = self._inventory_quantities(lines)
        rows = []

        for line in lines:
            recounts = sorted(list(line.recounts.all()), key=lambda recount: recount.requested_at)
            active = [
                recount for recount in recounts
                if recount.status in self.ACTIVE_RECOUNT_STATUSES
            ]
            accepted = [
                recount for recount in recounts
                if recount.status == CycleCountRecount.Status.ACCEPTED
            ]
            if active:
                recount = active[-1]
                if recount.status == CycleCountRecount.Status.REQUESTED:
                    rows.append(self._base_line_row(line, "recount_requested", recount.requested_at, ["open_detail", "open_scanner_recount", "cancel_recount"], recount=recount))
                elif recount.status == CycleCountRecount.Status.IN_PROGRESS:
                    rows.append(self._base_line_row(line, "recount_in_progress", recount.started_at or recount.requested_at, ["open_detail", "open_scanner_recount", "cancel_recount"], recount=recount))
                else:
                    stale = recount.movement_after_baseline or self._movement_after_baseline(recount.branch, recount.product, recount.location, recount.baseline_at, recount)
                    actions = ["open_detail", "cancel_recount"]
                    if not stale:
                        actions.insert(1, "accept_recount")
                    rows.append(self._base_line_row(
                        line,
                        "recount_waiting_review",
                        recount.counted_at or recount.updated_at,
                        actions,
                        recount=recount,
                        effective_counted=recount.counted_quantity,
                        effective_variance=recount.variance_quantity,
                        is_stale=stale,
                        movement_after_baseline=stale,
                    ))
                continue

            if accepted:
                recount = sorted(accepted, key=lambda item: item.accepted_at or item.updated_at)[-1]
                current = inventory.get((line.branch_id, line.location_id, line.product_id), Decimal("0"))
                stale = recount.movement_after_baseline or current != recount.baseline_quantity
                actions = ["open_detail", "resolve_without_adjustment", "request_recount"]
                if not stale and recount.variance_quantity not in [None, 0]:
                    actions.insert(1, "apply_adjustment")
                rows.append(self._base_line_row(
                    line,
                    "accepted_recount_pending_reconciliation",
                    recount.accepted_at or recount.updated_at,
                    actions,
                    recount=recount,
                    effective_counted=recount.counted_quantity,
                    effective_variance=recount.variance_quantity,
                    is_stale=stale,
                    movement_after_baseline=stale,
                ))
                continue

            current = inventory.get((line.branch_id, line.location_id, line.product_id), Decimal("0"))
            stale = (
                line.movement_after_snapshot
                or current != line.expected_quantity
                or self._movement_after_baseline(line.branch, line.product, line.location, line.session.snapshot_at)
            )
            item_type = "stale_variance" if stale else "variance_pending_review"
            actions = ["open_detail", "resolve_without_adjustment", "request_recount"]
            if not stale:
                actions.insert(1, "apply_adjustment")
            rows.append(self._base_line_row(line, item_type, line.counted_at or line.updated_at, actions, is_stale=stale))

        session_rows = self._session_close_rows(branch_codes)
        rows.extend(session_rows)
        return rows

    def _session_close_rows(self, branch_codes):
        sessions = (
            CycleCountSession.objects.select_related("branch")
            .prefetch_related("lines", "locations", "recounts")
            .filter(branch__code__in=branch_codes, status=CycleCountSession.Status.AWAITING_REVIEW)
        )
        rows = []
        for session in sessions:
            if session.locations.exclude(status=CycleCountLocation.Status.SUBMITTED).exists():
                continue
            if session.lines.filter(reconciliation_status=CycleCountLine.ReconciliationStatus.PENDING_REVIEW).exists():
                continue
            if session.recounts.filter(status__in=self.ACTIVE_RECOUNT_STATUSES).exists():
                continue
            rows.append({
                "key": f"session-close-{session.id}",
                "item_type": "session_waiting_close",
                "item_type_label": self.ITEM_LABELS["session_waiting_close"],
                "priority": self.PRIORITY["session_waiting_close"],
                "branch": session.branch_id,
                "branch_code": session.branch.code,
                "session": session.id,
                "session_reference": session.reference,
                "session_status": session.status,
                "line": None,
                "recount": None,
                "recount_reference": "",
                "location": None,
                "location_code": "",
                "product": None,
                "product_sku": "",
                "product_name": "",
                "expected_quantity": "",
                "original_counted_quantity": "",
                "effective_counted_quantity": "",
                "effective_variance": "",
                "movement_after_snapshot": False,
                "movement_after_baseline": False,
                "is_stale": False,
                "reconciliation_status": "",
                "recount_status": "",
                "waiting_since": session.submitted_at or session.updated_at,
                "valid_actions": ["open_detail", "close_session"],
                "detail_url": f"/wms/cycle-counts/{session.id}",
            })
        return rows

    def _filter_rows(self, rows, request):
        item_type = request.query_params.get("item_type", "").strip()
        search = request.query_params.get("search", "").strip().lower()
        location = request.query_params.get("location", "").strip().lower()
        product = request.query_params.get("product", "").strip().lower()
        recount_status = request.query_params.get("recount_status", "").strip()
        reconciliation_status = request.query_params.get("reconciliation_status", "").strip()
        stale_only = request.query_params.get("stale_only", "").strip().lower() in ["1", "true", "yes"]
        date_from = parse_date(request.query_params.get("date_from", ""))
        date_to = parse_date(request.query_params.get("date_to", ""))

        if item_type:
            rows = [row for row in rows if row["item_type"] == item_type]
        if search:
            rows = [
                row for row in rows
                if any(search in str(row.get(key, "")).lower() for key in ["session_reference", "location_code", "product_sku", "product_name", "recount_reference"])
            ]
        if location:
            rows = [row for row in rows if location in row["location_code"].lower()]
        if product:
            rows = [row for row in rows if product in row["product_sku"].lower() or product in row["product_name"].lower()]
        if recount_status:
            rows = [row for row in rows if row["recount_status"] == recount_status]
        if reconciliation_status:
            rows = [row for row in rows if row["reconciliation_status"] == reconciliation_status]
        if stale_only:
            rows = [row for row in rows if row["is_stale"]]
        if date_from is not None:
            rows = [row for row in rows if row["waiting_since"].date() >= date_from]
        if date_to is not None:
            rows = [row for row in rows if row["waiting_since"].date() <= date_to]
        return rows

    def _summary(self, rows):
        return {
            "total": len(rows),
            **{item_type: sum(1 for row in rows if row["item_type"] == item_type) for item_type in self.ITEM_LABELS},
        }

    def list(self, request):
        branch_codes = self._leader_branch_codes(request)
        rows = self._filter_rows(self._build_rows(branch_codes), request)
        rows = sorted(rows, key=lambda row: (row["priority"], row["waiting_since"], row["session_reference"], row["key"]))
        summary = self._summary(rows)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request)
        serializer = self.serializer_class(page, many=True)
        response = paginator.get_paginated_response(serializer.data)
        response.data["summary"] = summary
        return response


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
                get_object_or_404(
                    TransferDiscrepancyReconciliation.objects.select_for_update().select_related(
                        "discrepancy",
                        "discrepancy__pallet",
                        "discrepancy__transfer",
                        "discrepancy__transfer__source_branch",
                        "discrepancy__transfer__destination_branch",
                        "source_review",
                    ),
                    pk=pk,
                )
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
