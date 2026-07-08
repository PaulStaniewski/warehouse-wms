from decimal import Decimal

import django_filters
from django.shortcuts import get_object_or_404
from django.db import models, transaction
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
    TransferDiscrepancy,
    TransferDiscrepancyManualReconciliationDecision,
    TransferDiscrepancyReconciliation,
    TransferDiscrepancyRecovery,
    TransferDiscrepancyShortageConfirmation,
    TransferDiscrepancySourceStockRecovery,
    TransferDiscrepancySourceStockVerification,
    TransferDiscrepancySourceStockVerificationItem,
    TransferDiscrepancySourceReview,
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
    TransferDiscrepancyReconciliationSerializer,
    TransferDiscrepancySerializer,
    TransferDiscrepancySourceStockVerificationSerializer,
    TransferDiscrepancySourceReviewSerializer,
)
from operations.services import is_route_late, is_route_work_fully_prepared, recalculate_route_readiness
from operations.services import (
    DiscrepancyLocationMissing,
    complete_source_verification_if_finished,
    discrepancy_line_remaining,
    ensure_reconciliation_for_source_review,
    ensure_source_stock_verification_for_reconciliation,
    finalize_discrepancy_if_complete,
    get_discrepancy_investigation_totals,
    get_source_verification_totals,
    get_discrepancy_location,
    source_verification_item_remaining,
)
from warehouse.models import InventoryItem, Location, Product


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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
        if not printer_code:
            return Response({"detail": "printer_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            discrepancy = (
                TransferDiscrepancy.objects.select_for_update()
                .select_related("pallet", "transfer", "transfer__destination_branch")
                .get(pk=pk)
            )
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
                        performed_by=None,
                    )
                    posted_quantity += unposted

                discrepancy.report_printed_at = now
                discrepancy.shortage_posted_at = now
                discrepancy.status = TransferDiscrepancy.Status.INVESTIGATING
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.UPDATE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=f"Worker {worker_code} printed discrepancy report {discrepancy.reference} on printer {printer_code}.",
                )
                if posted_quantity:
                    AuditLog.objects.create(
                        action_type=AuditLog.ActionType.UPDATE,
                        entity_name="TransferDiscrepancy",
                        entity_id=str(discrepancy.id),
                        message=(
                            f"{posted_quantity} missing unit from discrepancy {discrepancy.reference} "
                            f"posted to location {unconfirmed_location.code}."
                        ),
                    )
            else:
                AuditLog.objects.create(
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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
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

            destination_location = Location.objects.select_related("branch").filter(code__iexact=destination_location_code).first()
            if destination_location is None:
                return Response({"detail": "Destination location not found."}, status=status.HTTP_404_NOT_FOUND)
            if destination_location.branch_id != discrepancy.transfer.destination_branch_id:
                return Response(
                    {"detail": "Destination location belongs to another branch."},
                    status=status.HTTP_400_BAD_REQUEST,
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
                performed_by=None,
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

            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancy",
                entity_id=str(discrepancy.id),
                message=(
                    f"Worker {worker_code} recovered {quantity} unit of {product.sku} from discrepancy "
                    f"{discrepancy.reference} and moved it from {unconfirmed_location.code} to {destination_location.code}."
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
                        f"{totals['recovered']} recovered, {totals['confirmed_shortage']} confirmed missing."
                    ),
                )

            recovery.refresh_from_db()

        return Response({"message": "Recovered item recorded.", "recovery": self._recovery_response(recovery)})

    @action(detail=True, methods=["post"], url_path="confirm-shortage")
    def confirm_shortage(self, request, pk=None):
        product_code = str(request.data.get("product_code", "")).strip()
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
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
                performed_by=None,
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

            unit_word = "unit" if quantity == 1 else "units"
            remove_word = "it" if quantity == 1 else "them"
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancy",
                entity_id=str(discrepancy.id),
                message=(
                    f"Worker {worker_code} confirmed {quantity} {unit_word} of {product.sku} as missing for "
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
                        f"{totals['recovered']} recovered, {totals['confirmed_shortage']} confirmed missing."
                    ),
                )

            confirmation.refresh_from_db()

        return Response(
            {"message": "Shortage confirmation recorded.", "confirmation": self._shortage_confirmation_response(confirmation)}
        )


class TransferDiscrepancySourceReviewFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = TransferDiscrepancySourceReview
        fields = ["status", "source_branch", "discrepancy"]

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

    def _validate_confirmed_shortage_discrepancy(self, review):
        if review.discrepancy.status != TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
            return Response(
                {"detail": "Source review requires a final confirmed-shortage discrepancy."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @action(detail=True, methods=["post"], url_path="begin")
    def begin(self, request, pk=None):
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
        with transaction.atomic():
            review = (
                TransferDiscrepancySourceReview.objects.select_for_update()
                .select_related("discrepancy")
                .get(pk=pk)
            )
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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
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
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = TransferDiscrepancyReconciliation
        fields = ["status", "route", "source_branch", "destination_branch"]

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

    @action(detail=True, methods=["post"], url_path="acknowledge")
    def acknowledge(self, request, pk=None):
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
        with transaction.atomic():
            reconciliation = (
                TransferDiscrepancyReconciliation.objects.select_for_update()
                .select_related("discrepancy", "source_review")
                .get(pk=pk)
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
                return Response(
                    {
                        "message": "Reconciliation case already acknowledged.",
                        "reconciliation": self.get_serializer(reconciliation).data,
                    }
                )

            reconciliation.status = TransferDiscrepancyReconciliation.Status.IN_PROGRESS
            reconciliation.acknowledged_at = timezone.now()
            reconciliation.acknowledged_by_worker_code = worker_code
            reconciliation.save(
                update_fields=["status", "acknowledged_at", "acknowledged_by_worker_code", "updated_at"]
            )
            verification, verification_created = ensure_source_stock_verification_for_reconciliation(reconciliation)
            AuditLog.objects.create(
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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
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
            if reconciliation.route == TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION:
                return Response(
                    {"detail": "This reconciliation requires transit investigation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            verification = None
            if reconciliation.route == TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION:
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
            elif reconciliation.route == TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION:
                if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
                    return Response(
                        {"detail": "Manual reconciliation must be in progress before completion."},
                        status=status.HTTP_400_BAD_REQUEST,
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
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = TransferDiscrepancySourceStockVerification
        fields = ["status", "source_branch"]

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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
        with transaction.atomic():
            verification = (
                TransferDiscrepancySourceStockVerification.objects.select_for_update()
                .select_related("reconciliation")
                .get(pk=pk)
            )
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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
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
            destination_location = Location.objects.select_related("branch").filter(code__iexact=destination_location_code).first()
            if destination_location is None:
                return Response({"detail": "Source location not found."}, status=status.HTTP_404_NOT_FOUND)
            if destination_location.branch_id != source_branch.id:
                return Response({"detail": "Source location belongs to another branch."}, status=status.HTTP_400_BAD_REQUEST)
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
                performed_by=None,
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

            unit_word = "unit" if quantity == 1 else "units"
            restore_word = "it" if quantity == 1 else "them"
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferDiscrepancySourceStockVerification",
                entity_id=str(verification.id),
                message=(
                    f"Worker {worker_code} found {quantity} {unit_word} of {product.sku} at source location "
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
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
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

            unit_word = "unit" if unresolved_quantity == 1 else "units"
            remain_word = "remains" if unresolved_quantity == 1 else "remain"
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancySourceStockVerification",
                entity_id=str(verification.id),
                message=(
                    f"Worker {worker_code} completed source stock verification {verification.reference} "
                    f"with {unresolved_quantity} {unit_word} unresolved."
                ),
            )
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferDiscrepancyReconciliation",
                entity_id=str(reconciliation.id),
                message=(
                    f"Reconciliation {reconciliation.reference} now requires manual action because "
                    f"{unresolved_quantity} source-verification {unit_word} {remain_word} unresolved."
                ),
            )
            verification.refresh_from_db()

        return Response(
            {
                "message": "Source search completed with unresolved stock.",
                "verification": self.get_serializer(verification).data,
            }
        )
