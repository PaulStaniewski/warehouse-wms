from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from operations.models import AuditLog, PickingTask, RouteRun, StockMovement
from operations.serializers import PickingTaskSerializer, RouteRunSerializer
from warehouse.models import InventoryItem


class ScannerPickingScanView(APIView):
    def post(self, request):
        route_run_id = request.data.get("route_run_id")
        code = str(request.data.get("code", "")).strip()

        if not route_run_id:
            return Response({"detail": "route_run_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not code:
            return Response({"detail": "Scan code is required."}, status=status.HTTP_400_BAD_REQUEST)

        route_run = get_object_or_404(RouteRun.objects.select_related("route", "route__branch"), pk=route_run_id)

        with transaction.atomic():
            matching_tasks = (
                PickingTask.objects.select_for_update()
                .select_related("branch", "order_line__order", "order_line__product", "source_location")
                .filter(order_line__order__route_run=route_run)
                .filter(
                    Q(order_line__product__sku__iexact=code)
                    | Q(order_line__product__barcode__iexact=code)
                    | Q(order_line__order__external_reference__iexact=code)
                )
                .order_by("status", "created_at", "id")
            )
            task = (
                matching_tasks.exclude(
                    status__in=[PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED],
                )
                .filter(quantity_picked__lt=F("quantity_to_pick"))
                .first()
            )

            if task is None:
                completed_match = matching_tasks.filter(status=PickingTask.Status.COMPLETED).first()
                if completed_match is not None:
                    return Response(
                        {"detail": "Matching picking task is already completed."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                return Response(
                    {"detail": "No matching open picking task found for this route run."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            order_line = task.order_line
            product = order_line.product
            scan_quantity = Decimal("1")
            task_remaining = task.quantity_to_pick - task.quantity_picked
            order_remaining = order_line.quantity_ordered - order_line.quantity_picked
            quantity_to_apply = min(scan_quantity, task_remaining, order_remaining)

            if quantity_to_apply <= 0:
                return Response({"detail": "Matching picking task is already completed."}, status=status.HTTP_400_BAD_REQUEST)

            inventory_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=task.branch, location=task.source_location, product=product)
                .first()
            )
            if inventory_item is None:
                return Response({"detail": "No inventory found at the source location."}, status=status.HTTP_400_BAD_REQUEST)

            if inventory_item.quantity_on_hand < quantity_to_apply:
                return Response({"detail": "Not enough stock at the source location."}, status=status.HTTP_400_BAD_REQUEST)

            task.quantity_picked = F("quantity_picked") + quantity_to_apply
            task.status = PickingTask.Status.IN_PROGRESS
            task.save(update_fields=["quantity_picked", "status", "updated_at"])
            task.refresh_from_db()

            if task.quantity_picked >= task.quantity_to_pick:
                task.status = PickingTask.Status.COMPLETED
                task.save(update_fields=["status", "updated_at"])
                task.refresh_from_db()

            order_line.quantity_picked = F("quantity_picked") + quantity_to_apply
            order_line.save(update_fields=["quantity_picked", "updated_at"])

            inventory_item.quantity_on_hand = F("quantity_on_hand") - quantity_to_apply
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

            StockMovement.objects.create(
                branch=task.branch,
                product=product,
                inventory_item=inventory_item,
                source_location=task.source_location,
                movement_type=StockMovement.MovementType.PICK,
                quantity=quantity_to_apply,
                reference=f"SCAN-TASK-{task.id}",
                performed_by=None,
            )
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="PickingTask",
                entity_id=str(task.id),
                message=(
                    f"Scanner picked {quantity_to_apply} of {product.sku} "
                    f"for route run {route_run.id} using code {code}."
                ),
            )

        route_run.refresh_from_db()
        return Response(
            {
                "message": "Scan accepted.",
                "task": PickingTaskSerializer(task).data,
                "route_run": RouteRunSerializer(route_run).data,
            },
            status=status.HTTP_200_OK,
        )
