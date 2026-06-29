from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from operations.models import AuditLog, PickingTask, RouteRun, StockMovement
from operations.serializers import PickingTaskSerializer, RouteRunSerializer
from warehouse.models import InventoryItem, Location, Product


def _find_product_by_code(code: str):
    return Product.objects.filter(Q(sku__iexact=code) | Q(barcode__iexact=code)).first()


def _find_location_by_code(code: str):
    return Location.objects.select_related("branch").filter(code__iexact=code).order_by("branch__code").first()


def _inventory_position_data(item: InventoryItem):
    return {
        "id": item.id,
        "branch": item.branch_id,
        "branch_code": item.branch.code,
        "location": item.location_id,
        "location_code": item.location.code,
        "location_name": item.location.name,
        "product": item.product_id,
        "product_sku": item.product.sku,
        "product_barcode": item.product.barcode,
        "product_name": item.product.name,
        "quantity_on_hand": str(item.quantity_on_hand),
        "quantity_reserved": str(item.quantity_reserved),
    }


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


class ScannerProductLookupView(APIView):
    def get(self, request):
        code = str(request.query_params.get("code", "")).strip()

        if not code:
            return Response({"detail": "code query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        product = _find_product_by_code(code)
        if product is None:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        inventory_items = (
            InventoryItem.objects.select_related("branch", "location", "product")
            .filter(product=product, quantity_on_hand__gt=0)
            .order_by("branch__code", "location__code")
        )

        return Response(
            {
                "product": {
                    "id": product.id,
                    "sku": product.sku,
                    "barcode": product.barcode,
                    "name": product.name,
                    "description": None,
                    "image_url": None,
                    "unit_of_measure": product.unit_of_measure,
                },
                "inventory_positions": [_inventory_position_data(item) for item in inventory_items],
            },
            status=status.HTTP_200_OK,
        )


class ScannerLocationContentsView(APIView):
    def get(self, request):
        code = str(request.query_params.get("code", "")).strip()

        if not code:
            return Response({"detail": "code query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        location = _find_location_by_code(code)
        if location is None:
            return Response({"detail": "Location not found."}, status=status.HTTP_404_NOT_FOUND)

        inventory_items = (
            InventoryItem.objects.select_related("branch", "location", "product")
            .filter(location=location, quantity_on_hand__gt=0)
            .order_by("product__sku")
        )

        return Response(
            {
                "location": {
                    "id": location.id,
                    "branch": location.branch_id,
                    "branch_code": location.branch.code,
                    "code": location.code,
                    "name": location.name,
                    "location_type": location.location_type,
                },
                "inventory_items": [_inventory_position_data(item) for item in inventory_items],
            },
            status=status.HTTP_200_OK,
        )


class ScannerQuickTransferView(APIView):
    def post(self, request):
        source_location_code = str(request.data.get("source_location_code", "")).strip()
        product_code = str(request.data.get("product_code", "")).strip()
        target_location_code = str(request.data.get("target_location_code", "")).strip()
        quantity_value = request.data.get("quantity", 1)

        if not source_location_code:
            return Response({"detail": "source_location_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not product_code:
            return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not target_location_code:
            return Response({"detail": "target_location_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            quantity = Decimal(str(quantity_value))
        except Exception:
            return Response({"detail": "quantity must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)

        if quantity <= 0:
            return Response({"detail": "quantity must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            source_location = _find_location_by_code(source_location_code)
            if source_location is None:
                return Response({"detail": "Source location not found."}, status=status.HTTP_404_NOT_FOUND)

            target_location = _find_location_by_code(target_location_code)
            if target_location is None:
                return Response({"detail": "Target location not found."}, status=status.HTTP_404_NOT_FOUND)

            if source_location.id == target_location.id:
                return Response(
                    {"detail": "Source and target location cannot be the same."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            product = _find_product_by_code(product_code)
            if product is None:
                return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

            source_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=source_location.branch, location=source_location, product=product)
                .first()
            )
            if source_item is None:
                return Response(
                    {"detail": "Product is not available on the source location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if source_item.quantity_on_hand < quantity:
                return Response({"detail": "Insufficient quantity on source location."}, status=status.HTTP_400_BAD_REQUEST)

            target_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=target_location.branch,
                location=target_location,
                product=product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )

            source_item.quantity_on_hand = F("quantity_on_hand") - quantity
            source_item.save(update_fields=["quantity_on_hand", "updated_at"])

            target_item.quantity_on_hand = F("quantity_on_hand") + quantity
            target_item.save(update_fields=["quantity_on_hand", "updated_at"])

            movement = StockMovement.objects.create(
                branch=source_location.branch,
                product=product,
                inventory_item=source_item,
                source_location=source_location,
                destination_location=target_location,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=quantity,
                reference=f"SCANNER-TRANSFER-{source_location.code}-{target_location.code}",
                performed_by=None,
            )
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="StockMovement",
                entity_id=str(movement.id),
                message=(
                    f"Scanner quick transfer moved {quantity} {product.sku} "
                    f"from {source_location.code} to {target_location.code}."
                ),
            )

            source_item.refresh_from_db()
            target_item.refresh_from_db()

        return Response(
            {
                "message": "Quick transfer completed.",
                "movement_id": movement.id,
                "source_inventory": _inventory_position_data(source_item),
                "target_inventory": _inventory_position_data(target_item),
            },
            status=status.HTTP_200_OK,
        )
