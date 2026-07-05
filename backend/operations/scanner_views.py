from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from operations.models import (
    AuditLog,
    CartPickedItem,
    Order,
    PickingTask,
    RouteRun,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    StockMovement,
)
from operations.serializers import PickingTaskSerializer, RouteRunSerializer
from operations.services import TERMINAL_ROUTE_STATUSES, recalculate_route_readiness
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


def _parse_positive_quantity(value, default="1"):
    try:
        quantity = Decimal(str(value if value not in [None, ""] else default))
    except Exception:
        return None, Response({"detail": "quantity must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)

    if quantity <= 0:
        return None, Response({"detail": "quantity must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

    return quantity, None


def _get_route_run_or_response(route_run_id):
    if not route_run_id:
        return None, Response({"detail": "route_run_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    route_run = RouteRun.objects.select_related("route", "route__branch").filter(pk=route_run_id).first()
    if route_run is None:
        return None, Response({"detail": "Route run not found."}, status=status.HTTP_404_NOT_FOUND)

    return route_run, None


def _session_data(session: ScannerSession):
    return {
        "id": session.id,
        "cart": session.cart_id,
        "cart_code": session.cart.code,
        "cart_name": session.cart.name,
        "worker_code": session.worker_code,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


def _get_active_session_or_response(session_id):
    if not session_id:
        return None, Response({"detail": "session_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    session = ScannerSession.objects.select_related("cart").filter(pk=session_id).first()
    if session is None:
        return None, Response({"detail": "Scanner session not found."}, status=status.HTTP_404_NOT_FOUND)

    if session.status != ScannerSession.Status.ACTIVE:
        return None, Response({"detail": "Scanner session is not active."}, status=status.HTTP_400_BAD_REQUEST)

    return session, None


def _cart_item_data(item: CartPickedItem):
    order = item.picking_task.order_line.order
    remaining = item.quantity_picked - item.quantity_prepared
    return {
        "id": item.id,
        "session": item.session_id,
        "cart_code": item.cart.code,
        "route_run": item.route_run_id,
        "route_code": item.route_run.route.code,
        "picking_task": item.picking_task_id,
        "product": item.product_id,
        "product_sku": item.product.sku,
        "product_barcode": item.product.barcode,
        "product_name": item.product.name,
        "order_reference": order.external_reference,
        "customer_name": order.customer_name,
        "quantity_picked": str(item.quantity_picked),
        "quantity_prepared": str(item.quantity_prepared),
        "remaining_quantity": str(remaining),
    }


def _pick_from_shelf(request, allow_legacy_without_session=False):
    route_run, error = _get_route_run_or_response(request.data.get("route_run_id"))
    if error is not None:
        return error
    if route_run.status in TERMINAL_ROUTE_STATUSES or route_run.status == RouteRun.Status.READY_TO_CLOSE:
        return Response({"detail": "Route run is not open for picking."}, status=status.HTTP_400_BAD_REQUEST)

    session, error = _get_active_session_or_response(request.data.get("session_id"))
    if error is not None and not allow_legacy_without_session:
        return error
    if error is not None:
        session = None

    code = str(request.data.get("code", "")).strip()
    if not code:
        return Response({"detail": "Scan code is required."}, status=status.HTTP_400_BAD_REQUEST)

    quantity, error = _parse_positive_quantity(request.data.get("quantity", 1))
    if error is not None:
        return error

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
            matching_tasks.exclude(status__in=[PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED])
            .filter(quantity_picked__lt=F("quantity_to_pick"))
            .first()
        )

        if task is None:
            completed_match = matching_tasks.filter(status=PickingTask.Status.COMPLETED).first()
            if completed_match is not None:
                return Response(
                    {"detail": "Matching picking task is already prepared."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {"detail": "No matching open picking task found for this route run."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order_line = task.order_line
        product = order_line.product
        task_remaining = task.quantity_to_pick - task.quantity_picked
        order_remaining = order_line.quantity_ordered - order_line.quantity_picked

        if quantity > task_remaining or quantity > order_remaining:
            return Response(
                {"detail": "Picking this quantity would exceed the required quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inventory_item = (
            InventoryItem.objects.select_for_update()
            .filter(branch=task.branch, location=task.source_location, product=product)
            .first()
        )
        if inventory_item is None:
            return Response({"detail": "No inventory found at the source location."}, status=status.HTTP_400_BAD_REQUEST)

        if inventory_item.quantity_on_hand < quantity:
            return Response({"detail": "Not enough stock at the source location."}, status=status.HTTP_400_BAD_REQUEST)

        task.quantity_picked = F("quantity_picked") + quantity
        task.status = PickingTask.Status.IN_PROGRESS
        task.save(update_fields=["quantity_picked", "status", "updated_at"])
        task.refresh_from_db()

        if task.quantity_picked >= task.quantity_to_pick:
            task.status = PickingTask.Status.PICKED
            task.save(update_fields=["status", "updated_at"])
            task.refresh_from_db()

        order_line.quantity_picked = F("quantity_picked") + quantity
        order_line.save(update_fields=["quantity_picked", "updated_at"])

        inventory_item.quantity_on_hand = F("quantity_on_hand") - quantity
        inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

        StockMovement.objects.create(
            branch=task.branch,
            product=product,
            inventory_item=inventory_item,
            source_location=task.source_location,
            movement_type=StockMovement.MovementType.PICK,
            quantity=quantity,
            reference=f"SCAN-TASK-{task.id}",
            performed_by=None,
        )
        if session is not None:
            cart_item, _ = CartPickedItem.objects.select_for_update().get_or_create(
                session=session,
                cart=session.cart,
                route_run=route_run,
                picking_task=task,
                product=product,
                defaults={"quantity_picked": Decimal("0"), "quantity_prepared": Decimal("0")},
            )
            cart_item.quantity_picked = F("quantity_picked") + quantity
            cart_item.save(update_fields=["quantity_picked", "updated_at"])
            cart_item.refresh_from_db()

        AuditLog.objects.create(
            action_type=AuditLog.ActionType.UPDATE,
            entity_name="PickingTask",
            entity_id=str(task.id),
            message=(
                f"Scanner picking picked {quantity} of {product.sku} "
                f"for route run {route_run.id} and order {order_line.order.external_reference}"
                + (
                    f" to cart {session.cart.code} by {session.worker_code or 'scanner'}."
                    if session is not None
                    else "."
                )
            ),
        )

    route_run.refresh_from_db()
    return Response(
        {
            "message": "Pick scan accepted.",
            "task": PickingTaskSerializer(task).data,
            "route_run": RouteRunSerializer(route_run).data,
        },
        status=status.HTTP_200_OK,
    )


def _prepare_for_order(request):
    session, error = _get_active_session_or_response(request.data.get("session_id"))
    if error is not None:
        return error

    order_reference = str(request.data.get("order_reference") or request.data.get("code") or "").strip()
    product_code = str(request.data.get("product_code", "")).strip()
    if not order_reference:
        return Response({"detail": "Order/proforma code is required."}, status=status.HTTP_400_BAD_REQUEST)
    if not product_code:
        return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)

    quantity, error = _parse_positive_quantity(request.data.get("quantity", 1))
    if error is not None:
        return error

    with transaction.atomic():
        order = Order.objects.filter(external_reference__iexact=order_reference).first()
        if order is None:
            return Response({"detail": "Order/proforma not found."}, status=status.HTTP_404_NOT_FOUND)

        product = _find_product_by_code(product_code)
        if product is None:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        label = ScannerCustomerLabel.objects.filter(session=session, order=order).first()
        if label is None:
            return Response(
                {"detail": "Customer label must be printed before preparing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_item = (
            CartPickedItem.objects.select_for_update()
            .select_related("picking_task__order_line__order", "picking_task__order_line__product", "product", "route_run__route")
            .filter(
                session=session,
                product=product,
                picking_task__order_line__order=order,
                quantity_prepared__lt=F("quantity_picked"),
            )
            .order_by("created_at", "id")
            .first()
        )
        if cart_item is None:
            return Response(
                {"detail": "Product is not available on the active cart for this order."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if cart_item.route_run.status in TERMINAL_ROUTE_STATUSES:
            return Response({"detail": "Route run is closed and cannot be controlled."}, status=status.HTTP_400_BAD_REQUEST)

        available_to_prepare = cart_item.quantity_picked - cart_item.quantity_prepared
        if quantity > available_to_prepare:
            return Response(
                {"detail": "Preparing this quantity would exceed picked quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_item.quantity_prepared = F("quantity_prepared") + quantity
        cart_item.save(update_fields=["quantity_prepared", "updated_at"])
        cart_item.refresh_from_db()

        task = cart_item.picking_task
        task.quantity_prepared = F("quantity_prepared") + quantity
        task.save(update_fields=["quantity_prepared", "updated_at"])
        task.refresh_from_db()

        if task.quantity_prepared >= task.quantity_to_pick:
            task.status = PickingTask.Status.COMPLETED
        elif task.quantity_picked >= task.quantity_to_pick:
            task.status = PickingTask.Status.PICKED
        else:
            task.status = PickingTask.Status.IN_PROGRESS
        task.save(update_fields=["status", "updated_at"])
        task.refresh_from_db()

        AuditLog.objects.create(
            action_type=AuditLog.ActionType.UPDATE,
            entity_name="PickingTask",
            entity_id=str(task.id),
            message=(
                f"Scanner picking prepared {quantity} of {product.sku} from cart {session.cart.code} "
                f"for order {order.external_reference} by {session.worker_code or 'scanner'}."
            ),
        )

    recalculate_route_readiness(cart_item.route_run)
    cart_item.route_run.refresh_from_db()
    return Response(
        {
            "message": "Prepare scan accepted.",
            "task": PickingTaskSerializer(task).data,
            "route_run": RouteRunSerializer(cart_item.route_run).data,
            "cart_item": _cart_item_data(cart_item),
        },
        status=status.HTTP_200_OK,
    )


class ScannerPickingScanView(APIView):
    def post(self, request):
        return _pick_from_shelf(request, allow_legacy_without_session=True)


class ScannerPickingPickView(APIView):
    def post(self, request):
        return _pick_from_shelf(request)


class ScannerPickingPrepareView(APIView):
    def post(self, request):
        return _prepare_for_order(request)


class ScannerSessionStartView(APIView):
    def post(self, request):
        cart_code = str(request.data.get("cart_code", "")).strip()
        worker_code = str(request.data.get("worker_code", "")).strip()

        if not cart_code:
            return Response({"detail": "cart_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            cart, _ = ScannerCart.objects.select_for_update().get_or_create(
                code=cart_code,
                defaults={"name": cart_code, "status": ScannerCart.Status.AVAILABLE},
            )
            active_session = ScannerSession.objects.filter(cart=cart, status=ScannerSession.Status.ACTIVE).first()
            if active_session is not None:
                if worker_code and active_session.worker_code != worker_code:
                    active_session.worker_code = worker_code
                    active_session.save(update_fields=["worker_code", "updated_at"])
                session = active_session
            else:
                cart.status = ScannerCart.Status.IN_USE
                cart.save(update_fields=["status", "updated_at"])
                session = ScannerSession.objects.create(cart=cart, worker_code=worker_code)

            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="ScannerSession",
                entity_id=str(session.id),
                message=f"Scanner session started for cart {cart.code} by {worker_code or 'scanner'}.",
            )

        return Response({"message": "Scanner session started.", "session": _session_data(session)})


class ScannerSessionCurrentView(APIView):
    def get(self, request):
        session, error = _get_active_session_or_response(request.query_params.get("session_id"))
        if error is not None:
            return error
        return Response({"session": _session_data(session)})


class ScannerSessionEndView(APIView):
    def post(self, request):
        session, error = _get_active_session_or_response(request.data.get("session_id"))
        if error is not None:
            return error

        with transaction.atomic():
            session.status = ScannerSession.Status.CLOSED
            session.ended_at = timezone.now()
            session.save(update_fields=["status", "ended_at", "updated_at"])
            session.cart.status = ScannerCart.Status.AVAILABLE
            session.cart.save(update_fields=["status", "updated_at"])
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="ScannerSession",
                entity_id=str(session.id),
                message=f"Scanner session ended for cart {session.cart.code} by {session.worker_code or 'scanner'}.",
            )

        return Response({"message": "Scanner session ended.", "session": _session_data(session)})


class ScannerControlCartItemsView(APIView):
    def get(self, request):
        session, error = _get_active_session_or_response(request.query_params.get("session_id"))
        if error is not None:
            return error

        items = (
            CartPickedItem.objects.select_related(
                "cart",
                "product",
                "route_run__route",
                "picking_task__order_line__order",
            )
            .filter(session=session, quantity_picked__gt=F("quantity_prepared"))
            .order_by("created_at", "id")
        )
        return Response({"session": _session_data(session), "items": [_cart_item_data(item) for item in items]})


class ScannerControlTargetView(APIView):
    def get(self, request):
        session, error = _get_active_session_or_response(request.query_params.get("session_id"))
        if error is not None:
            return error

        product_code = str(request.query_params.get("product_code", "")).strip()
        if not product_code:
            return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        product = _find_product_by_code(product_code)
        if product is None:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        items = (
            CartPickedItem.objects.select_related(
                "cart",
                "product",
                "route_run__route",
                "picking_task__order_line__order",
            )
            .filter(session=session, product=product, quantity_picked__gt=F("quantity_prepared"))
            .order_by("created_at", "id")
        )
        if not items:
            return Response({"detail": "Product is not available on the active cart."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"product_sku": product.sku, "candidates": [_cart_item_data(item) for item in items]})


class ScannerControlPrintLabelView(APIView):
    def post(self, request):
        session, error = _get_active_session_or_response(request.data.get("session_id"))
        if error is not None:
            return error

        order_reference = str(request.data.get("order_reference", "")).strip()
        printer_code = str(request.data.get("printer_code", "")).strip()
        if not order_reference:
            return Response({"detail": "order_reference is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not printer_code:
            return Response({"detail": "printer_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        order = Order.objects.filter(external_reference__iexact=order_reference).first()
        if order is None:
            return Response({"detail": "Order/proforma not found."}, status=status.HTTP_404_NOT_FOUND)

        label, created = ScannerCustomerLabel.objects.get_or_create(
            session=session,
            order=order,
            defaults={"printer_code": printer_code},
        )
        if not created and label.printer_code != printer_code:
            label.printer_code = printer_code
            label.save(update_fields=["printer_code", "updated_at"])

        AuditLog.objects.create(
            action_type=AuditLog.ActionType.UPDATE,
            entity_name="ScannerCustomerLabel",
            entity_id=str(label.id),
            message=(
                f"Customer label printed for order {order.external_reference} on printer {printer_code} "
                f"from cart {session.cart.code} by {session.worker_code or 'scanner'}."
            ),
        )

        return Response(
            {
                "message": "Customer label ready.",
                "label": {
                    "id": label.id,
                    "order_reference": order.external_reference,
                    "printer_code": label.printer_code,
                    "printed_at": label.printed_at.isoformat(),
                },
            }
        )


class ScannerControlFinishView(APIView):
    def post(self, request):
        session, error = _get_active_session_or_response(request.data.get("session_id"))
        if error is not None:
            return error

        remaining_exists = CartPickedItem.objects.filter(
            session=session,
            quantity_picked__gt=F("quantity_prepared"),
        ).exists()
        if remaining_exists:
            return Response(
                {"detail": "Cannot finish control while unprepared cart items remain."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            session.status = ScannerSession.Status.CLOSED
            session.ended_at = timezone.now()
            session.save(update_fields=["status", "ended_at", "updated_at"])
            session.cart.status = ScannerCart.Status.AVAILABLE
            session.cart.save(update_fields=["status", "updated_at"])
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="ScannerSession",
                entity_id=str(session.id),
                message=f"Control finished and cart {session.cart.code} released by {session.worker_code or 'scanner'}.",
            )

        return Response({"message": "Control finished. Cart released.", "session": _session_data(session)})


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
