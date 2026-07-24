from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.authorization import require_branch_access
from operations.models import (
    AuditLog,
    InterBranchTransfer,
    PickingTask,
    RouteRun,
    Shipment,
    ShipmentLine,
    ShipmentLineQuantityAdjustment,
    ShipmentRouteAssignment,
    ShipmentStatusHistory,
)
from operations.operational_projections import shipment_line_progress, shipment_operational_projection
from operations.route_services import operational_identifier
from operations.services import is_route_work_fully_prepared, recalculate_route_readiness
from warehouse.models import InventoryItem
from warehouse.quantity_policy import product_quantity_error


TERMINAL_SHIPMENT_STATUSES = {
    Shipment.Status.DISPATCHED,
    Shipment.Status.COMPLETED,
    Shipment.Status.CANCELLED,
}

MANUAL_STATUS_TRANSITIONS = {
    Shipment.Status.PENDING_ACTIVATION: [Shipment.Status.ACTIVE, Shipment.Status.CANCELLED],
    Shipment.Status.ACTIVE: [Shipment.Status.EXCEPTION, Shipment.Status.CANCELLED],
    Shipment.Status.PICKING: [Shipment.Status.EXCEPTION],
    Shipment.Status.PICKED: [Shipment.Status.EXCEPTION],
    Shipment.Status.CONTROLLED: [Shipment.Status.EXCEPTION],
    Shipment.Status.EXCEPTION: [Shipment.Status.ACTIVE, Shipment.Status.CANCELLED],
}


def route_snapshot(route_run: RouteRun | None) -> str:
    if route_run is None:
        return ""
    return (
        f"{route_run.route.branch.code} / {route_run.route.code} / "
        f"{route_run.service_date} / run {route_run.run_number} / {route_run.departure_time}"
    )


def audit_shipment_event(user, shipment: Shipment, event_type: str, message: str, **extra):
    return AuditLog.objects.create(
        actor=user,
        action_type=extra.pop("action_type", AuditLog.ActionType.STATUS_CHANGE),
        event_type=event_type,
        branch=shipment.branch,
        order=shipment.order,
        route_run=shipment.route_run,
        transfer=shipment.inter_branch_transfer,
        reference=shipment.reference,
        entity_name="Shipment",
        entity_id=str(shipment.id),
        message=message,
        **extra,
    )


def shipment_picking_totals(shipment: Shipment) -> dict[str, Decimal]:
    tasks = PickingTask.objects.filter(order_line__shipment_line__shipment=shipment).exclude(status=PickingTask.Status.CANCELLED)
    totals = tasks.aggregate(
        quantity_to_pick=Sum("quantity_to_pick"),
        quantity_picked=Sum("quantity_picked"),
        quantity_prepared=Sum("quantity_prepared"),
        shortage_quantity=Sum("shortage_quantity"),
    )
    return {key: value or Decimal("0") for key, value in totals.items()}


def shipment_line_effective_quantity(line: ShipmentLine) -> Decimal:
    return shipment_line_progress(line).effective_quantity


def shipment_line_task_totals(line: ShipmentLine) -> dict[str, Decimal]:
    progress = shipment_line_progress(line)
    return {
        "picked": progress.picked_quantity,
        "prepared": progress.prepared_quantity,
        "shortage": progress.shortage_quantity,
    }


def shipment_line_max_removable_quantity(line: ShipmentLine) -> Decimal:
    totals = shipment_line_task_totals(line)
    locked_quantity = max(totals["picked"], totals["prepared"])
    return max(shipment_line_effective_quantity(line) - locked_quantity, Decimal("0"))


def derive_shipment_operational_statuses(shipment: Shipment) -> dict[str, str]:
    tasks = list(PickingTask.objects.filter(order_line__shipment_line__shipment=shipment).exclude(status=PickingTask.Status.CANCELLED))
    active_lines = list(shipment.lines.all())
    has_effective_quantity = any(shipment_line_effective_quantity(line) > 0 for line in active_lines)
    if not has_effective_quantity:
        return {
            "picking_status": ShipmentLine.ServiceStatus.CANCELLED,
            "control_status": ShipmentLine.ServiceStatus.CANCELLED,
            "document_status": shipment.document_status,
            "route_status": shipment.route_run.status if shipment.route_run else "unassigned",
        }
    if not tasks:
        picking_status = "not_started"
        control_status = "not_started"
    elif all(task.quantity_picked == 0 for task in tasks) and all(
        task.status in {PickingTask.Status.OPEN, PickingTask.Status.ASSIGNED} for task in tasks
    ):
        picking_status = "not_started"
        control_status = "not_started"
    elif any(task.status in {PickingTask.Status.OPEN, PickingTask.Status.ASSIGNED, PickingTask.Status.IN_PROGRESS} for task in tasks):
        picking_status = "in_progress"
        control_status = "not_started"
    elif any(task.shortage_quantity > 0 for task in tasks):
        picking_status = "shortage"
        control_status = "blocked"
    elif all(task.quantity_picked >= task.quantity_to_pick for task in tasks):
        picking_status = "completed"
        control_status = "completed" if all(task.quantity_prepared >= task.quantity_to_pick for task in tasks) else "in_progress"
    else:
        picking_status = "in_progress"
        control_status = "not_started"

    route_status = shipment.route_run.status if shipment.route_run else "unassigned"
    return {
        "picking_status": picking_status,
        "control_status": control_status,
        "document_status": shipment.document_status,
        "route_status": route_status,
    }


def derive_shipment_line_status(line: ShipmentLine) -> str:
    state = shipment_line_progress(line).state
    return {
        "unstarted": ShipmentLine.ServiceStatus.NOT_STARTED,
        "started": ShipmentLine.ServiceStatus.PICKING,
        "picked": ShipmentLine.ServiceStatus.PICKED,
        "prepared": ShipmentLine.ServiceStatus.PREPARED,
        "cancelled": ShipmentLine.ServiceStatus.CANCELLED,
    }[state]

def sync_shipment_status_from_work(shipment: Shipment) -> Shipment:
    if shipment.status in TERMINAL_SHIPMENT_STATUSES or shipment.status == Shipment.Status.PREPARED:
        return shipment
    line_states = [
        shipment_line_progress(line)
        for line in shipment.lines.prefetch_related("order_line__picking_tasks").all()
        if shipment_line_progress(line).effective_quantity > 0
    ]
    if line_states and all(progress.state == "prepared" and progress.shortage_quantity == 0 for progress in line_states):
        shipment.status = Shipment.Status.PREPARED
        shipment.prepared_at = shipment.prepared_at or timezone.now()
        shipment.save(update_fields=["status", "prepared_at", "updated_at"])
        return shipment
    statuses = derive_shipment_operational_statuses(shipment)
    new_status = shipment.status
    if statuses["control_status"] == "completed":
        new_status = Shipment.Status.CONTROLLED
    elif statuses["picking_status"] == "completed":
        new_status = Shipment.Status.PICKED
    elif statuses["picking_status"] == "in_progress":
        new_status = Shipment.Status.PICKING
    if new_status != shipment.status:
        shipment.status = new_status
        shipment.save(update_fields=["status", "updated_at"])
    return shipment


def activate_shipment(user, shipment_id: int, client_operation_id: str | None = None):
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch", "order").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        if shipment.status == Shipment.Status.ACTIVE:
            return shipment, True
        if shipment.status in TERMINAL_SHIPMENT_STATUSES:
            raise ValidationError("Terminal shipments cannot be activated.")
        if shipment.status != Shipment.Status.PENDING_ACTIVATION:
            raise ValidationError("Only pending shipments can be activated.")
        shipment.status = Shipment.Status.ACTIVE
        shipment.activated_at = timezone.now()
        shipment.activated_by = user
        shipment.save(update_fields=["status", "activated_at", "activated_by", "updated_at"])
        ShipmentStatusHistory.objects.create(
            shipment=shipment,
            previous_status=Shipment.Status.PENDING_ACTIVATION,
            new_status=Shipment.Status.ACTIVE,
            changed_by=user,
            reason="Shipment activated.",
            client_operation_id=client_operation_id or None,
        )
        audit_shipment_event(user, shipment, "shipment_activated", f"{user.username} activated shipment {shipment.reference}.")
        return shipment, False


def post_picking_lists(user, shipment_id: int, client_operation_id: str | None = None):
    with transaction.atomic():
        shipment = (
            Shipment.objects.select_for_update()
            .select_related("branch", "order")
            .prefetch_related("lines__order_line__picking_tasks", "lines__product")
            .get(pk=shipment_id)
        )
        require_branch_access(user, shipment.branch)
        if shipment.status not in [Shipment.Status.ACTIVE, Shipment.Status.PICKING]:
            raise ValidationError("Picking lists can only be posted for active shipments.")
        created_count = 0
        for line in shipment.lines.select_related("order_line", "product"):
            if line.order_line.order_id != shipment.order_id:
                raise ValidationError("Shipment line does not belong to the shipment order.")
            if line.order_line.picking_tasks.exists():
                continue
            inventory_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=shipment.branch, product=line.product, quantity_on_hand__gt=0)
                .select_related("location")
                .order_by("-quantity_on_hand", "location__code")
                .first()
            )
            if inventory_item is None:
                raise ValidationError(f"No available source stock for {line.product.sku}.")
            effective_quantity = shipment_line_effective_quantity(line)
            if effective_quantity <= 0:
                continue
            PickingTask.objects.create(
                branch=shipment.branch,
                order_line=line.order_line,
                source_location=inventory_item.location,
                status=PickingTask.Status.OPEN,
                quantity_to_pick=effective_quantity,
            )
            created_count += 1
        shipment.status = Shipment.Status.PICKING
        shipment.picking_lists_posted_at = shipment.picking_lists_posted_at or timezone.now()
        shipment.picking_lists_posted_by = shipment.picking_lists_posted_by or user
        shipment.save(update_fields=["status", "picking_lists_posted_at", "picking_lists_posted_by", "updated_at"])
        audit_shipment_event(
            user,
            shipment,
            "shipment_picking_work_posted",
            f"{user.username} posted picking work for shipment {shipment.reference}.",
            result="created" if created_count else "already_posted",
        )
        return shipment, created_count


def prepare_shipment(user, shipment_id: int):
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch", "order").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        if shipment.status == Shipment.Status.PREPARED:
            return shipment, True
        if shipment.status in TERMINAL_SHIPMENT_STATUSES:
            raise ValidationError("Terminal shipments cannot be prepared.")
        totals = shipment_picking_totals(shipment)
        if totals["quantity_to_pick"] <= 0:
            raise ValidationError("Shipment has no posted picking work.")
        required = totals["quantity_to_pick"] - totals["shortage_quantity"]
        if totals["quantity_picked"] < required:
            raise ValidationError("Picking is not complete.")
        if totals["quantity_prepared"] < required:
            raise ValidationError("Control is not complete.")
        shipment.status = Shipment.Status.PREPARED
        shipment.prepared_at = timezone.now()
        shipment.prepared_by = user
        if shipment.document_status == Shipment.DocumentStatus.NOT_AVAILABLE:
            shipment.document_status = Shipment.DocumentStatus.AVAILABLE
        shipment.save(update_fields=["status", "prepared_at", "prepared_by", "document_status", "updated_at"])
        if shipment.route_run:
            recalculate_route_readiness(shipment.route_run)
        audit_shipment_event(user, shipment, "shipment_prepared", f"{user.username} prepared shipment {shipment.reference}.")
        return shipment, False


def cancel_shipment(user, shipment_id: int, reason: str):
    reason = reason.strip()
    if not reason:
        raise ValidationError("Cancellation reason is required.")
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        if shipment.status == Shipment.Status.CANCELLED:
            return shipment, True
        if shipment.status in [Shipment.Status.DISPATCHED, Shipment.Status.COMPLETED]:
            raise ValidationError("Dispatched or completed shipments cannot be cancelled.")
        if shipment.route_run and shipment.route_run.status == RouteRun.Status.CLOSED:
            raise ValidationError("Shipments on closed routes cannot be cancelled.")
        if shipment.documents_posted_at or (
            shipment.inter_branch_transfer and shipment.inter_branch_transfer.status != InterBranchTransfer.Status.DRAFT
        ):
            raise ValidationError("Shipments with posted inter-branch documents cannot be cancelled.")
        PickingTask.objects.filter(order_line__shipment_line__shipment=shipment).exclude(
            status__in=[PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED]
        ).update(status=PickingTask.Status.CANCELLED)
        previous_status = shipment.status
        shipment.status = Shipment.Status.CANCELLED
        shipment.cancelled_at = timezone.now()
        shipment.cancelled_by = user
        shipment.cancellation_reason = reason
        shipment.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_reason", "updated_at"])
        ShipmentStatusHistory.objects.create(
            shipment=shipment,
            previous_status=previous_status,
            new_status=Shipment.Status.CANCELLED,
            changed_by=user,
            reason=reason,
        )
        audit_shipment_event(user, shipment, "shipment_cancelled", f"{user.username} cancelled shipment {shipment.reference}.", result="cancelled")
        return shipment, False


def print_shipment_documents(user, shipment_id: int, printer: str = ""):
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        if shipment.status == Shipment.Status.CANCELLED:
            raise ValidationError("Cancelled shipments cannot be printed.")
        if shipment.document_status == Shipment.DocumentStatus.NOT_AVAILABLE:
            raise ValidationError("Documents are not available for this shipment.")
        shipment.document_status = Shipment.DocumentStatus.PRINTED
        shipment.documents_printed_at = timezone.now()
        shipment.documents_printed_by = user
        shipment.document_print_count += 1
        shipment.save(
            update_fields=[
                "document_status",
                "documents_printed_at",
                "documents_printed_by",
                "document_print_count",
                "updated_at",
            ]
        )
        audit_shipment_event(
            user,
            shipment,
            "shipment_documents_printed",
            f"{user.username} printed documents for shipment {shipment.reference}.",
            result=printer[:64],
        )
        return shipment


def post_inter_branch_documents(user, shipment_id: int):
    with transaction.atomic():
        shipment = (
            Shipment.objects.select_for_update()
            .select_related("branch")
            .get(pk=shipment_id)
        )
        require_branch_access(user, shipment.branch)
        if shipment.shipment_type != Shipment.ShipmentType.INTER_BRANCH or shipment.inter_branch_transfer is None:
            raise ValidationError("Shipment is not an inter-branch shipment.")
        transfer = InterBranchTransfer.objects.select_related("source_branch", "destination_branch").get(pk=shipment.inter_branch_transfer_id)
        if transfer.source_branch_id != shipment.branch_id:
            raise ValidationError("Only the source branch can post inter-branch documents.")
        if shipment.documents_posted_at is not None:
            return shipment, True
        if shipment.status != Shipment.Status.PREPARED:
            raise ValidationError("Shipment must be prepared before documents are posted.")
        now = timezone.now()
        shipment.status = Shipment.Status.DOCUMENTS_POSTED
        shipment.document_status = Shipment.DocumentStatus.POSTED
        shipment.documents_posted_at = now
        shipment.documents_posted_by = user
        shipment.save(update_fields=["status", "document_status", "documents_posted_at", "documents_posted_by", "updated_at"])
        audit_shipment_event(user, shipment, "shipment_documents_posted", f"{user.username} posted documents for shipment {shipment.reference}.")
        return shipment, False


def confirm_picking_route(user, shipment_id: int):
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        if shipment.route_run is None:
            raise ValidationError("Shipment is not assigned to a route.")
        if shipment.status in TERMINAL_SHIPMENT_STATUSES:
            raise ValidationError("Terminal shipments cannot have picking route confirmation.")
        replayed = shipment.picking_route_confirmed_at is not None
        shipment.picking_route_confirmed_at = shipment.picking_route_confirmed_at or timezone.now()
        shipment.picking_route_confirmed_by = shipment.picking_route_confirmed_by or user
        shipment.save(update_fields=["picking_route_confirmed_at", "picking_route_confirmed_by", "updated_at"])
        if not replayed:
            audit_shipment_event(user, shipment, "shipment_picking_route_confirmed", f"{user.username} confirmed picking route for shipment {shipment.reference}.")
        return shipment, replayed


def route_close_readiness(route_run: RouteRun) -> dict:
    active_shipments = list(
        route_run.shipments.exclude(status=Shipment.Status.CANCELLED).prefetch_related(
            "lines__order_line__picking_tasks"
        )
    )
    unpicked_line_count = 0
    uncontrolled_line_count = 0
    unprepared_line_count = 0
    incomplete_shipment_count = 0

    prepared_statuses = {
        Shipment.Status.PREPARED,
        Shipment.Status.DOCUMENTS_POSTED,
        Shipment.Status.READY_FOR_DISPATCH,
    }
    for shipment in active_shipments:
        effective_lines = []
        shipment_incomplete = False
        for line in shipment.lines.all():
            progress = shipment_line_progress(line)
            if progress.effective_quantity <= 0:
                continue
            effective_lines.append(line)
            required = max(progress.effective_quantity - progress.shortage_quantity, Decimal("0"))
            if progress.remaining_to_pick > 0:
                unpicked_line_count += 1
                shipment_incomplete = True
            elif progress.prepared_quantity < required:
                uncontrolled_line_count += 1
                shipment_incomplete = True
        if effective_lines and shipment.status not in prepared_statuses:
            if not shipment_incomplete:
                unprepared_line_count += len(effective_lines)
            shipment_incomplete = True
        if shipment_incomplete:
            incomplete_shipment_count += 1

    blockers = []
    if not active_shipments:
        blockers.append({"code": "no_active_shipments", "message": "The route has no active Shipments."})
    if unpicked_line_count:
        blockers.append(
            {
                "code": "picking_incomplete",
                "message": f"{unpicked_line_count} Shipment line(s) still require picking.",
            }
        )
    if uncontrolled_line_count:
        blockers.append(
            {
                "code": "control_incomplete",
                "message": f"{uncontrolled_line_count} Shipment line(s) still require Control.",
            }
        )
    if unprepared_line_count:
        blockers.append(
            {
                "code": "preparation_incomplete",
                "message": f"{unprepared_line_count} Shipment line(s) still require preparation.",
            }
        )
    if incomplete_shipment_count > 1:
        blockers.append(
            {
                "code": "shipment_incomplete",
                "message": "Another Shipment assigned to this RouteRun is incomplete.",
            }
        )

    lifecycle_blocked = route_run.status in {
        RouteRun.Status.CLOSED,
        RouteRun.Status.CANCELLED,
        RouteRun.Status.DISPATCHED,
    }
    return {
        "can_close": not lifecycle_blocked and not blockers,
        "close_blockers": blockers,
        "unpicked_line_count": unpicked_line_count,
        "uncontrolled_line_count": uncontrolled_line_count,
        "unprepared_line_count": unprepared_line_count,
        "incomplete_shipment_count": incomplete_shipment_count,
        "shipment_count": len(active_shipments),
        "route_status": route_run.status,
    }


def print_route_document_package(user, route_run: RouteRun, shipments: list[Shipment], printer_code: str) -> dict:
    """Print the supported route package: one Shipment document per active Shipment."""
    printed_at = timezone.now()
    for shipment in shipments:
        if shipment.document_status != Shipment.DocumentStatus.POSTED:
            shipment.document_status = Shipment.DocumentStatus.PRINTED
        shipment.documents_printed_at = printed_at
        shipment.documents_printed_by = user
        shipment.document_print_count += 1
        shipment.save(
            update_fields=[
                "document_status",
                "documents_printed_at",
                "documents_printed_by",
                "document_print_count",
                "updated_at",
            ]
        )
    return {
        "document_count": len(shipments),
        "printer_code": printer_code,
        "printed_at": printed_at,
    }


def close_route_run(user, route_run_id: int, printer_code: str = "WMS-ROUTE") -> dict:
    with transaction.atomic():
        route_run = (
            RouteRun.objects.select_for_update()
            .select_related("route", "route__branch")
            .get(pk=route_run_id)
        )
        require_branch_access(user, route_run.route.branch)
        shipments = list(
            Shipment.objects.select_for_update()
            .select_related("branch", "order")
            .filter(route_run=route_run)
            .exclude(status=Shipment.Status.CANCELLED)
            .order_by("id")
        )

        if route_run.status == RouteRun.Status.CLOSED:
            return {
                "replayed": True,
                "route_run_id": route_run.id,
                "operational_identifier": operational_identifier(
                    route_run.route, route_run.service_date, route_run.run_number
                ),
                "shipment_count": len(shipments),
                "document_count": len(shipments),
                "printer_code": printer_code,
                "printed_at": route_run.documents_printed_at,
                "closed_at": route_run.closed_at,
                "status": route_run.status,
            }
        if route_run.status in {RouteRun.Status.CANCELLED, RouteRun.Status.DISPATCHED}:
            raise ValidationError(
                {
                    "detail": "Route cannot be closed.",
                    "code": "route_not_open",
                    "blockers": [
                        {"code": "route_not_open", "message": "The RouteRun is no longer open."}
                    ],
                }
            )

        readiness = route_close_readiness(route_run)
        if not readiness["can_close"]:
            raise ValidationError(
                {
                    "detail": "Route cannot be closed.",
                    "code": "route_not_ready",
                    "blockers": readiness["close_blockers"],
                    **{key: readiness[key] for key in [
                        "unpicked_line_count",
                        "uncontrolled_line_count",
                        "unprepared_line_count",
                        "incomplete_shipment_count",
                    ]},
                }
            )

        try:
            package = print_route_document_package(user, route_run, shipments, printer_code)
        except Exception:
            transaction.set_rollback(True)
            raise ValidationError(
                {
                    "detail": "Route package could not be printed. The route remains open.",
                    "code": "route_print_failed",
                    "route_run_id": route_run.id,
                }
            )

        route_run.documents_printed_at = package["printed_at"]
        route_run.status = RouteRun.Status.CLOSED
        route_run.closed_at = timezone.now()
        route_run.save(update_fields=["documents_printed_at", "status", "closed_at", "updated_at"])
        Shipment.objects.filter(id__in=[shipment.id for shipment in shipments]).update(
            status=Shipment.Status.COMPLETED,
            updated_at=route_run.closed_at,
        )
        identifier = operational_identifier(route_run.route, route_run.service_date, route_run.run_number)
        AuditLog.objects.create(
            actor=user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="route_package_printed",
            branch=route_run.route.branch,
            route_run=route_run,
            reference=identifier,
            result=printer_code[:64],
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=f"Route package printed for {identifier}: {package['document_count']} Shipment document(s).",
        )
        AuditLog.objects.create(
            actor=user,
            action_type=AuditLog.ActionType.STATUS_CHANGE,
            event_type="route_run_closed",
            branch=route_run.route.branch,
            route_run=route_run,
            reference=identifier,
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=f"{user.username} closed RouteRun {identifier} after route package printing.",
        )
        return {
            "replayed": False,
            "route_run_id": route_run.id,
            "operational_identifier": identifier,
            "shipment_count": len(shipments),
            "document_count": package["document_count"],
            "printer_code": package["printer_code"],
            "printed_at": package["printed_at"],
            "closed_at": route_run.closed_at,
            "status": route_run.status,
        }


def close_shipment_route(user, shipment_id: int, printer_code: str = "WMS-ROUTE"):
    shipment = Shipment.objects.select_related("branch", "route_run").get(pk=shipment_id)
    require_branch_access(user, shipment.branch)
    if shipment.route_run_id is None:
        raise ValidationError("Shipment is not assigned to a route.")
    result = close_route_run(user, shipment.route_run_id, printer_code)
    shipment.refresh_from_db()
    return shipment, result

def change_shipment_route(user, shipment_id: int, new_route_run_id: int, reason: str = "", client_operation_id: str | None = None):
    reason = reason.strip()
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch", "order").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        new_route_run = RouteRun.objects.select_for_update().select_related("route", "route__branch").get(pk=new_route_run_id)
        require_branch_access(user, new_route_run.route.branch)
        if new_route_run.route.branch_id != shipment.branch_id:
            raise ValidationError("Target route belongs to another branch.")
        if shipment.status in [Shipment.Status.DISPATCHED, Shipment.Status.COMPLETED, Shipment.Status.CANCELLED]:
            raise ValidationError("Terminal shipments cannot be reassigned.")
        if new_route_run.status in [RouteRun.Status.CLOSED, RouteRun.Status.DISPATCHED, RouteRun.Status.CANCELLED]:
            raise ValidationError("Target route is not eligible.")
        if shipment.documents_posted_at:
            raise ValidationError("Posted documents prevent route reassignment.")
        if shipment.route_run_id == new_route_run.id:
            return shipment, True
        previous = shipment.route_run
        ShipmentRouteAssignment.objects.create(
            shipment=shipment,
            previous_route_run=previous,
            new_route_run=new_route_run,
            changed_by=user,
            reason=reason,
            previous_route_snapshot=route_snapshot(previous),
            new_route_snapshot=route_snapshot(new_route_run),
            client_operation_id=client_operation_id or None,
        )
        shipment.route_run = new_route_run
        if shipment.document_status == Shipment.DocumentStatus.PRINTED:
            shipment.document_status = Shipment.DocumentStatus.REQUIRES_REFRESH
        shipment.save(update_fields=["route_run", "document_status", "updated_at"])
        shipment.order.route_run = new_route_run
        shipment.order.save(update_fields=["route_run", "updated_at"])
        audit_shipment_event(user, shipment, "shipment_route_changed", f"{user.username} moved shipment {shipment.reference} to {route_snapshot(new_route_run)}.")
        return shipment, False


def change_shipment_status(user, shipment_id: int, new_status: str, reason: str, client_operation_id: str | None = None):
    reason = reason.strip()
    if not reason:
        raise ValidationError("Status change reason is required.")
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        allowed = MANUAL_STATUS_TRANSITIONS.get(shipment.status, [])
        if new_status not in allowed:
            raise ValidationError("Requested status transition is not permitted.")
        previous_status = shipment.status
        shipment.status = new_status
        shipment.save(update_fields=["status", "updated_at"])
        ShipmentStatusHistory.objects.create(
            shipment=shipment,
            previous_status=previous_status,
            new_status=new_status,
            changed_by=user,
            reason=reason,
            client_operation_id=client_operation_id or None,
        )
        audit_shipment_event(user, shipment, "shipment_status_changed", f"{user.username} changed shipment {shipment.reference} from {previous_status} to {new_status}.")
        return shipment


def remove_shipment_line_quantity(
    user,
    shipment_id: int,
    shipment_line_id: int,
    quantity: Decimal,
    reason: str,
    client_operation_id: str | None = None,
):
    reason = reason.strip()
    if not reason:
        raise ValidationError("Removal reason is required.")
    if quantity <= 0:
        raise ValidationError("Removal quantity must be positive.")

    with transaction.atomic():
        if client_operation_id:
            existing = ShipmentLineQuantityAdjustment.objects.select_related("shipment").filter(client_operation_id=client_operation_id).first()
            if existing is not None:
                if existing.shipment_id != int(shipment_id) or existing.shipment_line_id != int(shipment_line_id) or existing.quantity_removed != quantity:
                    raise ValidationError("Client operation id was already used for another shipment-line adjustment.")
                return existing.shipment, existing.shipment_line, existing, True

        shipment = Shipment.objects.select_for_update().select_related("branch").get(pk=shipment_id)
        require_branch_access(user, shipment.branch)
        if shipment.status in TERMINAL_SHIPMENT_STATUSES or shipment.status == Shipment.Status.DOCUMENTS_POSTED:
            raise ValidationError("Shipment is no longer eligible for quantity removal.")
        if shipment.route_run and shipment.route_run.status == RouteRun.Status.CLOSED:
            raise ValidationError("Shipments on closed routes cannot be adjusted.")

        line = (
            ShipmentLine.objects.select_for_update()
            .select_related("shipment", "order_line", "product")
            .prefetch_related("order_line__picking_tasks")
            .get(pk=shipment_line_id)
        )
        if line.shipment_id != shipment.id:
            raise ValidationError("Shipment line does not belong to the selected shipment.")
        quantity_policy_error = product_quantity_error(line.product, quantity)
        if quantity_policy_error:
            raise ValidationError(quantity_policy_error)

        previous_effective = shipment_line_effective_quantity(line)
        if quantity > previous_effective:
            raise ValidationError("Removal quantity exceeds the current effective shipment quantity.")

        totals = shipment_line_task_totals(line)
        locked_quantity = max(totals["picked"], totals["prepared"])
        new_effective = previous_effective - quantity
        if new_effective < locked_quantity:
            raise ValidationError("Quantity already picked or controlled cannot be silently removed.")

        line.cancelled_quantity += quantity
        line.save(update_fields=["cancelled_quantity", "updated_at"])

        remaining_reduction = quantity
        tasks = list(line.order_line.picking_tasks.select_for_update().exclude(status=PickingTask.Status.CANCELLED).order_by("-quantity_to_pick", "id"))
        for task in tasks:
            if remaining_reduction <= 0:
                break
            task_locked_quantity = max(task.quantity_picked, task.quantity_prepared)
            reducible = max(task.quantity_to_pick - task_locked_quantity, Decimal("0"))
            reduction = min(reducible, remaining_reduction)
            if reduction <= 0:
                continue
            next_quantity_to_pick = task.quantity_to_pick - reduction
            if next_quantity_to_pick <= 0 and task_locked_quantity <= 0:
                task.status = PickingTask.Status.CANCELLED
                task.save(update_fields=["status", "updated_at"])
            else:
                task.quantity_to_pick = next_quantity_to_pick
                if task.quantity_to_pick <= task_locked_quantity and task.status in {
                    PickingTask.Status.OPEN,
                    PickingTask.Status.ASSIGNED,
                    PickingTask.Status.IN_PROGRESS,
                }:
                    task.status = PickingTask.Status.PICKED if task.quantity_picked >= task.quantity_to_pick else task.status
                task.save(update_fields=["quantity_to_pick", "status", "updated_at"])
            remaining_reduction -= reduction

        if shipment.document_status == Shipment.DocumentStatus.PRINTED:
            shipment.document_status = Shipment.DocumentStatus.REQUIRES_REFRESH
            shipment.save(update_fields=["document_status", "updated_at"])

        adjustment = ShipmentLineQuantityAdjustment.objects.create(
            shipment=shipment,
            shipment_line=line,
            quantity_removed=quantity,
            previous_effective_quantity=previous_effective,
            new_effective_quantity=new_effective,
            adjusted_by=user,
            reason=reason,
            client_operation_id=client_operation_id or None,
        )
        audit_shipment_event(
            user,
            shipment,
            "shipment_line_quantity_removed",
            f"{user.username} removed {quantity} from shipment {shipment.reference} line {line.line_number}.",
            result=reason,
        )
        if shipment.route_run:
            recalculate_route_readiness(shipment.route_run)
        return shipment, line, adjustment, False
