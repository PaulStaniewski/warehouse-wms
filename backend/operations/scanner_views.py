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
    CartWorkSession,
    InterBranchTransfer,
    Order,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingJobTask,
    PickingTask,
    RouteRun,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    StockMovement,
    TransferDiscrepancy,
    TransferDiscrepancyItem,
    TransferPallet,
    TransferPalletItem,
)
from operations.contents import ContentsLookupError, resolve_contents_code
from operations.serializers import PickingTaskSerializer, RouteRunSerializer
from operations.services import (
    TERMINAL_ROUTE_STATUSES,
    discrepancy_line_remaining,
    get_discrepancy_investigation_totals,
    recalculate_route_readiness,
)
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


def _parse_positive_piece_quantity(value, default="1"):
    raw_value = str(value if value not in [None, ""] else default).strip()
    if not raw_value.isdigit():
        return None, Response({"detail": "Quantity must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)

    quantity = Decimal(raw_value)
    if quantity <= 0:
        return None, Response({"detail": "Quantity must be at least 1."}, status=status.HTTP_400_BAD_REQUEST)

    return quantity, None


def _get_route_run_or_response(route_run_id):
    if not route_run_id:
        return None, Response({"detail": "route_run_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    route_run = RouteRun.objects.select_related("route", "route__branch").filter(pk=route_run_id).first()
    if route_run is None:
        return None, Response({"detail": "Route run not found."}, status=status.HTTP_404_NOT_FOUND)

    return route_run, None


def _session_data(session: ScannerSession):
    cart_work_session = getattr(session, "cart_work_session", None)
    return {
        "id": session.id,
        "cart": session.cart_id,
        "cart_code": session.cart.code,
        "cart_name": session.cart.name,
        "cart_work_session": cart_work_session.id if cart_work_session else None,
        "picking_job": cart_work_session.picking_job_id if cart_work_session else None,
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
    label = ScannerCustomerLabel.objects.filter(session=item.session, order=order).first()
    return {
        "id": item.id,
        "session": item.session_id,
        "cart_work_session": item.cart_work_session_id,
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
        "customer_label_ready": label is not None,
        "customer_label_scan_code": label.scan_code if label else None,
    }


def _task_remaining(task: PickingTask):
    return task.quantity_to_pick - task.quantity_picked


def _job_tasks(picking_job: PickingJob):
    return PickingTask.objects.select_related(
        "branch",
        "order_line__order__route_run__route",
        "order_line__product",
        "source_location",
    ).filter(job_task__picking_job=picking_job)


def _job_summary(picking_job: PickingJob):
    tasks = list(_job_tasks(picking_job))
    routes = [
        {
            "id": route.id,
            "route_code": route.route.code,
            "route_name": route.route.name,
            "branch_code": route.route.branch.code,
            "run_number": route.run_number,
            "departure_time": route.departure_time.isoformat(),
        }
        for route in picking_job.route_runs.select_related("route", "route__branch").order_by("route__code", "run_number")
    ]
    total_quantity = sum((task.quantity_to_pick for task in tasks), Decimal("0"))
    picked_quantity = sum((task.quantity_picked for task in tasks), Decimal("0"))
    prepared_quantity = sum((task.quantity_prepared for task in tasks), Decimal("0"))
    remaining_lines = sum(task.quantity_picked < task.quantity_to_pick for task in tasks)
    progress = round(float((picked_quantity / total_quantity) * 100), 1) if total_quantity > 0 else 0
    active_work = picking_job.cart_work_sessions.select_related("cart").filter(
        status__in=[CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL]
    ).first()

    return {
        "id": picking_job.id,
        "status": picking_job.status,
        "mode": picking_job.mode,
        "routes": routes,
        "total_lines": len(tasks),
        "remaining_lines": remaining_lines,
        "total_quantity": str(total_quantity),
        "picked_quantity": str(picked_quantity),
        "prepared_quantity": str(prepared_quantity),
        "progress_percent": progress,
        "assigned_cart_code": active_work.cart.code if active_work else None,
        "started_at": picking_job.started_at.isoformat() if picking_job.started_at else None,
        "completed_at": picking_job.completed_at.isoformat() if picking_job.completed_at else None,
        "created_at": picking_job.created_at.isoformat(),
    }


def _cart_work_session_data(cart_work_session: CartWorkSession):
    picking_job = cart_work_session.picking_job
    return {
        "id": cart_work_session.id,
        "cart": cart_work_session.cart_id,
        "cart_code": cart_work_session.cart.code,
        "picking_job": _job_summary(picking_job),
        "scanner_session": _session_data(cart_work_session.scanner_session) if cart_work_session.scanner_session else None,
        "confirmed_location": cart_work_session.confirmed_location_id,
        "confirmed_location_code": cart_work_session.confirmed_location.code if cart_work_session.confirmed_location else None,
        "status": cart_work_session.status,
        "started_at": cart_work_session.started_at.isoformat(),
        "finished_at": cart_work_session.finished_at.isoformat() if cart_work_session.finished_at else None,
    }


def _current_pick_task_queryset(picking_job: PickingJob):
    return (
        PickingTask.objects.select_related(
            "branch",
            "order_line__order__route_run",
            "order_line__product",
            "source_location",
        )
        .filter(job_task__picking_job=picking_job)
        .exclude(status__in=[PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED])
        .filter(quantity_picked__lt=F("quantity_to_pick"))
        .order_by("source_location__code", "created_at", "id")
    )


def _pick_instruction_data(task: PickingTask | None):
    if task is None:
        return None

    product = task.order_line.product
    return {
        "picking_task_id": task.id,
        "route_run_id": task.order_line.order.route_run_id,
        "location": {
            "id": task.source_location_id,
            "code": task.source_location.code,
            "name": task.source_location.name,
        },
        "product": {
            "id": product.id,
            "sku": product.sku,
            "barcode": product.barcode,
            "name": product.name,
        },
        "required_quantity": str(task.quantity_to_pick),
        "picked_quantity": str(task.quantity_picked),
        "remaining_quantity": str(task.quantity_to_pick - task.quantity_picked),
    }


def _current_pick_instruction(cart_work_session: CartWorkSession):
    return _pick_instruction_data(_current_pick_task_queryset(cart_work_session.picking_job).first())


def _picking_state(cart_work_session: CartWorkSession):
    instruction = _current_pick_instruction(cart_work_session)
    confirmed_code = None
    state = "completed"
    if instruction is not None:
        confirmed_location = cart_work_session.confirmed_location
        confirmed_code = confirmed_location.code if confirmed_location else None
        state = (
            "waiting_for_product"
            if confirmed_location is not None and confirmed_location.id == instruction["location"]["id"]
            else "waiting_for_location"
        )
    return state, confirmed_code, instruction


def _piece_value(value):
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def _pallet_item_data(item: TransferPalletItem):
    remaining = item.expected_quantity - item.received_quantity
    return {
        "id": item.id,
        "product": item.product_id,
        "product_sku": item.product.sku,
        "product_barcode": item.product.barcode,
        "product_name": item.product.name,
        "expected_quantity": _piece_value(item.expected_quantity),
        "received_quantity": _piece_value(item.received_quantity),
        "remaining_quantity": _piece_value(remaining),
    }


def _receiving_session_state(session: PalletReceivingSession):
    return "waiting_for_location" if session.current_pallet_item_id and session.pending_quantity else "waiting_for_product"


def _discrepancy_item_data(item: TransferDiscrepancyItem):
    return {
        "id": item.id,
        "product": item.product_id,
        "product_sku": item.product.sku,
        "product_name": item.product.name,
        "discrepancy_type": item.discrepancy_type,
        "expected_quantity": _piece_value(item.expected_quantity),
        "received_quantity": _piece_value(item.received_quantity),
        "difference_quantity": _piece_value(item.difference_quantity),
        "discrepancy_quantity": _piece_value(item.discrepancy_quantity),
        "posted_to_unconfirmed_quantity": _piece_value(item.posted_to_unconfirmed_quantity),
        "recovered_quantity": _piece_value(item.recovered_quantity),
        "confirmed_shortage_quantity": _piece_value(item.confirmed_shortage_quantity),
        "remaining_quantity": _piece_value(discrepancy_line_remaining(item)),
    }


def _discrepancy_data(discrepancy: TransferDiscrepancy | None):
    if discrepancy is None:
        return None
    items = list(discrepancy.items.select_related("product").order_by("product__sku"))
    totals = get_discrepancy_investigation_totals(discrepancy)
    return {
        "id": discrepancy.id,
        "reference": discrepancy.reference,
        "status": discrepancy.status,
        "report_printed_at": discrepancy.report_printed_at.isoformat() if discrepancy.report_printed_at else None,
        "report_print_count": discrepancy.report_print_count,
        "last_report_printer_code": discrepancy.last_report_printer_code,
        "shortage_posted_at": discrepancy.shortage_posted_at.isoformat() if discrepancy.shortage_posted_at else None,
        "line_count": len(items),
        "total_discrepancy_quantity": _piece_value(sum((item.discrepancy_quantity for item in items), Decimal("0"))),
        "total_posted_to_unconfirmed_quantity": _piece_value(totals["posted"]),
        "total_recovered_quantity": _piece_value(totals["recovered"]),
        "total_confirmed_shortage_quantity": _piece_value(totals["confirmed_shortage"]),
        "total_remaining_quantity": _piece_value(totals["remaining"]),
        "items": [_discrepancy_item_data(item) for item in items],
    }


def _receiving_session_data(session: PalletReceivingSession):
    pallet = session.pallet
    transfer = pallet.transfer
    items = list(
        pallet.items.select_related("product").order_by("product__sku")
    )
    total_expected = sum((item.expected_quantity for item in items), Decimal("0"))
    total_received = sum((item.received_quantity for item in items), Decimal("0"))
    pending_item = session.current_pallet_item
    discrepancy = getattr(pallet, "discrepancy", None)
    pending = (
        {
            "pallet_item": pending_item.id,
            "product_sku": pending_item.product.sku,
            "product_name": pending_item.product.name,
            "quantity": _piece_value(session.pending_quantity),
        }
        if pending_item and session.pending_quantity
        else None
    )
    return {
        "id": session.id,
        "status": session.status,
        "worker_code": session.worker_code,
        "state": _receiving_session_state(session),
        "session_id": session.id,
        "pallet": {
            "id": pallet.id,
            "scan_code": pallet.scan_code,
            "status": pallet.status,
            "source_branch_code": transfer.source_branch.code,
            "destination_branch_code": transfer.destination_branch.code,
            "transfer_reference": transfer.reference,
        },
        "summary": {
            "lines": len(items),
            "expected_quantity": _piece_value(total_expected),
            "received_quantity": _piece_value(total_received),
            "remaining_quantity": _piece_value(total_expected - total_received),
        },
        "current_item": pending,
        "pending_quantity": _piece_value(session.pending_quantity) if session.pending_quantity else None,
        "pending": pending,
        "discrepancy": _discrepancy_data(discrepancy),
        "manifest": [_pallet_item_data(item) for item in items],
    }


def _get_active_receiving_session_or_response(session_id):
    if not session_id:
        return None, Response({"detail": "receiving_session_id is required."}, status=status.HTTP_400_BAD_REQUEST)
    session = (
        PalletReceivingSession.objects.select_related(
            "pallet",
            "pallet__transfer",
            "pallet__transfer__source_branch",
            "pallet__transfer__destination_branch",
            "current_pallet_item",
            "current_pallet_item__product",
        )
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        return None, Response({"detail": "Receiving session not found."}, status=status.HTTP_404_NOT_FOUND)
    if session.status != PalletReceivingSession.Status.ACTIVE:
        return None, Response({"detail": "Receiving session is not active."}, status=status.HTTP_400_BAD_REQUEST)
    return session, None


def _pallet_is_closed(pallet: TransferPallet):
    return pallet.status in [
        TransferPallet.Status.RECEIVED,
        TransferPallet.Status.CLOSED_WITH_DISCREPANCY,
        TransferPallet.Status.CANCELLED,
    ]


def _update_transfer_after_pallet_close(transfer: InterBranchTransfer):
    pallets = list(transfer.pallets.all())
    if not pallets:
        return
    terminal_statuses = {TransferPallet.Status.RECEIVED, TransferPallet.Status.CLOSED_WITH_DISCREPANCY}
    if all(pallet.status in terminal_statuses for pallet in pallets):
        transfer.status = (
            InterBranchTransfer.Status.CLOSED_WITH_DISCREPANCY
            if any(pallet.status == TransferPallet.Status.CLOSED_WITH_DISCREPANCY for pallet in pallets)
            else InterBranchTransfer.Status.RECEIVED
        )
        transfer.completed_at = timezone.now()
        transfer.save(update_fields=["status", "completed_at", "updated_at"])


def _close_receiving_session(session_id):
    with transaction.atomic():
        session, error = _get_active_receiving_session_or_response(session_id)
        if error is not None:
            return error
        session = (
            PalletReceivingSession.objects.select_for_update(of=("self",))
            .select_related("pallet", "pallet__transfer")
            .get(pk=session.id)
        )
        pallet = TransferPallet.objects.select_for_update().select_related("transfer").get(pk=session.pallet_id)

        if _pallet_is_closed(pallet):
            return Response({"detail": "Pallet is already closed."}, status=status.HTTP_400_BAD_REQUEST)
        if session.current_pallet_item_id or session.pending_quantity:
            return Response(
                {"detail": "Finish or cancel the pending put-away before closing the pallet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items = list(
            TransferPalletItem.objects.select_for_update()
            .select_related("product")
            .filter(pallet=pallet)
            .order_by("product__sku")
        )
        shortages = []
        for item in items:
            difference = item.received_quantity - item.expected_quantity
            if difference < 0:
                shortages.append((item, difference))

        now = timezone.now()
        session.status = PalletReceivingSession.Status.COMPLETED
        session.completed_at = now
        session.save(update_fields=["status", "completed_at", "updated_at"])

        discrepancy = None
        if shortages:
            pallet.status = TransferPallet.Status.CLOSED_WITH_DISCREPANCY
            pallet.received_at = now
            pallet.save(update_fields=["status", "received_at", "updated_at"])

            discrepancy, created = TransferDiscrepancy.objects.get_or_create(
                pallet=pallet,
                defaults={
                    "reference": f"DIS-{pallet.id:08d}",
                    "transfer": pallet.transfer,
                    "status": TransferDiscrepancy.Status.OPEN,
                    "created_by_worker_code": session.worker_code,
                },
            )
            if not created and discrepancy.transfer_id != pallet.transfer_id:
                discrepancy.transfer = pallet.transfer
                discrepancy.save(update_fields=["transfer", "updated_at"])

            for item, difference in shortages:
                TransferDiscrepancyItem.objects.update_or_create(
                    discrepancy=discrepancy,
                    pallet_item=item,
                    defaults={
                        "product": item.product,
                        "discrepancy_type": TransferDiscrepancyItem.DiscrepancyType.SHORTAGE,
                        "expected_quantity": item.expected_quantity,
                        "received_quantity": item.received_quantity,
                        "difference_quantity": difference,
                        "discrepancy_quantity": abs(difference),
                    },
                )

            total_missing = sum((abs(difference) for _, difference in shortages), Decimal("0"))
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferPallet",
                entity_id=str(pallet.id),
                message=(
                    f"Worker {session.worker_code or 'scanner'} closed pallet {pallet.scan_code} "
                    f"with discrepancies: {_piece_value(total_missing)} missing unit across {len(shortages)} line."
                ),
            )
            if created:
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.CREATE,
                    entity_name="TransferDiscrepancy",
                    entity_id=str(discrepancy.id),
                    message=f"Discrepancy {discrepancy.reference} created for pallet {pallet.scan_code}.",
                )
        else:
            pallet.status = TransferPallet.Status.RECEIVED
            pallet.received_at = now
            pallet.save(update_fields=["status", "received_at", "updated_at"])
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferPallet",
                entity_id=str(pallet.id),
                message=(
                    f"Worker {session.worker_code or 'scanner'} closed pallet {pallet.scan_code} "
                    "with exact manifest match."
                ),
            )

        _update_transfer_after_pallet_close(pallet.transfer)

    session.refresh_from_db()
    return Response(
        {
            "message": "Pallet closed with discrepancy." if shortages else "Pallet received.",
            "result": "discrepancy" if shortages else "exact",
            "receiving_session": _receiving_session_data(session),
        }
    )


def _get_active_cart_work_or_response(cart_work_session_id):
    if not cart_work_session_id:
        return None, Response({"detail": "cart_work_session_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    cart_work_session = (
        CartWorkSession.objects.select_related("cart", "picking_job", "scanner_session")
        .filter(pk=cart_work_session_id)
        .first()
    )
    if cart_work_session is None:
        return None, Response({"detail": "Cart work session not found."}, status=status.HTTP_404_NOT_FOUND)
    if cart_work_session.status not in [CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL]:
        return None, Response({"detail": "Cart work session is not active."}, status=status.HTTP_400_BAD_REQUEST)
    if cart_work_session.picking_job.status in [PickingJob.Status.COMPLETED, PickingJob.Status.CANCELLED]:
        return None, Response({"detail": "Picking job is completed."}, status=status.HTTP_400_BAD_REQUEST)

    return cart_work_session, None


def _pick_for_cart_work(request):
    cart_work_session_id = request.data.get("cart_work_session_id")
    product_code = str(request.data.get("product_code") or request.data.get("code") or "").strip()
    if not product_code:
        return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)

    quantity, error = _parse_positive_piece_quantity(request.data.get("quantity", 1))
    if error is not None:
        return error

    worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"

    with transaction.atomic():
        cart_work_session = (
            CartWorkSession.objects.select_for_update(of=("self",))
            .select_related("cart", "picking_job", "scanner_session")
            .filter(pk=cart_work_session_id)
            .first()
        )
        if cart_work_session is None:
            return Response({"detail": "Cart work session not found."}, status=status.HTTP_404_NOT_FOUND)
        if cart_work_session.status not in [CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL]:
            return Response({"detail": "Cart work session is not active."}, status=status.HTTP_400_BAD_REQUEST)

        picking_job = cart_work_session.picking_job
        task = _current_pick_task_queryset(picking_job).select_for_update(of=("self",)).first()
        if task is None:
            return Response({"detail": "Picking job has no remaining work."}, status=status.HTTP_400_BAD_REQUEST)

        if cart_work_session.confirmed_location_id is None:
            return Response(
                {"detail": "Scan the expected location before scanning the product."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if cart_work_session.confirmed_location_id != task.source_location_id:
            return Response(
                {"detail": f"Wrong location. Go to {task.source_location.code}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order_line = task.order_line
        product = order_line.product
        if product_code.lower() not in {product.sku.lower(), (product.barcode or "").lower()}:
            return Response({"detail": f"Wrong product. Expected {product.sku}."}, status=status.HTTP_400_BAD_REQUEST)

        task_remaining = task.quantity_to_pick - task.quantity_picked
        order_remaining = order_line.quantity_ordered - order_line.quantity_picked
        if quantity > task_remaining or quantity > order_remaining:
            return Response(
                {"detail": "Quantity exceeds remaining picking quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inventory_item = (
            InventoryItem.objects.select_for_update()
            .filter(branch=task.branch, location=cart_work_session.confirmed_location, product=product)
            .first()
        )
        if inventory_item is None:
            return Response({"detail": "No inventory found at the source location."}, status=status.HTTP_400_BAD_REQUEST)
        if inventory_item.quantity_on_hand < quantity:
            return Response(
                {"detail": "Not enough stock at the confirmed location."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            reference=f"JOB-{picking_job.id}-TASK-{task.id}",
            performed_by=None,
        )

        cart_item, _ = CartPickedItem.objects.select_for_update().get_or_create(
            session=cart_work_session.scanner_session,
            cart_work_session=cart_work_session,
            cart=cart_work_session.cart,
            route_run=order_line.order.route_run,
            picking_task=task,
            product=product,
            defaults={"quantity_picked": Decimal("0"), "quantity_prepared": Decimal("0")},
        )
        cart_item.quantity_picked = F("quantity_picked") + quantity
        cart_item.save(update_fields=["quantity_picked", "updated_at"])
        cart_item.refresh_from_db()

        if not _job_tasks(picking_job).filter(quantity_picked__lt=F("quantity_to_pick")).exists():
            picking_job.status = PickingJob.Status.PICKED
            picking_job.save(update_fields=["status", "updated_at"])

        next_task = _current_pick_task_queryset(picking_job).first()
        if next_task is None or next_task.source_location_id != cart_work_session.confirmed_location_id:
            cart_work_session.confirmed_location = None
            cart_work_session.save(update_fields=["confirmed_location", "updated_at"])
            cart_work_session.refresh_from_db()

        AuditLog.objects.create(
            action_type=AuditLog.ActionType.UPDATE,
            entity_name="PickingJob",
            entity_id=str(picking_job.id),
            message=(
                f"Worker {worker_code} picked {quantity} of {product.sku} "
                f"from location {task.source_location.code} to cart {cart_work_session.cart.code} "
                f"for picking job {picking_job.id}."
            ),
        )

    cart_work_session.refresh_from_db()
    state, confirmed_location_code, instruction = _picking_state(cart_work_session)
    return Response(
        {
            "message": "Pick scan accepted.",
            "task": PickingTaskSerializer(task).data,
            "picking_job": _job_summary(picking_job),
            "cart_work_session": _cart_work_session_data(cart_work_session),
            "state": state,
            "confirmed_location_code": confirmed_location_code,
            "current_instruction": instruction,
            "cart_item": _cart_item_data(cart_item),
        },
        status=status.HTTP_200_OK,
    )


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

    quantity, error = _parse_positive_piece_quantity(request.data.get("quantity", 1))
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

    quantity, error = _parse_positive_piece_quantity(request.data.get("quantity", 1))
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
        if request.data.get("cart_work_session_id"):
            return _pick_for_cart_work(request)
        return _pick_from_shelf(request)


class ScannerPickingConfirmLocationView(APIView):
    def post(self, request):
        location_code = str(request.data.get("location_code", "")).strip()
        if not location_code:
            return Response({"detail": "location_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            cart_work_session = (
                CartWorkSession.objects.select_for_update(of=("self",))
                .select_related("cart", "picking_job", "scanner_session", "confirmed_location")
                .filter(pk=request.data.get("cart_work_session_id"))
                .first()
            )
            if cart_work_session is None:
                return Response({"detail": "Cart work session not found."}, status=status.HTTP_404_NOT_FOUND)
            if cart_work_session.status not in [CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL]:
                return Response({"detail": "Cart work session is not active."}, status=status.HTTP_400_BAD_REQUEST)

            scanned_location = _find_location_by_code(location_code)
            if scanned_location is None:
                return Response({"detail": "Unknown location."}, status=status.HTTP_404_NOT_FOUND)

            task = _current_pick_task_queryset(cart_work_session.picking_job).first()
            if task is None:
                cart_work_session.confirmed_location = None
                cart_work_session.save(update_fields=["confirmed_location", "updated_at"])
                state, confirmed_location_code, instruction = _picking_state(cart_work_session)
                return Response(
                    {
                        "message": "Picking completed.",
                        "state": state,
                        "confirmed_location_code": confirmed_location_code,
                        "cart_work_session": _cart_work_session_data(cart_work_session),
                        "current_instruction": instruction,
                    },
                    status=status.HTTP_200_OK,
                )

            if scanned_location.id != task.source_location_id:
                return Response(
                    {"detail": f"Wrong location. Go to {task.source_location.code}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            cart_work_session.confirmed_location = scanned_location
            cart_work_session.save(update_fields=["confirmed_location", "updated_at"])
            cart_work_session.refresh_from_db()
            state, confirmed_location_code, instruction = _picking_state(cart_work_session)

        return Response(
            {
                "message": "Location confirmed.",
                "state": state,
                "confirmed_location_code": confirmed_location_code,
                "cart_work_session": _cart_work_session_data(cart_work_session),
                "current_instruction": instruction,
            },
            status=status.HTTP_200_OK,
        )


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
            cart_work_session = getattr(session, "cart_work_session", None)
            session.status = ScannerSession.Status.CLOSED
            session.ended_at = timezone.now()
            session.save(update_fields=["status", "ended_at", "updated_at"])
            if cart_work_session is not None:
                cart_work_session.status = CartWorkSession.Status.CANCELLED
                cart_work_session.finished_at = timezone.now()
                cart_work_session.save(update_fields=["status", "finished_at", "updated_at"])
            session.cart.status = ScannerCart.Status.AVAILABLE
            session.cart.save(update_fields=["status", "updated_at"])
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="ScannerSession",
                entity_id=str(session.id),
                message=f"Scanner session ended for cart {session.cart.code} by {session.worker_code or 'scanner'}.",
            )

        return Response({"message": "Scanner session ended.", "session": _session_data(session)})


def _route_proforma_data(route_run: RouteRun):
    tasks = list(
        PickingTask.objects.filter(order_line__order__route_run=route_run)
        .select_related("order_line")
        .order_by("created_at")
    )
    available_tasks = [task for task in tasks if not hasattr(task, "job_task") and task.status not in [PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED]]
    started_tasks = [task for task in tasks if hasattr(task, "job_task")]
    return {
        "id": route_run.id,
        "route_code": route_run.route.code,
        "route_name": route_run.route.name,
        "branch": route_run.route.branch_id,
        "branch_code": route_run.route.branch.code,
        "run_number": route_run.run_number,
        "status": route_run.status,
        "departure_time": route_run.departure_time.isoformat(),
        "akt": len(available_tasks),
        "lines": sum(task.quantity_picked < task.quantity_to_pick for task in available_tasks),
        "started": len(started_tasks),
        "picked": sum(task.quantity_picked >= task.quantity_to_pick for task in tasks),
        "prepared": sum(task.quantity_prepared >= task.quantity_to_pick for task in tasks),
        "is_selectable": bool(available_tasks) and route_run.status not in TERMINAL_ROUTE_STATUSES,
    }


class ScannerProformasView(APIView):
    def get(self, request):
        branch = request.query_params.get("branch")
        route_runs = RouteRun.objects.select_related("route", "route__branch").exclude(status__in=TERMINAL_ROUTE_STATUSES)
        if branch:
            route_runs = route_runs.filter(route__branch_id=branch)
        route_runs = route_runs.order_by("service_date", "departure_time", "route__code", "run_number")
        return Response({"results": [_route_proforma_data(route_run) for route_run in route_runs]})


class ScannerProformasCreateJobsView(APIView):
    def post(self, request):
        route_run_ids = request.data.get("route_run_ids") or []
        mode = str(request.data.get("mode", "")).strip()
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"

        if not route_run_ids:
            return Response({"detail": "route_run_ids is required."}, status=status.HTTP_400_BAD_REQUEST)
        if mode not in [PickingJob.Mode.MERGED, PickingJob.Mode.SEPARATE]:
            return Response({"detail": "mode must be merged or separate."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            route_runs = list(
                RouteRun.objects.select_for_update()
                .select_related("route", "route__branch")
                .filter(id__in=route_run_ids)
                .order_by("id")
            )
            if len(route_runs) != len(set(route_run_ids)):
                return Response({"detail": "One or more route runs were not found."}, status=status.HTTP_404_NOT_FOUND)
            if any(route_run.status in TERMINAL_ROUTE_STATUSES for route_run in route_runs):
                return Response({"detail": "Closed or cancelled routes cannot create picking jobs."}, status=status.HTTP_400_BAD_REQUEST)

            created_jobs = []
            route_groups = [route_runs] if mode == PickingJob.Mode.MERGED else [[route_run] for route_run in route_runs]
            for group in route_groups:
                reserved_task_ids = PickingJobTask.objects.filter(
                    picking_task__order_line__order__route_run__in=group
                ).values_list("picking_task_id", flat=True)
                tasks = list(
                    PickingTask.objects.select_for_update()
                    .filter(order_line__order__route_run__in=group)
                    .exclude(status__in=[PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED])
                    .filter(quantity_picked__lt=F("quantity_to_pick"))
                    .exclude(id__in=reserved_task_ids)
                    .order_by("id")
                )
                if not tasks:
                    return Response(
                        {"detail": "Selected route work is no longer available."},
                        status=status.HTTP_409_CONFLICT,
                    )

                picking_job = PickingJob.objects.create(status=PickingJob.Status.AVAILABLE, mode=mode)
                picking_job.route_runs.add(*group)
                PickingJobTask.objects.bulk_create(
                    [PickingJobTask(picking_job=picking_job, picking_task=task) for task in tasks]
                )
                created_jobs.append(picking_job)

                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.CREATE,
                    entity_name="PickingJob",
                    entity_id=str(picking_job.id),
                    message=(
                        f"PickingJob {picking_job.id} created in {mode} mode by {worker_code}. "
                        f"Routes: {', '.join(str(route_run.id) for route_run in group)}."
                    ),
                )

        return Response(
            {"message": "Picking jobs created.", "jobs": [_job_summary(job) for job in created_jobs]},
            status=status.HTTP_201_CREATED,
        )


class ScannerTasksView(APIView):
    def get(self, request):
        jobs = (
            PickingJob.objects.prefetch_related("route_runs", "route_runs__route", "route_runs__route__branch")
            .exclude(status__in=[PickingJob.Status.COMPLETED, PickingJob.Status.CANCELLED])
            .order_by("status", "created_at")
        )
        return Response({"results": [_job_summary(job) for job in jobs]})


class ScannerTaskStartView(APIView):
    def post(self, request, job_id):
        cart_code = str(request.data.get("cart_code", "")).strip()
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
        if not cart_code:
            return Response({"detail": "cart_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            picking_job = PickingJob.objects.select_for_update().filter(pk=job_id).first()
            if picking_job is None:
                return Response({"detail": "Picking job not found."}, status=status.HTTP_404_NOT_FOUND)
            if picking_job.status != PickingJob.Status.AVAILABLE:
                return Response({"detail": "Picking job is not available."}, status=status.HTTP_409_CONFLICT)

            cart, _ = ScannerCart.objects.select_for_update().get_or_create(
                code=cart_code,
                defaults={"name": cart_code, "status": ScannerCart.Status.AVAILABLE},
            )
            if CartWorkSession.objects.filter(
                cart=cart,
                status__in=[CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL],
            ).exists():
                return Response({"detail": "Cart already has active work."}, status=status.HTTP_409_CONFLICT)

            if CartWorkSession.objects.filter(
                picking_job=picking_job,
                status__in=[CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL],
            ).exists():
                return Response({"detail": "Picking job is already assigned to a cart."}, status=status.HTTP_409_CONFLICT)

            session = ScannerSession.objects.create(cart=cart, worker_code=worker_code)
            cart.status = ScannerCart.Status.IN_USE
            cart.save(update_fields=["status", "updated_at"])
            picking_job.status = PickingJob.Status.IN_PROGRESS
            picking_job.started_at = timezone.now()
            picking_job.save(update_fields=["status", "started_at", "updated_at"])
            cart_work_session = CartWorkSession.objects.create(
                cart=cart,
                picking_job=picking_job,
                scanner_session=session,
            )

            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="PickingJob",
                entity_id=str(picking_job.id),
                message=f"PickingJob {picking_job.id} assigned to cart {cart.code} by {worker_code}.",
            )

        return Response(
            {
                "message": "Picking job started.",
                "job": _job_summary(picking_job),
                "cart_work_session": _cart_work_session_data(cart_work_session),
                "session": _session_data(session),
            },
            status=status.HTTP_200_OK,
        )


class ScannerCartWorkCurrentView(APIView):
    def get(self, request):
        session_id = request.query_params.get("session_id")
        cart_work_session_id = request.query_params.get("cart_work_session_id")
        cart_work_session = None
        if cart_work_session_id:
            cart_work_session = CartWorkSession.objects.select_related("cart", "picking_job", "scanner_session").filter(
                pk=cart_work_session_id
            ).first()
        elif session_id:
            cart_work_session = CartWorkSession.objects.select_related("cart", "picking_job", "scanner_session").filter(
                scanner_session_id=session_id
            ).first()

        if cart_work_session is None:
            return Response({"detail": "Cart work session not found."}, status=status.HTTP_404_NOT_FOUND)

        tasks = _job_tasks(cart_work_session.picking_job).order_by("source_location__code", "created_at", "id")
        state, confirmed_location_code, instruction = _picking_state(cart_work_session)
        return Response(
            {
                "cart_work_session": _cart_work_session_data(cart_work_session),
                "state": state,
                "confirmed_location_code": confirmed_location_code,
                "current_instruction": instruction,
                "tasks": [PickingTaskSerializer(task).data for task in tasks],
            }
        )


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
            .filter(session=session, quantity_picked__gt=0)
            .order_by("created_at", "id")
        )
        return Response({"session": _session_data(session), "items": [_cart_item_data(item) for item in items]})


class ScannerControlCartView(APIView):
    def get(self, request):
        cart_code = str(request.query_params.get("cart_code", "")).strip()
        if not cart_code:
            return Response({"detail": "cart_code query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        cart_work_session = (
            CartWorkSession.objects.select_related("cart", "picking_job", "scanner_session")
            .filter(cart__code__iexact=cart_code, status__in=[CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL])
            .first()
        )
        if cart_work_session is None or cart_work_session.scanner_session is None:
            return Response({"detail": "Cart has no active picked work."}, status=status.HTTP_404_NOT_FOUND)

        items = (
            CartPickedItem.objects.select_related(
                "cart",
                "product",
                "route_run__route",
                "picking_task__order_line__order",
            )
            .filter(cart_work_session=cart_work_session, quantity_picked__gt=0)
            .order_by("created_at", "id")
        )
        return Response(
            {
                "session": _session_data(cart_work_session.scanner_session),
                "cart_work_session": _cart_work_session_data(cart_work_session),
                "items": [_cart_item_data(item) for item in items],
            }
        )


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
                    "scan_code": label.scan_code,
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
            cart_work_session = getattr(session, "cart_work_session", None)
            session.status = ScannerSession.Status.CLOSED
            session.ended_at = timezone.now()
            session.save(update_fields=["status", "ended_at", "updated_at"])
            if cart_work_session is not None:
                cart_work_session.status = CartWorkSession.Status.COMPLETED
                cart_work_session.finished_at = timezone.now()
                cart_work_session.save(update_fields=["status", "finished_at", "updated_at"])

                picking_job = cart_work_session.picking_job
                if _job_tasks(picking_job).filter(quantity_prepared__lt=F("quantity_to_pick")).exists():
                    picking_job.status = PickingJob.Status.PICKED
                    picking_job.save(update_fields=["status", "updated_at"])
                else:
                    picking_job.status = PickingJob.Status.COMPLETED
                    picking_job.completed_at = timezone.now()
                    picking_job.save(update_fields=["status", "completed_at", "updated_at"])

                route_runs = picking_job.route_runs.all()
                for route_run in route_runs:
                    recalculate_route_readiness(route_run)

            session.cart.status = ScannerCart.Status.AVAILABLE
            session.cart.save(update_fields=["status", "updated_at"])
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="CartWorkSession" if cart_work_session is not None else "ScannerSession",
                entity_id=str(cart_work_session.id if cart_work_session is not None else session.id),
                message=(
                    f"Control finished and cart {session.cart.code} released "
                    f"by {session.worker_code or 'scanner'}."
                ),
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


class ScannerContentsView(APIView):
    def get(self, request):
        try:
            return Response(resolve_contents_code(request.query_params.get("code", "")))
        except ContentsLookupError as error:
            payload = {"detail": error.detail}
            if error.matched_object_types:
                payload["matched_object_types"] = error.matched_object_types
            return Response(payload, status=error.status_code)


class ScannerReceivingStartView(APIView):
    def post(self, request):
        pallet_code = str(request.data.get("pallet_code", "")).strip()
        worker_code = str(request.data.get("worker_code", "")).strip() or "DEMO"
        if not pallet_code:
            return Response({"detail": "pallet_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            pallet = (
                TransferPallet.objects.select_for_update()
                .select_related("transfer", "transfer__source_branch", "transfer__destination_branch")
                .filter(scan_code__iexact=pallet_code)
                .first()
            )
            if pallet is None:
                return Response({"detail": "Pallet not found."}, status=status.HTTP_404_NOT_FOUND)
            if _pallet_is_closed(pallet):
                return Response({"detail": "Pallet is already closed."}, status=status.HTTP_400_BAD_REQUEST)

            session = PalletReceivingSession.objects.select_for_update().filter(
                pallet=pallet,
                status=PalletReceivingSession.Status.ACTIVE,
            ).first()
            created = False
            if session is None:
                session = PalletReceivingSession.objects.create(pallet=pallet, worker_code=worker_code)
                created = True
            elif session.worker_code != worker_code:
                session.worker_code = worker_code
                session.save(update_fields=["worker_code", "updated_at"])

            now = timezone.now()
            update_fields = []
            if pallet.status != TransferPallet.Status.RECEIVING:
                pallet.status = TransferPallet.Status.RECEIVING
                update_fields.append("status")
            if pallet.receiving_started_at is None:
                pallet.receiving_started_at = now
                update_fields.append("receiving_started_at")
            if update_fields:
                update_fields.append("updated_at")
                pallet.save(update_fields=update_fields)

            transfer = pallet.transfer
            if transfer.status != InterBranchTransfer.Status.RECEIVING:
                transfer.status = InterBranchTransfer.Status.RECEIVING
                transfer.save(update_fields=["status", "updated_at"])

            if created:
                AuditLog.objects.create(
                    action_type=AuditLog.ActionType.UPDATE,
                    entity_name="TransferPallet",
                    entity_id=str(pallet.id),
                    message=f"Receiving started for pallet {pallet.scan_code} by {worker_code}.",
                )

        return Response(
            {"message": "Pallet receiving started.", "receiving_session": _receiving_session_data(session)},
            status=status.HTTP_200_OK,
        )


class ScannerReceivingCurrentView(APIView):
    def get(self, request):
        session_id = request.query_params.get("receiving_session_id")
        pallet_code = str(request.query_params.get("pallet_code", "")).strip()

        if not session_id and not pallet_code:
            return Response(
                {"detail": "receiving_session_id or pallet_code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = PalletReceivingSession.objects.select_related(
            "pallet",
            "pallet__transfer",
            "pallet__transfer__source_branch",
            "pallet__transfer__destination_branch",
            "current_pallet_item",
            "current_pallet_item__product",
        )
        session = queryset.filter(pk=session_id).first() if session_id else queryset.filter(
            pallet__scan_code__iexact=pallet_code,
            status=PalletReceivingSession.Status.ACTIVE,
        ).first()
        if session is None:
            return Response({"detail": "Receiving session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != PalletReceivingSession.Status.ACTIVE:
            return Response({"detail": "Receiving session is not active."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"receiving_session": _receiving_session_data(session)})


class ScannerReceivingScanProductView(APIView):
    def post(self, request):
        product_code = str(request.data.get("product_code") or request.data.get("code") or "").strip()
        if not product_code:
            return Response({"detail": "product_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        quantity, error = _parse_positive_piece_quantity(request.data.get("quantity", 1))
        if error is not None:
            return error

        product = _find_product_by_code(product_code)
        if product is None:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            session, error = _get_active_receiving_session_or_response(request.data.get("receiving_session_id"))
            if error is not None:
                return error
            session = PalletReceivingSession.objects.select_for_update().get(pk=session.id)
            pallet = TransferPallet.objects.select_for_update().get(pk=session.pallet_id)
            if _pallet_is_closed(pallet):
                return Response({"detail": "Pallet is already closed."}, status=status.HTTP_400_BAD_REQUEST)

            item = (
                TransferPalletItem.objects.select_for_update()
                .select_related("product")
                .filter(pallet=pallet, product=product)
                .first()
            )
            if item is None:
                return Response({"detail": "Product is not expected on this pallet."}, status=status.HTTP_400_BAD_REQUEST)

            remaining = item.expected_quantity - item.received_quantity
            if quantity > remaining:
                return Response(
                    {"detail": "Quantity exceeds remaining pallet quantity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            session.current_pallet_item = item
            session.pending_quantity = quantity
            session.save(update_fields=["current_pallet_item", "pending_quantity", "updated_at"])

            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="TransferPallet",
                entity_id=str(pallet.id),
                message=f"Receiving scanned {quantity} {product.sku} on pallet {pallet.scan_code}.",
            )

        session.refresh_from_db()
        return Response({"message": "Product confirmed.", "receiving_session": _receiving_session_data(session)})


class ScannerReceivingPutAwayView(APIView):
    def post(self, request):
        location_code = str(request.data.get("location_code", "")).strip()
        if not location_code:
            return Response({"detail": "location_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            session, error = _get_active_receiving_session_or_response(request.data.get("receiving_session_id"))
            if error is not None:
                return error
            session = (
                PalletReceivingSession.objects.select_for_update(of=("self",))
                .select_related(
                    "pallet",
                    "pallet__transfer",
                    "pallet__transfer__destination_branch",
                    "current_pallet_item",
                    "current_pallet_item__product",
                )
                .get(pk=session.id)
            )
            if not session.current_pallet_item_id or not session.pending_quantity:
                return Response(
                    {"detail": "Scan a product before scanning the destination location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            pallet = TransferPallet.objects.select_for_update().get(pk=session.pallet_id)
            if _pallet_is_closed(pallet):
                return Response({"detail": "Pallet is already closed."}, status=status.HTTP_400_BAD_REQUEST)

            location = Location.objects.select_related("branch").filter(code__iexact=location_code).first()
            if location is None:
                return Response({"detail": "Destination location not found."}, status=status.HTTP_404_NOT_FOUND)
            destination_branch = session.pallet.transfer.destination_branch
            if location.branch_id != destination_branch.id:
                return Response(
                    {"detail": f"Wrong branch. Use a {destination_branch.code} destination location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            item = TransferPalletItem.objects.select_for_update().select_related("product").get(
                pk=session.current_pallet_item_id
            )
            quantity = session.pending_quantity
            remaining = item.expected_quantity - item.received_quantity
            if quantity > remaining:
                return Response(
                    {"detail": "Quantity exceeds remaining pallet quantity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            inventory_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=destination_branch,
                location=location,
                product=item.product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            inventory_item.quantity_on_hand = F("quantity_on_hand") + quantity
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

            item.received_quantity = F("received_quantity") + quantity
            item.save(update_fields=["received_quantity", "updated_at"])
            item.refresh_from_db()

            PalletReceivingScan.objects.create(
                receiving_session=session,
                pallet=pallet,
                product=item.product,
                destination_location=location,
                quantity=quantity,
                worker_code=session.worker_code,
            )
            movement = StockMovement.objects.create(
                branch=destination_branch,
                product=item.product,
                inventory_item=inventory_item,
                destination_location=location,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=quantity,
                reference=pallet.scan_code,
                performed_by=None,
            )
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.UPDATE,
                entity_name="PalletReceivingScan",
                entity_id=str(movement.id),
                message=(
                    f"Received {quantity} {item.product.sku} from pallet {pallet.scan_code} "
                    f"to location {location.code}."
                ),
            )

            session.current_pallet_item = None
            session.pending_quantity = None
            session.save(update_fields=["current_pallet_item", "pending_quantity", "updated_at"])

        session.refresh_from_db()
        return Response({"message": "Product put away.", "receiving_session": _receiving_session_data(session)})


class ScannerReceivingCompleteView(APIView):
    def post(self, request):
        return _close_receiving_session(request.data.get("receiving_session_id"))


class ScannerReceivingCloseView(APIView):
    def post(self, request):
        return _close_receiving_session(request.data.get("receiving_session_id"))


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
