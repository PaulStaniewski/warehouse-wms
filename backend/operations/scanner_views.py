from decimal import Decimal
import random
import re
import uuid

from django.core import signing
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authorization import branch_ids_filter, require_branch_access
from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkParticipant,
    CartWorkSession,
    InterBranchTransfer,
    Order,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingJobTask,
    PickingShortage,
    PickingShortageAllocation,
    PickingTask,
    PickingTaskClaim,
    PickingTaskReallocation,
    ReplenishmentRequest,
    RouteRun,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    StockMovement,
    TransferDiscrepancy,
    TransferDiscrepancyItem,
    TransferPallet,
    TransferPalletArrival,
    TransferPalletItem,
)
from operations.contents import ContentsLookupError, resolve_contents_code
from operations.serializers import PickingTaskSerializer, RouteRunSerializer
from operations.services import (
    DiscrepancyLocationMissing,
    TERMINAL_ROUTE_STATUSES,
    discrepancy_line_remaining,
    get_discrepancy_location,
    get_discrepancy_investigation_totals,
    is_picking_job_work_fully_prepared,
    recalculate_route_readiness,
)
from warehouse.models import Branch, InventoryItem, Location, Product


User = get_user_model()
PICKING_SHORTAGE_CHALLENGE_SALT = "warehouse-wms-picking-shortage"
PICKING_SHORTAGE_CHALLENGE_MAX_AGE = 10 * 60


def _scanner_actor(request, worker_code=""):
    if request.user and request.user.is_authenticated:
        return request.user
    worker_code = str(worker_code or "").strip()
    if not worker_code:
        return None
    return User.objects.filter(username__iexact=worker_code).first()


def _scanner_actor_code(request, worker_code="") -> str:
    if request.user and request.user.is_authenticated:
        return request.user.username
    return str(worker_code or "").strip() or "scanner"


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
    return task.quantity_to_pick - task.quantity_picked - task.shortage_quantity


def _job_tasks(picking_job: PickingJob):
    return PickingTask.objects.select_related(
        "branch",
        "order_line__order__route_run__route",
        "order_line__product",
        "source_location",
    ).filter(job_task__picking_job=picking_job).exclude(status=PickingTask.Status.CANCELLED)


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
    total_quantity = sum((task.quantity_to_pick - task.shortage_quantity for task in tasks), Decimal("0"))
    picked_quantity = sum((task.quantity_picked for task in tasks), Decimal("0"))
    prepared_quantity = sum((task.quantity_prepared for task in tasks), Decimal("0"))
    remaining_lines = sum((task.quantity_picked + task.shortage_quantity) < task.quantity_to_pick for task in tasks)
    progress = round(float((picked_quantity / total_quantity) * 100), 1) if total_quantity > 0 else 0
    active_work = picking_job.cart_work_sessions.select_related("cart").filter(
        status__in=[CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL]
    ).first()
    active_participants = (
        []
        if active_work is None
        else list(_active_participants(active_work).values_list("user__username", flat=True))
    )

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
        "cart_work_session": active_work.id if active_work else None,
        "active_workers": active_participants,
        "active_workers_count": len(active_participants),
        "started_at": picking_job.started_at.isoformat() if picking_job.started_at else None,
        "completed_at": picking_job.completed_at.isoformat() if picking_job.completed_at else None,
        "created_at": picking_job.created_at.isoformat(),
    }


def _cart_work_branch(cart_work_session: CartWorkSession):
    route_run = cart_work_session.picking_job.route_runs.select_related("route", "route__branch").first()
    return route_run.route.branch if route_run is not None else None


def _active_participants(cart_work_session: CartWorkSession):
    return cart_work_session.participants.select_related(
        "user",
        "current_picking_task__order_line__product",
        "current_picking_task__source_location",
    ).filter(status=CartWorkParticipant.Status.ACTIVE)


def _participant_data(participant: CartWorkParticipant, *, current_user=None):
    task = participant.current_picking_task
    product = task.order_line.product if task else None
    location = task.source_location if task else None
    return {
        "id": participant.id,
        "user": participant.user_id,
        "username": participant.user.username,
        "display_name": participant.user.get_full_name() or participant.user.username,
        "branch": participant.branch_id,
        "branch_code": participant.branch.code,
        "status": participant.status,
        "picking_direction": participant.picking_direction,
        "picking_direction_label": participant.get_picking_direction_display(),
        "participant_work_state": participant.work_state,
        "participant_work_state_label": participant.get_work_state_display(),
        "is_current_user": bool(current_user and current_user.is_authenticated and participant.user_id == current_user.id),
        "current_picking_task": task.id if task else None,
        "current_product_sku": product.sku if product else None,
        "current_product_name": product.name if product else None,
        "current_location_code": location.code if location else None,
        "confirmed_location": participant.confirmed_location_id,
        "confirmed_location_code": participant.confirmed_location.code if participant.confirmed_location else None,
        "joined_at": participant.joined_at.isoformat(),
        "last_seen_at": participant.last_seen_at.isoformat(),
        "left_at": participant.left_at.isoformat() if participant.left_at else None,
    }


def _active_participant_data(cart_work_session: CartWorkSession, request=None):
    return [
        _participant_data(participant, current_user=getattr(request, "user", None))
        for participant in _active_participants(cart_work_session)
    ]


def _cart_work_session_data(cart_work_session: CartWorkSession, request=None):
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
        "participants": _active_participant_data(cart_work_session, request),
    }


def _release_participant_claim(participant: CartWorkParticipant):
    now = timezone.now()
    PickingTaskClaim.objects.filter(
        cart_work_participant=participant,
        status=PickingTaskClaim.Status.CLAIMED,
    ).update(status=PickingTaskClaim.Status.RELEASED, released_at=now, last_activity_at=now)
    participant.current_picking_task = None
    participant.current_task_claimed_at = None
    participant.confirmed_location = None
    participant.last_seen_at = now
    participant.save(
        update_fields=[
            "current_picking_task",
            "current_task_claimed_at",
            "confirmed_location",
            "last_seen_at",
            "updated_at",
        ]
    )


def _natural_location_sort_key(code: str):
    parts = re.split(r"(\d+)", str(code or ""))
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts)


def _task_manifest_sort_key(task: PickingTask):
    location_code = task.source_location.code if task.source_location else ""
    return (_natural_location_sort_key(location_code), task.created_at, task.id)


def _claimable_tasks_queryset(picking_job: PickingJob):
    claimed_task_ids = PickingTaskClaim.objects.filter(
        status=PickingTaskClaim.Status.CLAIMED,
        cart_work_participant__status=CartWorkParticipant.Status.ACTIVE,
    ).values_list("picking_task_id", flat=True)
    return (
        _current_pick_task_queryset(picking_job)
        .select_for_update(of=("self",))
        .exclude(id__in=claimed_task_ids)
    )


def _ordered_claimable_tasks(picking_job: PickingJob, *, reverse=False):
    tasks = list(_claimable_tasks_queryset(picking_job))
    return sorted(tasks, key=_task_manifest_sort_key, reverse=reverse)


def _has_unresolved_cart_work(picking_job: PickingJob):
    return _job_tasks(picking_job).filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity")).exists()


def _claim_task_for_participant(
    cart_work_session: CartWorkSession,
    participant: CartWorkParticipant,
    *,
    task: PickingTask | None = None,
    mode="beginning",
    keep_existing=True,
):
    existing_claim = (
        PickingTaskClaim.objects.select_for_update()
        .select_related("picking_task")
        .filter(cart_work_participant=participant, status=PickingTaskClaim.Status.CLAIMED)
        .first()
    )
    if keep_existing and task is None and existing_claim is not None and _task_remaining(existing_claim.picking_task) > 0:
        return existing_claim.picking_task, existing_claim, False

    if mode in [CartWorkParticipant.PickingDirection.BEGINNING, CartWorkParticipant.PickingDirection.END]:
        participant.picking_direction = mode
    elif task is not None:
        participant.picking_direction = CartWorkParticipant.PickingDirection.MANUAL

    if existing_claim is not None and (task is None or existing_claim.picking_task_id != task.id):
        existing_claim.status = (
            PickingTaskClaim.Status.COMPLETED
            if _task_remaining(existing_claim.picking_task) <= 0
            else PickingTaskClaim.Status.RELEASED
        )
        existing_claim.released_at = timezone.now()
        existing_claim.last_activity_at = timezone.now()
        existing_claim.save(update_fields=["status", "released_at", "last_activity_at", "updated_at"])

    if task is None:
        if mode == CartWorkParticipant.PickingDirection.MANUAL:
            task = None
        else:
            candidates = _ordered_claimable_tasks(cart_work_session.picking_job, reverse=mode == "end")
            task = candidates[0] if candidates else None
    else:
        if _task_remaining(task) <= 0 or task.status in [PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED]:
            return None, None, False
        other_claim = (
            PickingTaskClaim.objects.select_for_update()
            .select_related("cart_work_participant__user")
            .filter(
                picking_task=task,
                status=PickingTaskClaim.Status.CLAIMED,
                cart_work_participant__status=CartWorkParticipant.Status.ACTIVE,
            )
            .exclude(cart_work_participant=participant)
            .first()
        )
        if other_claim is not None:
            task.claim_conflict_username = other_claim.cart_work_participant.user.username
            return None, None, False
        if existing_claim is not None and existing_claim.picking_task_id == task.id:
            participant.work_state = CartWorkParticipant.WorkState.ACTIVE
            participant.last_seen_at = timezone.now()
            participant.save(update_fields=["picking_direction", "work_state", "last_seen_at", "updated_at"])
            return task, existing_claim, False

    if task is None:
        participant.current_picking_task = None
        participant.current_task_claimed_at = None
        participant.confirmed_location = None
        participant.work_state = (
            CartWorkParticipant.WorkState.WAITING_FOR_AVAILABLE_LINE
            if _has_unresolved_cart_work(cart_work_session.picking_job)
            else CartWorkParticipant.WorkState.COMPLETED_PARTICIPATION
        )
        participant.last_seen_at = timezone.now()
        participant.save(
            update_fields=[
                "picking_direction",
                "work_state",
                "current_picking_task",
                "current_task_claimed_at",
                "confirmed_location",
                "last_seen_at",
                "updated_at",
            ]
        )
        return None, None, False

    _release_participant_claim(participant)
    claim = PickingTaskClaim.objects.create(picking_task=task, cart_work_participant=participant)
    participant.current_picking_task = task
    participant.current_task_claimed_at = claim.claimed_at
    participant.confirmed_location = None
    participant.work_state = CartWorkParticipant.WorkState.ACTIVE
    participant.last_seen_at = timezone.now()
    participant.save(
        update_fields=[
            "picking_direction",
            "work_state",
            "current_picking_task",
            "current_task_claimed_at",
            "confirmed_location",
            "last_seen_at",
            "updated_at",
        ]
    )
    return task, claim, True


def _participant_for_request(cart_work_session: CartWorkSession, request, *, create_missing=False, lock=False, touch=False):
    if not request.user or not request.user.is_authenticated:
        return None, None

    branch = _cart_work_branch(cart_work_session)
    if branch is None:
        return None, Response({"detail": "Cart work branch could not be resolved."}, status=status.HTTP_400_BAD_REQUEST)
    require_branch_access(request.user, branch)

    participant_queryset = CartWorkParticipant.objects.select_related(
        "user", "branch", "confirmed_location", "current_picking_task"
    ).filter(
        cart_work_session=cart_work_session,
        user=request.user,
        status=CartWorkParticipant.Status.ACTIVE,
    )
    if lock:
        participant_queryset = participant_queryset.select_for_update(of=("self",))
    participant = participant_queryset.first()

    if participant is None and create_missing:
        if not lock:
            return None, Response(
                {"detail": "Participant creation requires a locked transaction."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        existing_active = (
            CartWorkParticipant.objects.select_for_update(of=("self",))
            .select_related("cart_work_session__cart")
            .filter(user=request.user, status=CartWorkParticipant.Status.ACTIVE)
            .exclude(cart_work_session=cart_work_session)
            .first()
        )
        if existing_active is not None:
            return None, Response(
                {"detail": f"You already have active work on {existing_active.cart_work_session.cart.code}."},
                status=status.HTTP_409_CONFLICT,
            )
        participant = CartWorkParticipant.objects.create(
            cart_work_session=cart_work_session,
            user=request.user,
            branch=branch,
        )
        participant.was_created = True
    elif participant is not None:
        participant.was_created = False
    if participant is not None and touch:
        participant.last_seen_at = timezone.now()
        participant.save(update_fields=["last_seen_at", "updated_at"])
    return participant, None


def _cart_work_response(cart_work_session: CartWorkSession, request, repair_messages=None):
    participant, error = _participant_for_request(cart_work_session, request, create_missing=False)
    if error is not None:
        return error
    if request.user and request.user.is_authenticated and participant is None:
        return Response({"detail": "You are not an active participant in this cart work."}, status=status.HTTP_403_FORBIDDEN)

    tasks = sorted(_job_tasks(cart_work_session.picking_job), key=_task_manifest_sort_key)
    state, confirmed_location_code, instruction = _picking_state(cart_work_session, participant)
    return Response(
        {
            "cart_work_session": _cart_work_session_data(cart_work_session, request),
            "state": state,
            "confirmed_location_code": confirmed_location_code,
            "current_instruction": instruction,
            "participant": _participant_data(participant, current_user=getattr(request, "user", None)) if participant else None,
            "repair_messages": repair_messages or [],
            "tasks": [_task_manifest_data(task, participant) for task in tasks],
        }
    )


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
        .filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity"))
        .order_by("source_location__code", "created_at", "id")
    )


def _own_reallocation_reserved_quantity(task: PickingTask):
    reallocation = getattr(task, "system_reallocation_source", None)
    if reallocation is not None:
        return max(reallocation.quantity - task.quantity_picked, Decimal("0"))

    allocation = getattr(task, "shortage_replacement_allocation", None)
    if allocation is not None:
        return max(allocation.quantity - allocation.picked_quantity, Decimal("0"))

    return Decimal("0")


def _available_quantity_for_task(item: InventoryItem, task: PickingTask):
    return item.quantity_on_hand - item.quantity_reserved + _own_reallocation_reserved_quantity(task)


def _eligible_alternative_inventory(task: PickingTask):
    return (
        InventoryItem.objects.select_for_update()
        .select_related("location", "branch", "product")
        .filter(
            branch=task.branch,
            product=task.order_line.product,
            location__is_active=True,
            location__location_type__in=[Location.LocationType.PICKING, Location.LocationType.STORAGE],
            quantity_on_hand__gt=F("quantity_reserved"),
        )
        .exclude(location=task.source_location)
        .exclude(location__code__iexact="UNCONFIRMED")
        .order_by("location__code", "id")
    )


def _repair_stale_current_task(cart_work_session: CartWorkSession, request=None):
    task = _current_pick_task_queryset(cart_work_session.picking_job).select_for_update(of=("self",)).first()
    if task is None:
        return []

    if (
        hasattr(task, "system_reallocation_source")
        or hasattr(task, "shortage_replacement_allocation")
        or task.system_reallocations.exists()
    ):
        return []

    remaining = _task_remaining(task)
    if remaining <= 0:
        return []

    product = task.order_line.product
    source_item = (
        InventoryItem.objects.select_for_update()
        .filter(branch=task.branch, location=task.source_location, product=product)
        .first()
    )
    source_available = _available_quantity_for_task(source_item, task) if source_item is not None else Decimal("0")
    if source_available >= remaining:
        return []

    source_available = max(source_available, Decimal("0"))
    uncovered_quantity = remaining - source_available
    original_location = task.source_location
    original_quantity = task.quantity_to_pick
    if source_available > 0:
        task.quantity_to_pick = task.quantity_picked + task.shortage_quantity + source_available
        task.status = PickingTask.Status.IN_PROGRESS if task.quantity_picked > 0 else PickingTask.Status.OPEN
        task.save(update_fields=["quantity_to_pick", "status", "updated_at"])
    else:
        task.status = PickingTask.Status.CANCELLED
        task.save(update_fields=["status", "updated_at"])

    actor = _scanner_actor(request, cart_work_session.scanner_session.worker_code if request else "")
    worker_code = _scanner_actor_code(request, cart_work_session.scanner_session.worker_code if request else "")
    messages = []
    allocated_total = Decimal("0")
    for item in _eligible_alternative_inventory(task):
        if uncovered_quantity <= 0:
            break
        available = item.quantity_on_hand - item.quantity_reserved
        if available <= 0:
            continue
        quantity = min(uncovered_quantity, available)
        replacement_task = PickingTask.objects.create(
            branch=task.branch,
            order_line=task.order_line,
            source_location=item.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=quantity,
            quantity_picked=Decimal("0"),
            quantity_prepared=Decimal("0"),
            shortage_quantity=Decimal("0"),
        )
        PickingJobTask.objects.create(picking_job=cart_work_session.picking_job, picking_task=replacement_task)
        reallocation = PickingTaskReallocation.objects.create(
            original_picking_task=task,
            replacement_picking_task=replacement_task,
            branch=task.branch,
            product=product,
            original_location=original_location,
            replacement_location=item.location,
            quantity=quantity,
        )
        item.quantity_reserved = F("quantity_reserved") + quantity
        item.save(update_fields=["quantity_reserved", "updated_at"])
        uncovered_quantity -= quantity
        allocated_total += quantity
        message = (
            f"Picking task for {_piece_value(quantity)} {product.sku} was reallocated from "
            f"{original_location.code} to {item.location.code} because no available stock remained at the original location."
        )
        messages.append(message)
        AuditLog.objects.create(
            actor=actor,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="picking_task_reallocated",
            branch=task.branch,
            product=product,
            quantity=quantity,
            source_location=original_location,
            destination_location=item.location,
            source_label=original_location.code,
            destination_label=item.location.code,
            cart=cart_work_session.cart,
            order=task.order_line.order,
            route_run=task.order_line.order.route_run,
            reference=task.order_line.order.external_reference,
            result="reallocated",
            entity_name="PickingTaskReallocation",
            entity_id=str(reallocation.id),
            message=message,
        )

    if uncovered_quantity > 0:
        order = task.order_line.order
        customer_alias = order.customer_alias or order.customer_name
        replenishment = ReplenishmentRequest.objects.filter(
            picking_task=task,
            reason=ReplenishmentRequest.Reason.SYSTEM_STOCK_UNAVAILABLE,
            status=ReplenishmentRequest.Status.PENDING_ORDER,
        ).first()
        if replenishment is None:
            replenishment = ReplenishmentRequest.objects.create(
                picking_task=task,
                branch=task.branch,
                customer_alias=customer_alias,
                order_reference=order.external_reference,
                product=product,
                quantity=uncovered_quantity,
                reason=ReplenishmentRequest.Reason.SYSTEM_STOCK_UNAVAILABLE,
                created_by=actor,
                note=f"System stock unavailable at {original_location.code}. Original task quantity was {original_quantity}.",
            )
            AuditLog.objects.create(
                actor=actor,
                action_type=AuditLog.ActionType.CREATE,
                event_type="replenishment_requested",
                branch=task.branch,
                product=product,
                quantity=uncovered_quantity,
                cart=cart_work_session.cart,
                order=order,
                route_run=order.route_run,
                reference=replenishment.reference,
                result="system_stock_unavailable",
                entity_name="ReplenishmentRequest",
                entity_id=str(replenishment.id),
                message=(
                    f"Replenishment request {replenishment.reference} was created for customer {customer_alias}: "
                    f"{_piece_value(uncovered_quantity)} {product.sku} could not be allocated from branch stock."
                ),
            )
        elif replenishment.quantity != uncovered_quantity:
            replenishment.quantity = uncovered_quantity
            replenishment.save(update_fields=["quantity", "updated_at"])

    if allocated_total > 0:
        cart_work_session.confirmed_location = None
        cart_work_session.save(update_fields=["confirmed_location", "updated_at"])

    return messages


def _repair_stale_current_tasks(cart_work_session: CartWorkSession, request=None):
    messages = []
    for _ in range(10):
        repaired_messages = _repair_stale_current_task(cart_work_session, request)
        if not repaired_messages:
            break
        messages.extend(repaired_messages)
        cart_work_session.refresh_from_db()
    return messages


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
            "brand": product.brand,
            "description": product.description,
            "image_url": product.image_url,
        },
        "order_reference": task.order_line.order.external_reference,
        "required_quantity": str(task.quantity_to_pick),
        "picked_quantity": str(task.quantity_picked),
        "shortage_quantity": str(task.shortage_quantity),
        "remaining_quantity": str(task.quantity_to_pick - task.quantity_picked - task.shortage_quantity),
        "customer_alias": task.order_line.order.customer_alias or task.order_line.order.customer_name,
    }


def _task_manifest_data(task: PickingTask, participant: CartWorkParticipant | None = None):
    data = PickingTaskSerializer(task).data
    active_claim = (
        PickingTaskClaim.objects.select_related("cart_work_participant__user")
        .filter(picking_task=task, status=PickingTaskClaim.Status.CLAIMED)
        .first()
    )
    data["claim_status"] = active_claim.status if active_claim else None
    data["claimed_by"] = active_claim.cart_work_participant_id if active_claim else None
    data["claimed_by_username"] = active_claim.cart_work_participant.user.username if active_claim else None
    data["is_claimed_by_current_user"] = bool(
        active_claim and participant and active_claim.cart_work_participant_id == participant.id
    )
    return data


def _current_pick_instruction(cart_work_session: CartWorkSession, participant: CartWorkParticipant | None = None):
    if participant is not None:
        task = (
            PickingTask.objects.select_related(
                "branch",
                "order_line__order__route_run",
                "order_line__product",
                "source_location",
            )
            .filter(pk=participant.current_picking_task_id, job_task__picking_job=cart_work_session.picking_job)
            .exclude(status__in=[PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED])
            .filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity"))
            .first()
        )
        return _pick_instruction_data(task)
    return _pick_instruction_data(_current_pick_task_queryset(cart_work_session.picking_job).first())


def _picking_state(cart_work_session: CartWorkSession, participant: CartWorkParticipant | None = None):
    instruction = _current_pick_instruction(cart_work_session, participant)
    confirmed_code = None
    state = "completed"
    if instruction is not None:
        confirmed_location = participant.confirmed_location if participant is not None else cart_work_session.confirmed_location
        confirmed_code = confirmed_location.code if confirmed_location else None
        state = (
            "waiting_for_product"
            if confirmed_location is not None and confirmed_location.id == instruction["location"]["id"]
            else "waiting_for_location"
        )
    elif participant is not None and participant.work_state == CartWorkParticipant.WorkState.WAITING_FOR_AVAILABLE_LINE:
        state = "waiting_for_available_line"
    elif _has_unresolved_cart_work(cart_work_session.picking_job):
        state = "waiting_for_available_line"
    return state, confirmed_code, instruction


def _piece_value(value):
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def _picking_shortage_summary(task: PickingTask, quantity: Decimal, cart_work_session: CartWorkSession):
    order = task.order_line.order
    product = task.order_line.product
    return {
        "picking_task_id": task.id,
        "product_sku": product.sku,
        "product_name": product.name,
        "product_brand": product.brand,
        "branch_code": task.branch.code,
        "location_code": task.source_location.code,
        "order_reference": order.external_reference,
        "customer_alias": order.customer_alias or order.customer_name,
        "cart_code": cart_work_session.cart.code,
        "required_quantity": str(task.quantity_to_pick),
        "picked_quantity": str(task.quantity_picked),
        "shortage_quantity": str(quantity),
    }


def _allocation_data(allocation: PickingShortageAllocation):
    return {
        "id": allocation.id,
        "location": allocation.source_location_id,
        "location_code": allocation.source_location.code,
        "location_name": allocation.source_location.name,
        "quantity": str(allocation.quantity),
        "picked_quantity": str(allocation.picked_quantity),
        "status": allocation.status,
        "replacement_picking_task": allocation.replacement_picking_task_id,
    }


def _shortage_response(
    shortage: PickingShortage,
    replenishment: ReplenishmentRequest | None,
    cart_work_session: CartWorkSession,
):
    state, confirmed_location_code, instruction = _picking_state(cart_work_session)
    allocations = list(shortage.allocations.select_related("source_location").order_by("source_location__code", "id"))
    allocated_quantity = sum((allocation.quantity for allocation in allocations), Decimal("0"))
    residual_quantity = shortage.customer_unfulfilled_quantity
    if residual_quantity > 0 and allocated_quantity > 0:
        message = (
            f"Missing stock at {shortage.reported_location.code} was recorded. "
            f"{_piece_value(allocated_quantity)} x {shortage.product.sku} was allocated from alternative locations. "
            f"{_piece_value(residual_quantity)} remains for customer replenishment."
        )
    elif allocated_quantity > 0:
        message = (
            f"Missing stock at {shortage.reported_location.code} was recorded. "
            f"{_piece_value(allocated_quantity)} x {shortage.product.sku} was allocated from alternative locations."
        )
    else:
        message = (
            f"Missing stock at {shortage.reported_location.code} was recorded. "
            f"{_piece_value(residual_quantity)} x {shortage.product.sku} requires customer replenishment."
        )

    return {
        "message": message,
        "shortage": {
            "id": shortage.id,
            "reference": shortage.reference,
            "quantity": str(shortage.quantity),
            "location_missing_quantity": str(shortage.location_missing_quantity),
            "alternative_allocated_quantity": str(shortage.alternative_allocated_quantity),
            "customer_unfulfilled_quantity": str(shortage.customer_unfulfilled_quantity),
            "unresolved_unconfirmed_quantity": str(shortage.unresolved_unconfirmed_quantity),
            "status": shortage.status,
            "product_sku": shortage.product.sku,
            "reported_location_code": shortage.reported_location.code,
            "unconfirmed_location_code": shortage.unconfirmed_location.code,
            "allocations": [_allocation_data(allocation) for allocation in allocations],
        },
        "alternative_allocations": [_allocation_data(allocation) for allocation in allocations],
        "replenishment_request": (
            {
                "id": replenishment.id,
                "reference": replenishment.reference,
                "status": replenishment.status,
                "quantity": str(replenishment.quantity),
            }
            if replenishment is not None
            else None
        ),
        "task": PickingTaskSerializer(shortage.picking_task).data,
        "picking_job": _job_summary(cart_work_session.picking_job),
        "cart_work_session": _cart_work_session_data(cart_work_session),
        "state": state,
        "confirmed_location_code": confirmed_location_code,
        "current_instruction": instruction,
    }


def _allocate_alternative_stock(
    *,
    shortage: PickingShortage,
    original_task: PickingTask,
    picking_job: PickingJob,
    quantity_needed: Decimal,
    actor,
    worker_code: str,
) -> Decimal:
    remaining = quantity_needed
    allocated_total = Decimal("0")
    product = original_task.order_line.product
    inventory_items = (
        InventoryItem.objects.select_for_update()
        .select_related("location", "branch", "product")
        .filter(
            branch=original_task.branch,
            product=product,
            location__is_active=True,
            location__location_type__in=[Location.LocationType.PICKING, Location.LocationType.STORAGE],
            quantity_on_hand__gt=F("quantity_reserved"),
        )
        .exclude(location=original_task.source_location)
        .exclude(location__code__iexact="UNCONFIRMED")
        .order_by("location__code", "id")
    )

    for item in inventory_items:
        if remaining <= 0:
            break

        available = item.quantity_on_hand - item.quantity_reserved
        if available <= 0:
            continue

        quantity = min(remaining, available)
        replacement_task = PickingTask.objects.create(
            branch=original_task.branch,
            order_line=original_task.order_line,
            source_location=item.location,
            status=PickingTask.Status.OPEN,
            quantity_to_pick=quantity,
            quantity_picked=Decimal("0"),
            quantity_prepared=Decimal("0"),
            shortage_quantity=Decimal("0"),
        )
        PickingJobTask.objects.create(picking_job=picking_job, picking_task=replacement_task)
        allocation = PickingShortageAllocation.objects.create(
            shortage=shortage,
            original_picking_task=original_task,
            replacement_picking_task=replacement_task,
            branch=original_task.branch,
            product=product,
            source_location=item.location,
            quantity=quantity,
        )
        item.quantity_reserved = F("quantity_reserved") + quantity
        item.save(update_fields=["quantity_reserved", "updated_at"])
        allocated_total += quantity
        remaining -= quantity

        AuditLog.objects.create(
            actor=actor,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="alternative_stock_allocated",
            branch=original_task.branch,
            product=product,
            quantity=quantity,
            source_location=item.location,
            source_label=item.location.code,
            cart=shortage.cart,
            order=original_task.order_line.order,
            route_run=original_task.order_line.order.route_run,
            reference=shortage.reference,
            entity_name="PickingShortageAllocation",
            entity_id=str(allocation.id),
            message=(
                f"Worker {worker_code} allocated {_piece_value(quantity)} {product.sku} "
                f"from alternative location {item.location.code} for shortage {shortage.reference}."
            ),
        )

    return allocated_total


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


def _mm_task_data(pallet):
    items = list(pallet.items.all())
    expected = sum((item.expected_quantity for item in items), Decimal("0"))
    put_away = sum((item.received_quantity for item in items), Decimal("0"))
    return {
        "pallet_id": pallet.id,
        "pallet_code": pallet.scan_code,
        "transfer_id": pallet.transfer_id,
        "transfer_reference": pallet.transfer.reference,
        "source_branch": pallet.transfer.source_branch.code,
        "destination_branch": pallet.transfer.destination_branch.code,
        "arrived_at": pallet.arrival.scanned_at,
        "expected_units": _piece_value(expected),
        "put_away_units": _piece_value(put_away),
        "remaining_units": _piece_value(max(expected - put_away, Decimal("0"))),
        "line_count": len(items),
        "status": "receiving" if pallet.status == TransferPallet.Status.RECEIVING else "waiting_for_receiving",
    }


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
            missing_unit = "unit" if total_missing == 1 else "units"
            line_word = "line" if len(shortages) == 1 else "lines"
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                entity_name="TransferPallet",
                entity_id=str(pallet.id),
                message=(
                    f"Worker {session.worker_code or 'scanner'} closed pallet {pallet.scan_code} "
                    f"with discrepancies: {_piece_value(total_missing)} missing {missing_unit} across {len(shortages)} {line_word}."
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

        if not AuditLog.objects.filter(event_type="mm_task_completed", pallet=pallet).exists():
            outcome = "with discrepancy" if shortages else "after receiving"
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                event_type="mm_task_completed",
                branch=pallet.transfer.destination_branch,
                transfer=pallet.transfer,
                pallet=pallet,
                source_label=pallet.transfer.source_branch.code,
                destination_label=pallet.transfer.destination_branch.code,
                result="discrepancy" if shortages else "completed",
                reference=pallet.transfer.reference,
                entity_name="TransferPalletArrival",
                entity_id=str(pallet.arrival.id),
                message=f"MM task for pallet {pallet.scan_code} was completed {outcome}.",
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

    worker_code = _scanner_actor_code(request, request.data.get("worker_code", ""))

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
        participant, error = _participant_for_request(cart_work_session, request, create_missing=True, lock=True, touch=True)
        if error is not None:
            return error
        if participant is not None:
            task, claim, _ = _claim_task_for_participant(cart_work_session, participant)
            if task is None:
                return Response({"detail": "Picking job has no remaining work."}, status=status.HTTP_400_BAD_REQUEST)
            task = (
                PickingTask.objects.select_for_update(of=("self",))
                .select_related("branch", "order_line__order__route_run", "order_line__product", "source_location")
                .get(pk=task.id)
            )
            claim = (
                PickingTaskClaim.objects.select_for_update()
                .filter(
                    picking_task=task,
                    cart_work_participant=participant,
                    status=PickingTaskClaim.Status.CLAIMED,
                )
                .first()
            )
            if claim is None:
                return Response({"detail": "Pick this line before scanning product."}, status=status.HTTP_409_CONFLICT)
            confirmed_location = participant.confirmed_location
        else:
            task = _current_pick_task_queryset(picking_job).select_for_update(of=("self",)).first()
            claim = None
            confirmed_location = cart_work_session.confirmed_location
        if task is None:
            return Response({"detail": "Picking job has no remaining work."}, status=status.HTTP_400_BAD_REQUEST)

        if confirmed_location is None:
            return Response(
                {"detail": "Scan the expected location before scanning the product."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if confirmed_location.id != task.source_location_id:
            return Response(
                {"detail": f"Wrong location. Go to {task.source_location.code}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order_line = task.order_line
        product = order_line.product
        if product_code.lower() not in {product.sku.lower(), (product.barcode or "").lower()}:
            return Response({"detail": f"Wrong product. Expected {product.sku}."}, status=status.HTTP_400_BAD_REQUEST)

        task_remaining = task.quantity_to_pick - task.quantity_picked - task.shortage_quantity
        order_remaining = order_line.quantity_ordered - order_line.quantity_picked
        if quantity > task_remaining or quantity > order_remaining:
            return Response(
                {"detail": "Quantity exceeds remaining picking quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inventory_item = (
            InventoryItem.objects.select_for_update()
            .filter(branch=task.branch, location=confirmed_location, product=product)
            .first()
        )
        if inventory_item is None:
            return Response({"detail": "No inventory found at the source location."}, status=status.HTTP_400_BAD_REQUEST)
        if inventory_item.quantity_on_hand < quantity:
            return Response(
                {"detail": "Not enough stock at the confirmed location."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        replacement_allocation = (
            PickingShortageAllocation.objects.select_for_update()
            .filter(replacement_picking_task=task)
            .first()
        )
        system_reallocation = (
            PickingTaskReallocation.objects.select_for_update()
            .filter(replacement_picking_task=task)
            .first()
        )
        new_allocation_picked_quantity = None
        if replacement_allocation is not None or system_reallocation is not None:
            if inventory_item.quantity_reserved < quantity:
                return Response(
                    {"detail": "Quantity exceeds the reserved reallocated quantity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if replacement_allocation is not None:
            new_allocation_picked_quantity = replacement_allocation.picked_quantity + quantity
            if new_allocation_picked_quantity > replacement_allocation.quantity:
                return Response(
                    {"detail": "Quantity exceeds the remaining replacement allocation."},
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
            if claim is not None:
                claim.status = PickingTaskClaim.Status.COMPLETED
                claim.released_at = timezone.now()
                claim.last_activity_at = timezone.now()
                claim.save(update_fields=["status", "released_at", "last_activity_at", "updated_at"])

        order_line.quantity_picked = F("quantity_picked") + quantity
        order_line.save(update_fields=["quantity_picked", "updated_at"])

        inventory_item.quantity_on_hand = F("quantity_on_hand") - quantity
        if replacement_allocation is not None or system_reallocation is not None:
            inventory_item.quantity_reserved = F("quantity_reserved") - quantity
            inventory_item.save(update_fields=["quantity_on_hand", "quantity_reserved", "updated_at"])
        else:
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])

        if replacement_allocation is not None:
            replacement_allocation.picked_quantity = new_allocation_picked_quantity
            replacement_allocation.status = (
                PickingShortageAllocation.Status.COMPLETED
                if new_allocation_picked_quantity >= replacement_allocation.quantity
                else PickingShortageAllocation.Status.PICKING
            )
            replacement_allocation.save(update_fields=["picked_quantity", "status", "updated_at"])

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

        if not _job_tasks(picking_job).filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity")).exists():
            picking_job.status = PickingJob.Status.PICKED
            picking_job.save(update_fields=["status", "updated_at"])

        next_task = _current_pick_task_queryset(picking_job).first()
        if participant is not None:
            _claim_task_for_participant(cart_work_session, participant, mode=participant.picking_direction)
            participant.refresh_from_db()
            next_participant_task = participant.current_picking_task
            if next_participant_task is None or next_participant_task.source_location_id != confirmed_location.id:
                participant.confirmed_location = None
                participant.save(update_fields=["confirmed_location", "updated_at"])
        elif next_task is None or next_task.source_location_id != cart_work_session.confirmed_location_id:
            cart_work_session.confirmed_location = None
            cart_work_session.save(update_fields=["confirmed_location", "updated_at"])
            cart_work_session.refresh_from_db()

        AuditLog.objects.create(
            actor=_scanner_actor(request, worker_code),
            action_type=AuditLog.ActionType.UPDATE,
            event_type="pick",
            branch=task.branch,
            product=product,
            quantity=quantity,
            source_location=task.source_location,
            source_label=task.source_location.code,
            destination_label=cart_work_session.cart.code,
            cart=cart_work_session.cart,
            order=order_line.order,
            route_run=order_line.order.route_run,
            reference=order_line.order.external_reference,
            entity_name="PickingJob",
            entity_id=str(picking_job.id),
            message=(
                f"Worker {worker_code} picked {_piece_value(quantity)} {product.sku} "
                f"from location {task.source_location.code} to cart {cart_work_session.cart.code} "
                f"for order {order_line.order.external_reference}."
            ),
        )
        repair_messages = _repair_stale_current_tasks(cart_work_session, request)

    cart_work_session.refresh_from_db()
    participant = None
    if request.user and request.user.is_authenticated:
        participant = (
            CartWorkParticipant.objects.select_related("user", "branch", "confirmed_location", "current_picking_task")
            .filter(cart_work_session=cart_work_session, user=request.user, status=CartWorkParticipant.Status.ACTIVE)
            .first()
        )
    state, confirmed_location_code, instruction = _picking_state(cart_work_session, participant)
    return Response(
        {
            "message": "Pick scan accepted.",
            "repair_messages": repair_messages,
            "task": PickingTaskSerializer(task).data,
            "picking_job": _job_summary(picking_job),
            "cart_work_session": _cart_work_session_data(cart_work_session, request),
            "state": state,
            "confirmed_location_code": confirmed_location_code,
            "current_instruction": instruction,
            "participant": _participant_data(participant, current_user=getattr(request, "user", None)) if participant else None,
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
            .filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity"))
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
        task_remaining = task.quantity_to_pick - task.quantity_picked - task.shortage_quantity
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

        if task.quantity_picked + task.shortage_quantity >= task.quantity_to_pick:
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
            event_type="pick",
            branch=task.branch,
            product=product,
            quantity=quantity,
            source_location=task.source_location,
            source_label=task.source_location.code,
            destination_label=session.cart.code if session is not None else "",
            cart=session.cart if session is not None else None,
            order=order_line.order,
            route_run=route_run,
            reference=order_line.order.external_reference,
            entity_name="PickingTask",
            entity_id=str(task.id),
            message=(
                f"Scanner picking picked {_piece_value(quantity)} of {product.sku} "
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

    actor = _scanner_actor(request, session.worker_code)
    actor_code = _scanner_actor_code(request, session.worker_code)

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
            matched_cart_item = (
                CartPickedItem.objects.select_related("cart", "route_run", "picking_task", "picking_task__branch")
                .filter(session=session, product=product, picking_task__order_line__order=order)
                .order_by("created_at", "id")
                .first()
            )
            if matched_cart_item is not None:
                AuditLog.objects.create(
                    actor=actor,
                    action_type=AuditLog.ActionType.UPDATE,
                    event_type="control_mismatch",
                    branch=matched_cart_item.picking_task.branch,
                    product=product,
                    quantity=quantity,
                    expected_quantity=Decimal("0"),
                    checked_quantity=quantity,
                    source_label=matched_cart_item.cart.code,
                    destination_label="Control",
                    cart=matched_cart_item.cart,
                    order=order,
                    route_run=matched_cart_item.route_run,
                    result="mismatch",
                    reference=order.external_reference,
                    entity_name="CartPickedItem",
                    entity_id=str(matched_cart_item.id),
                    message=(
                        f"Worker {actor_code} found a quantity mismatch for {product.sku} "
                        f"on cart {session.cart.code} for order {order.external_reference}. "
                        f"Expected 0, checked {_piece_value(quantity)}."
                    ),
                )
            return Response(
                {"detail": "Product is not available on the active cart for this order."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if cart_item.route_run.status in TERMINAL_ROUTE_STATUSES:
            return Response({"detail": "Route run is closed and cannot be controlled."}, status=status.HTTP_400_BAD_REQUEST)

        available_to_prepare = cart_item.quantity_picked - cart_item.quantity_prepared
        if quantity > available_to_prepare:
            AuditLog.objects.create(
                actor=actor,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="control_mismatch",
                branch=cart_item.picking_task.branch,
                product=product,
                quantity=quantity,
                expected_quantity=available_to_prepare,
                checked_quantity=quantity,
                source_label=cart_item.cart.code,
                destination_label="Control",
                cart=cart_item.cart,
                order=order,
                route_run=cart_item.route_run,
                result="mismatch",
                reference=order.external_reference,
                entity_name="CartPickedItem",
                entity_id=str(cart_item.id),
                message=(
                    f"Worker {actor_code} found a quantity mismatch for {product.sku} "
                    f"on cart {session.cart.code} for order {order.external_reference}. "
                    f"Expected {_piece_value(available_to_prepare)}, checked {_piece_value(quantity)}."
                ),
            )
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
            actor=actor,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="control",
            branch=task.branch,
            product=product,
            quantity=quantity,
            expected_quantity=quantity,
            checked_quantity=quantity,
            source_label=session.cart.code,
            destination_label="Checked",
            cart=session.cart,
            order=order,
            route_run=cart_item.route_run,
            result="passed",
            reference=order.external_reference,
            entity_name="PickingTask",
            entity_id=str(task.id),
            message=(
                f"Worker {actor_code} verified {_piece_value(quantity)} {product.sku} on cart {session.cart.code} "
                f"for order {order.external_reference}."
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


class ScannerPickingShortageChallengeView(APIView):
    def post(self, request):
        cart_work_session_id = request.data.get("cart_work_session_id")
        quantity, error = _parse_positive_piece_quantity(request.data.get("quantity", 1))
        if error is not None:
            return error
        worker_code = _scanner_actor_code(request, request.data.get("worker_code", ""))

        with transaction.atomic():
            cart_work_session = (
                CartWorkSession.objects.select_for_update(of=("self",))
                .select_related("cart", "picking_job", "scanner_session", "confirmed_location")
                .filter(pk=cart_work_session_id)
                .first()
            )
            if cart_work_session is None:
                return Response({"detail": "Cart work session not found."}, status=status.HTTP_404_NOT_FOUND)
            _repair_stale_current_tasks(cart_work_session, request)
            participant, error = _participant_for_request(cart_work_session, request, create_missing=True, lock=True, touch=True)
            if error is not None:
                return error
            if participant is not None:
                task, claim, _ = _claim_task_for_participant(cart_work_session, participant)
                if task is not None:
                    task = (
                        PickingTask.objects.select_for_update(of=("self",))
                        .select_related("branch", "order_line__order", "order_line__product", "source_location")
                        .get(pk=task.id)
                    )
                confirmed_location_id = participant.confirmed_location_id
            else:
                task = _current_pick_task_queryset(cart_work_session.picking_job).select_for_update(of=("self",)).first()
                confirmed_location_id = cart_work_session.confirmed_location_id
            if task is None:
                return Response({"detail": "Picking job has no remaining work."}, status=status.HTTP_400_BAD_REQUEST)
            if confirmed_location_id != task.source_location_id:
                return Response({"detail": "Scan the expected location before reporting missing stock."}, status=status.HTTP_400_BAD_REQUEST)

            remaining = task.quantity_to_pick - task.quantity_picked - task.shortage_quantity
            if quantity > remaining:
                return Response({"detail": "Missing quantity exceeds remaining picking quantity."}, status=status.HTTP_400_BAD_REQUEST)

            source_item = (
                InventoryItem.objects.select_for_update()
                .filter(branch=task.branch, location=task.source_location, product=task.order_line.product)
                .first()
            )
            source_available = _available_quantity_for_task(source_item, task) if source_item is not None else Decimal("0")
            if source_available < quantity:
                return Response(
                    {"detail": "System stock at this location is already insufficient. Refresh picking to get an updated location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            code = f"{random.SystemRandom().randint(0, 9999):04d}"
            payload = {
                "nonce": uuid.uuid4().hex,
                "code": code,
                "cart_work_session_id": cart_work_session.id,
                "picking_task_id": task.id,
                "participant_id": participant.id if participant else None,
                "product_id": task.order_line.product_id,
                "quantity": str(quantity),
                "worker_code": worker_code,
            }
        return Response(
            {
                "confirmation_code": code,
                "challenge_token": signing.dumps(payload, salt=PICKING_SHORTAGE_CHALLENGE_SALT),
                "expires_at": (timezone.now() + timezone.timedelta(seconds=PICKING_SHORTAGE_CHALLENGE_MAX_AGE)).isoformat(),
                "summary": _picking_shortage_summary(task, quantity, cart_work_session),
            }
        )


class ScannerPickingReportShortageView(APIView):
    def post(self, request):
        token = str(request.data.get("challenge_token", "")).strip()
        confirmation_code = str(request.data.get("confirmation_code", "")).strip()
        client_operation_id = str(request.data.get("client_operation_id", "")).strip() or None
        if not token:
            return Response({"detail": "challenge_token is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not confirmation_code:
            return Response({"detail": "confirmation_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = signing.loads(token, salt=PICKING_SHORTAGE_CHALLENGE_SALT, max_age=PICKING_SHORTAGE_CHALLENGE_MAX_AGE)
        except signing.SignatureExpired:
            return Response({"detail": "Confirmation code expired."}, status=status.HTTP_400_BAD_REQUEST)
        except signing.BadSignature:
            return Response({"detail": "Invalid confirmation challenge."}, status=status.HTTP_400_BAD_REQUEST)
        if confirmation_code != payload.get("code"):
            return Response({"detail": "Confirmation code is incorrect."}, status=status.HTTP_400_BAD_REQUEST)

        existing = PickingShortage.objects.select_related("product", "branch", "unconfirmed_location", "picking_task").filter(
            confirmation_nonce=payload["nonce"]
        ).first()
        if existing is not None:
            cart_work_session = CartWorkSession.objects.select_related("cart", "picking_job", "scanner_session").get(
                pk=payload["cart_work_session_id"]
            )
            return Response(_shortage_response(existing, getattr(existing, "replenishment_request", None), cart_work_session))

        quantity = Decimal(payload["quantity"])
        worker_code = payload.get("worker_code") or "DEMO"
        with transaction.atomic():
            cart_work_session = (
                CartWorkSession.objects.select_for_update(of=("self",))
                .select_related("cart", "picking_job", "scanner_session", "confirmed_location")
                .get(pk=payload["cart_work_session_id"])
            )
            participant = None
            confirmed_location_id = cart_work_session.confirmed_location_id
            if request.user and request.user.is_authenticated:
                participant, error = _participant_for_request(cart_work_session, request, create_missing=False, lock=True, touch=True)
                if error is not None:
                    return error
                if participant is None or payload.get("participant_id") != participant.id:
                    return Response({"detail": "This shortage confirmation does not belong to your active pick."}, status=status.HTTP_409_CONFLICT)
                claim = PickingTaskClaim.objects.select_for_update().filter(
                    cart_work_participant=participant,
                    picking_task_id=payload["picking_task_id"],
                    status=PickingTaskClaim.Status.CLAIMED,
                ).first()
                if claim is None:
                    return Response({"detail": "Pick this line before reporting missing stock."}, status=status.HTTP_409_CONFLICT)
                confirmed_location_id = participant.confirmed_location_id
            task = (
                PickingTask.objects.select_for_update()
                .select_related("branch", "order_line__order", "order_line__product", "source_location")
                .get(pk=payload["picking_task_id"], job_task__picking_job=cart_work_session.picking_job)
            )
            if confirmed_location_id != task.source_location_id:
                return Response({"detail": "Current confirmed location changed. Generate a new challenge."}, status=status.HTTP_400_BAD_REQUEST)
            if task.order_line.product_id != payload["product_id"]:
                return Response({"detail": "Product changed. Generate a new challenge."}, status=status.HTTP_400_BAD_REQUEST)
            remaining = task.quantity_to_pick - task.quantity_picked - task.shortage_quantity
            if quantity > remaining:
                return Response({"detail": "Shortage quantity exceeds remaining picking quantity."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                unconfirmed_location = get_discrepancy_location(task.branch)
            except DiscrepancyLocationMissing as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            product = task.order_line.product
            source_item = InventoryItem.objects.select_for_update().filter(branch=task.branch, location=task.source_location, product=product).first()
            if source_item is None or source_item.quantity_on_hand < quantity:
                return Response({"detail": "Not enough stock at the expected location."}, status=status.HTTP_400_BAD_REQUEST)
            unconfirmed_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=task.branch,
                location=unconfirmed_location,
                product=product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            source_item.quantity_on_hand = F("quantity_on_hand") - quantity
            source_item.save(update_fields=["quantity_on_hand", "updated_at"])
            unconfirmed_item.quantity_on_hand = F("quantity_on_hand") + quantity
            unconfirmed_item.save(update_fields=["quantity_on_hand", "updated_at"])

            order = task.order_line.order
            actor = _scanner_actor(request, worker_code)
            customer_alias = order.customer_alias or order.customer_name
            shortage = PickingShortage.objects.create(
                picking_task=task,
                order=order,
                branch=task.branch,
                product=product,
                reported_location=task.source_location,
                unconfirmed_location=unconfirmed_location,
                cart=cart_work_session.cart,
                quantity=quantity,
                customer_alias_snapshot=customer_alias,
                reported_by=actor,
                reported_by_worker_code=worker_code,
                confirmation_nonce=payload["nonce"],
                client_operation_id=client_operation_id,
                note=str(request.data.get("note", "")).strip(),
            )
            StockMovement.objects.create(
                branch=task.branch,
                product=product,
                inventory_item=unconfirmed_item,
                source_location=task.source_location,
                destination_location=unconfirmed_location,
                movement_type=StockMovement.MovementType.PICKING_SHORTAGE,
                quantity=quantity,
                reference=shortage.reference,
                performed_by=actor,
            )
            allocated_quantity = _allocate_alternative_stock(
                shortage=shortage,
                original_task=task,
                picking_job=cart_work_session.picking_job,
                quantity_needed=quantity,
                actor=actor,
                worker_code=worker_code,
            )
            customer_unfulfilled_quantity = quantity - allocated_quantity
            shortage.alternative_allocated_quantity = allocated_quantity
            shortage.customer_unfulfilled_quantity = customer_unfulfilled_quantity
            shortage.save(
                update_fields=[
                    "alternative_allocated_quantity",
                    "customer_unfulfilled_quantity",
                    "updated_at",
                ]
            )
            task.shortage_quantity = F("shortage_quantity") + quantity
            task.status = (
                PickingTask.Status.WAITING_REPLENISHMENT
                if customer_unfulfilled_quantity > 0
                else (
                    PickingTask.Status.PICKED
                    if task.quantity_picked + quantity >= task.quantity_to_pick
                    else PickingTask.Status.IN_PROGRESS
                )
            )
            task.save(update_fields=["shortage_quantity", "status", "updated_at"])
            task.refresh_from_db()
            replenishment = None
            if customer_unfulfilled_quantity > 0:
                replenishment = ReplenishmentRequest.objects.create(
                    picking_shortage=shortage,
                    branch=task.branch,
                    customer_alias=customer_alias,
                    order_reference=order.external_reference,
                    product=product,
                    quantity=customer_unfulfilled_quantity,
                    created_by=actor,
                )
            if not _job_tasks(cart_work_session.picking_job).filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity")).exists():
                cart_work_session.picking_job.status = PickingJob.Status.PICKED
                cart_work_session.picking_job.save(update_fields=["status", "updated_at"])
            next_task = _current_pick_task_queryset(cart_work_session.picking_job).first()
            if participant is not None:
                if task.quantity_picked + task.shortage_quantity >= task.quantity_to_pick:
                    PickingTaskClaim.objects.filter(
                        cart_work_participant=participant,
                        picking_task=task,
                        status=PickingTaskClaim.Status.CLAIMED,
                    ).update(
                        status=PickingTaskClaim.Status.COMPLETED,
                        released_at=timezone.now(),
                        last_activity_at=timezone.now(),
                    )
                _claim_task_for_participant(cart_work_session, participant, mode=participant.picking_direction)
                participant.refresh_from_db()
                next_participant_task = participant.current_picking_task
                if next_participant_task is None or next_participant_task.source_location_id != confirmed_location_id:
                    participant.confirmed_location = None
                    participant.save(update_fields=["confirmed_location", "updated_at"])
            elif next_task is None or next_task.source_location_id != cart_work_session.confirmed_location_id:
                cart_work_session.confirmed_location = None
                cart_work_session.save(update_fields=["confirmed_location", "updated_at"])

            AuditLog.objects.create(
                actor=actor,
                action_type=AuditLog.ActionType.CREATE,
                event_type="picking_location_shortage",
                branch=task.branch,
                product=product,
                quantity=quantity,
                source_location=task.source_location,
                destination_location=unconfirmed_location,
                source_label=task.source_location.code,
                destination_label=unconfirmed_location.code,
                cart=cart_work_session.cart,
                order=order,
                route_run=order.route_run,
                reference=shortage.reference,
                entity_name="PickingShortage",
                entity_id=str(shortage.id),
                message=(
                    f"Worker {worker_code} reported missing stock at location {task.source_location.code}: "
                    f"{_piece_value(quantity)} {product.sku} for order {order.external_reference}."
                ),
            )
            if replenishment is not None:
                AuditLog.objects.create(
                    actor=actor,
                    action_type=AuditLog.ActionType.CREATE,
                    event_type="replenishment_requested",
                    branch=task.branch,
                    product=product,
                    quantity=customer_unfulfilled_quantity,
                    cart=cart_work_session.cart,
                    order=order,
                    route_run=order.route_run,
                    reference=replenishment.reference,
                    entity_name="ReplenishmentRequest",
                    entity_id=str(replenishment.id),
                    message=(
                        f"Replenishment request {replenishment.reference} was created for customer "
                        f"{customer_alias}: {_piece_value(customer_unfulfilled_quantity)} {product.sku}."
                    ),
                )

        cart_work_session.refresh_from_db()
        shortage.refresh_from_db()
        if replenishment is not None:
            replenishment.refresh_from_db()
        return Response(_shortage_response(shortage, replenishment, cart_work_session), status=status.HTTP_201_CREATED)


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

            participant, error = _participant_for_request(cart_work_session, request, create_missing=True, lock=True, touch=True)
            if error is not None:
                return error
            repair_messages = _repair_stale_current_tasks(cart_work_session, request)
            if participant is not None:
                task, _, _ = _claim_task_for_participant(cart_work_session, participant)
            else:
                task = _current_pick_task_queryset(cart_work_session.picking_job).first()
            if task is None:
                if participant is not None:
                    participant.confirmed_location = None
                    participant.save(update_fields=["confirmed_location", "updated_at"])
                else:
                    cart_work_session.confirmed_location = None
                    cart_work_session.save(update_fields=["confirmed_location", "updated_at"])
                state, confirmed_location_code, instruction = _picking_state(cart_work_session, participant)
                return Response(
                    {
                        "message": "Picking completed.",
                        "state": state,
                        "confirmed_location_code": confirmed_location_code,
                        "cart_work_session": _cart_work_session_data(cart_work_session, request),
                        "current_instruction": instruction,
                        "participant": _participant_data(participant, current_user=getattr(request, "user", None)) if participant else None,
                        "repair_messages": repair_messages,
                    },
                    status=status.HTTP_200_OK,
                )

            scanned_location = (
                Location.objects.select_related("branch")
                .filter(branch=task.branch, code__iexact=location_code)
                .first()
            )
            if scanned_location is None:
                if Location.objects.filter(code__iexact=location_code).exists():
                    return Response(
                        {"detail": f"Wrong location. Go to {task.source_location.code}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response({"detail": "Unknown location."}, status=status.HTTP_404_NOT_FOUND)

            if scanned_location.id != task.source_location_id:
                return Response(
                    {"detail": f"Wrong location. Go to {task.source_location.code}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if participant is not None:
                participant.confirmed_location = scanned_location
                participant.save(update_fields=["confirmed_location", "last_seen_at", "updated_at"])
            else:
                cart_work_session.confirmed_location = scanned_location
                cart_work_session.save(update_fields=["confirmed_location", "updated_at"])
                cart_work_session.refresh_from_db()
            state, confirmed_location_code, instruction = _picking_state(cart_work_session, participant)

        return Response(
            {
                "message": "Location confirmed.",
                "repair_messages": repair_messages,
                "state": state,
                "confirmed_location_code": confirmed_location_code,
                "cart_work_session": _cart_work_session_data(cart_work_session, request),
                "current_instruction": instruction,
                "participant": _participant_data(participant, current_user=getattr(request, "user", None)) if participant else None,
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
                now = timezone.now()
                PickingTaskClaim.objects.filter(
                    cart_work_participant__cart_work_session=cart_work_session,
                    status=PickingTaskClaim.Status.CLAIMED,
                ).update(status=PickingTaskClaim.Status.RELEASED, released_at=now, last_activity_at=now)
                CartWorkParticipant.objects.filter(
                    cart_work_session=cart_work_session,
                    status=CartWorkParticipant.Status.ACTIVE,
                ).update(status=CartWorkParticipant.Status.LEFT, left_at=now, last_seen_at=now)
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
        "lines": sum((task.quantity_picked + task.shortage_quantity) < task.quantity_to_pick for task in available_tasks),
        "started": len(started_tasks),
        "picked": sum((task.quantity_picked + task.shortage_quantity) >= task.quantity_to_pick for task in tasks),
        "prepared": sum(task.quantity_prepared >= task.quantity_to_pick for task in tasks),
        "is_selectable": bool(available_tasks) and route_run.status not in TERMINAL_ROUTE_STATUSES,
    }


class ScannerProformasView(APIView):
    def get(self, request):
        branch = request.query_params.get("branch")
        route_runs = RouteRun.objects.select_related("route", "route__branch").exclude(status__in=TERMINAL_ROUTE_STATUSES)
        if branch:
            route_runs = route_runs.filter(route__branch_id=branch)
        if request.user and request.user.is_authenticated:
            branch_ids = branch_ids_filter(request.user, branch)
            route_runs = route_runs.filter(route__branch_id__in=branch_ids)
        route_runs = route_runs.order_by("service_date", "departure_time", "route__code", "run_number")
        return Response({"results": [_route_proforma_data(route_run) for route_run in route_runs]})


class ScannerProformasCreateJobsView(APIView):
    def post(self, request):
        route_run_ids = request.data.get("route_run_ids") or []
        mode = str(request.data.get("mode", "")).strip()
        worker_code = _scanner_actor_code(request, request.data.get("worker_code", ""))

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
            branch_ids = {route_run.route.branch_id for route_run in route_runs}
            if len(branch_ids) != 1:
                return Response({"detail": "The selected routes belong to different branches."}, status=status.HTTP_400_BAD_REQUEST)
            branch = route_runs[0].route.branch
            if request.user and request.user.is_authenticated:
                require_branch_access(request.user, branch)

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
                    .filter(quantity_picked__lt=F("quantity_to_pick") - F("shortage_quantity"))
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
                    actor=request.user if request.user.is_authenticated else None,
                    action_type=AuditLog.ActionType.CREATE,
                    event_type="picking_job_created",
                    branch=branch,
                    reference=f"JOB-{picking_job.id}",
                    result=mode,
                    entity_name="PickingJob",
                    entity_id=str(picking_job.id),
                    message=(
                        f"Worker {worker_code} created {mode} picking job #{picking_job.id} "
                        f"from {', '.join(route_run.route.code for route_run in group)}."
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
        if request.user and request.user.is_authenticated:
            allowed_branch_ids = branch_ids_filter(request.user)
            jobs = jobs.filter(route_runs__route__branch_id__in=allowed_branch_ids).distinct()
        return Response({"results": [_job_summary(job) for job in jobs]})


class ScannerTaskStartView(APIView):
    def post(self, request, job_id):
        cart_code = str(request.data.get("cart_code", "")).strip()
        worker_code = _scanner_actor_code(request, request.data.get("worker_code", ""))
        if not cart_code:
            return Response({"detail": "cart_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            picking_job = PickingJob.objects.select_for_update().filter(pk=job_id).first()
            if picking_job is None:
                return Response({"detail": "Picking job not found."}, status=status.HTTP_404_NOT_FOUND)
            if picking_job.status != PickingJob.Status.AVAILABLE:
                return Response({"detail": "Picking job is not available."}, status=status.HTTP_409_CONFLICT)
            if request.user and request.user.is_authenticated:
                for route_run in picking_job.route_runs.select_related("route", "route__branch"):
                    require_branch_access(request.user, route_run.route.branch)
                active_participant = (
                    CartWorkParticipant.objects.select_related("cart_work_session__cart")
                    .filter(user=request.user, status=CartWorkParticipant.Status.ACTIVE)
                    .first()
                )
                if active_participant is not None:
                    return Response(
                        {"detail": f"You already have active work on {active_participant.cart_work_session.cart.code}."},
                        status=status.HTTP_409_CONFLICT,
                    )

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
            _repair_stale_current_tasks(cart_work_session, request)
            participant = None
            if request.user and request.user.is_authenticated:
                branch = _cart_work_branch(cart_work_session)
                participant = CartWorkParticipant.objects.create(
                    cart_work_session=cart_work_session,
                    user=request.user,
                    branch=branch,
                )
                _claim_task_for_participant(cart_work_session, participant)

            AuditLog.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="picking_job_started",
                result="started",
                entity_name="PickingJob",
                entity_id=str(picking_job.id),
                message=f"PickingJob {picking_job.id} assigned to cart {cart.code} by {worker_code}.",
            )

        return Response(
            {
                "message": "Picking job started.",
                "job": _job_summary(picking_job),
                "cart_work_session": _cart_work_session_data(cart_work_session, request),
                "session": _session_data(session),
                "participant": _participant_data(participant, current_user=request.user) if participant else None,
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

        return _cart_work_response(cart_work_session, request)


class ScannerCartWorkJoinView(APIView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return Response({"detail": "Authentication is required to join cart work."}, status=status.HTTP_401_UNAUTHORIZED)

        cart_code = str(request.data.get("cart_barcode") or request.data.get("cart_code") or "").strip()
        if not cart_code:
            return Response({"detail": "cart_barcode is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            cart_work_session = (
                CartWorkSession.objects.select_for_update(of=("self",))
                .select_related("cart", "picking_job", "scanner_session")
                .filter(cart__code__iexact=cart_code)
                .order_by("-started_at", "-id")
                .first()
            )
            if cart_work_session is None:
                return Response(
                    {"detail": f"No active picking work was found for cart {cart_code}."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if cart_work_session.status in [CartWorkSession.Status.COMPLETED, CartWorkSession.Status.CANCELLED]:
                return Response({"detail": "This cart work session is already completed."}, status=status.HTTP_400_BAD_REQUEST)
            if cart_work_session.status not in [CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL]:
                return Response({"detail": "Cart work session is not active."}, status=status.HTTP_400_BAD_REQUEST)

            participant, error = _participant_for_request(cart_work_session, request, create_missing=True, lock=True, touch=True)
            if error is not None:
                return error
            was_created = getattr(participant, "was_created", False)
            repair_messages = _repair_stale_current_tasks(cart_work_session, request)
            _claim_task_for_participant(cart_work_session, participant)
            if was_created:
                AuditLog.objects.create(
                    actor=request.user,
                    action_type=AuditLog.ActionType.UPDATE,
                    event_type="cart_work_joined",
                    branch=participant.branch,
                    cart=cart_work_session.cart,
                    reference=f"JOB-{cart_work_session.picking_job_id}",
                    entity_name="CartWorkSession",
                    entity_id=str(cart_work_session.id),
                    message=f"{request.user.username} joined cart work {cart_work_session.cart.code}.",
                )

        response = _cart_work_response(cart_work_session, request, repair_messages)
        response.data["message"] = "Cart work joined."
        response.data["session"] = _session_data(cart_work_session.scanner_session)
        return response


class ScannerCartWorkClaimView(APIView):
    def post(self, request):
        cart_work_session, error = _get_active_cart_work_or_response(request.data.get("cart_work_session_id"))
        if error is not None:
            return error
        task_id = request.data.get("picking_task_id")
        mode = str(request.data.get("mode") or request.data.get("direction") or "beginning").strip()
        if mode not in ["beginning", "end", "specific"]:
            return Response({"detail": "mode must be beginning, end, or specific."}, status=status.HTTP_400_BAD_REQUEST)
        if mode == "specific" and not task_id:
            return Response({"detail": "picking_task_id is required for specific mode."}, status=status.HTTP_400_BAD_REQUEST)
        if task_id and mode in ["beginning", "end"]:
            task_id = None

        with transaction.atomic():
            cart_work_session = (
                CartWorkSession.objects.select_for_update(of=("self",))
                .select_related("cart", "picking_job", "scanner_session")
                .get(pk=cart_work_session.id)
            )
            participant, error = _participant_for_request(cart_work_session, request, create_missing=True, lock=True, touch=True)
            if error is not None:
                return error
            repair_messages = _repair_stale_current_tasks(cart_work_session, request)
            task = None
            if task_id:
                task = (
                    PickingTask.objects.select_for_update(of=("self",))
                    .select_related("branch", "order_line__order", "order_line__product", "source_location")
                    .filter(pk=task_id, job_task__picking_job=cart_work_session.picking_job)
                    .first()
                )
                if task is None:
                    return Response({"detail": "Picking task not found in this cart work."}, status=status.HTTP_404_NOT_FOUND)
            selected_task, claim, created = _claim_task_for_participant(
                cart_work_session,
                participant,
                task=task,
                mode=mode,
                keep_existing=False,
            )
            if selected_task is None:
                if task is not None and hasattr(task, "claim_conflict_username"):
                    return Response(
                        {"detail": f"This picking line is already handled by {task.claim_conflict_username}."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if task is not None:
                    return Response(
                        {"detail": "This picking line is no longer available."},
                        status=status.HTTP_409_CONFLICT,
                    )
                return Response({"detail": "No eligible picking line is available."}, status=status.HTTP_409_CONFLICT)
            if selected_task is not None and created:
                AuditLog.objects.create(
                    actor=request.user if request.user.is_authenticated else None,
                    action_type=AuditLog.ActionType.UPDATE,
                    event_type="picking_task_claimed",
                    branch=selected_task.branch,
                    product=selected_task.order_line.product,
                    source_location=selected_task.source_location,
                    source_label=selected_task.source_location.code,
                    cart=cart_work_session.cart,
                    order=selected_task.order_line.order,
                    route_run=selected_task.order_line.order.route_run,
                    reference=selected_task.order_line.order.external_reference,
                    entity_name="PickingTaskClaim",
                    entity_id=str(claim.id),
                    result=mode,
                    message=(
                        f"{_scanner_actor_code(request)} selected {selected_task.order_line.product.sku} "
                        f"at {selected_task.source_location.code}."
                    ),
                )

        return _cart_work_response(cart_work_session, request, repair_messages)


class ScannerCartWorkLeaveView(APIView):
    def post(self, request):
        cart_work_session, error = _get_active_cart_work_or_response(request.data.get("cart_work_session_id"))
        if error is not None:
            return error
        with transaction.atomic():
            cart_work_session = (
                CartWorkSession.objects.select_for_update(of=("self",))
                .select_related("cart", "picking_job", "scanner_session")
                .get(pk=cart_work_session.id)
            )
            participant, error = _participant_for_request(cart_work_session, request, create_missing=False, lock=True, touch=True)
            if error is not None:
                return error
            if participant is None:
                return Response({"detail": "You are not an active participant in this cart work."}, status=status.HTTP_400_BAD_REQUEST)
            _release_participant_claim(participant)
            participant.status = CartWorkParticipant.Status.LEFT
            participant.left_at = timezone.now()
            participant.last_seen_at = timezone.now()
            participant.save(update_fields=["status", "left_at", "last_seen_at", "updated_at"])
            AuditLog.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="cart_work_left",
                branch=participant.branch,
                cart=cart_work_session.cart,
                reference=f"JOB-{cart_work_session.picking_job_id}",
                entity_name="CartWorkSession",
                entity_id=str(cart_work_session.id),
                message=f"{_scanner_actor_code(request)} left cart work {cart_work_session.cart.code}.",
            )

        return Response({"message": "Cart work left."})


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
                now = timezone.now()
                PickingTaskClaim.objects.filter(
                    cart_work_participant__cart_work_session=cart_work_session,
                    status=PickingTaskClaim.Status.CLAIMED,
                ).update(status=PickingTaskClaim.Status.COMPLETED, released_at=now, last_activity_at=now)
                CartWorkParticipant.objects.filter(
                    cart_work_session=cart_work_session,
                    status=CartWorkParticipant.Status.ACTIVE,
                ).update(status=CartWorkParticipant.Status.LEFT, left_at=now, last_seen_at=now)

                picking_job = cart_work_session.picking_job
                if not is_picking_job_work_fully_prepared(picking_job):
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
                    "brand": product.brand,
                    "description": product.description,
                    "image_url": product.image_url or None,
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


class ScannerInterBranchArrivalView(APIView):
    def get(self, request):
        branch_code = str(request.query_params.get("branch", "")).strip()
        if not branch_code:
            return Response({"detail": "branch is required."}, status=status.HTTP_400_BAD_REQUEST)
        arrivals = (
            TransferPalletArrival.objects.select_related(
                "pallet", "pallet__transfer", "pallet__transfer__source_branch", "pallet__transfer__destination_branch"
            )
            .prefetch_related("pallet__items")
            .filter(pallet__transfer__destination_branch__code__iexact=branch_code)
            .order_by("-scanned_at")[:20]
        )
        if arrivals:
            require_branch_access(request.user, arrivals[0].pallet.transfer.destination_branch)
        else:
            branch = get_object_or_404(Branch, code__iexact=branch_code)
            require_branch_access(request.user, branch)
        return Response({"results": [_mm_task_data(arrival.pallet) for arrival in arrivals]})

    def post(self, request):
        pallet_code = str(request.data.get("pallet_code", "")).strip()
        worker_code = _scanner_actor_code(request, request.data.get("worker_code", ""))
        client_operation_id = str(request.data.get("client_operation_id", "")).strip() or None
        if not pallet_code:
            return Response({"detail": "pallet_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            pallet = (
                TransferPallet.objects.select_for_update()
                .select_related("transfer", "transfer__source_branch", "transfer__destination_branch")
                .prefetch_related("items")
                .filter(scan_code__iexact=pallet_code)
                .first()
            )
            if pallet is None:
                return Response({"detail": "Pallet not found."}, status=status.HTTP_404_NOT_FOUND)
            require_branch_access(request.user, pallet.transfer.destination_branch)
            if pallet.transfer.status == InterBranchTransfer.Status.CANCELLED or pallet.status == TransferPallet.Status.CANCELLED:
                return Response({"detail": "Cancelled pallets cannot be registered as arrived."}, status=status.HTTP_400_BAD_REQUEST)
            if not pallet.released_at or pallet.transfer.status not in [InterBranchTransfer.Status.RELEASED, InterBranchTransfer.Status.IN_TRANSIT, InterBranchTransfer.Status.RECEIVING]:
                return Response({"detail": "Pallet has not been released by the source branch."}, status=status.HTTP_400_BAD_REQUEST)
            if _pallet_is_closed(pallet):
                return Response({"detail": "Pallet is already completed."}, status=status.HTTP_400_BAD_REQUEST)
            arrival, created = TransferPalletArrival.objects.get_or_create(
                pallet=pallet,
                defaults={
                    "scanned_by": request.user,
                    "scanned_by_worker_code": worker_code,
                    "client_operation_id": client_operation_id,
                },
            )
            if created:
                AuditLog.objects.create(
                    actor=request.user,
                    action_type=AuditLog.ActionType.CREATE,
                    event_type="inter_branch_arrival",
                    branch=pallet.transfer.destination_branch,
                    transfer=pallet.transfer,
                    pallet=pallet,
                    source_label=pallet.transfer.source_branch.code,
                    destination_label=pallet.transfer.destination_branch.code,
                    result="arrived",
                    reference=pallet.transfer.reference,
                    entity_name="TransferPalletArrival",
                    entity_id=str(arrival.id),
                    message=(f"Worker {worker_code} registered pallet {pallet.scan_code} as arrived at "
                             f"{pallet.transfer.destination_branch.code} from {pallet.transfer.source_branch.code}.")
                )
        payload = _mm_task_data(pallet)
        payload["arrival_result"] = "registered" if created else "already_registered"
        message = (f"Pallet registered at {pallet.transfer.destination_branch.code}." if created else
                   f"Pallet {pallet.scan_code} was already registered at {pallet.transfer.destination_branch.code}.")
        return Response({"message": message, "arrival": payload}, status=status.HTTP_200_OK)


class InterBranchMMTasksView(APIView):
    def get(self, request):
        branch_code = str(request.query_params.get("branch", "")).strip()
        branch = get_object_or_404(Branch, code__iexact=branch_code)
        require_branch_access(request.user, branch)
        pallets = (
            TransferPallet.objects.select_related("transfer", "transfer__source_branch", "transfer__destination_branch", "arrival")
            .prefetch_related("items")
            .filter(transfer__destination_branch=branch, arrival__isnull=False)
            .exclude(status__in=[TransferPallet.Status.RECEIVED, TransferPallet.Status.CLOSED_WITH_DISCREPANCY, TransferPallet.Status.CANCELLED])
            .order_by("arrival__scanned_at")
        )
        return Response({"results": [_mm_task_data(pallet) for pallet in pallets]})


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
            if not TransferPalletArrival.objects.filter(pallet=pallet).exists():
                return Response(
                    {"detail": "Register the pallet arrival before starting receiving."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
                event_type="receive_scan",
                branch=pallet.transfer.destination_branch,
                product=product,
                quantity=quantity,
                source_label=pallet.scan_code,
                transfer=pallet.transfer,
                pallet=pallet,
                reference=pallet.scan_code,
                entity_name="TransferPallet",
                entity_id=str(pallet.id),
                message=(
                    f"Worker {session.worker_code or 'scanner'} scanned {_piece_value(quantity)} {product.sku} "
                    f"on pallet {pallet.scan_code}."
                ),
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

            destination_branch = session.pallet.transfer.destination_branch
            location = (
                Location.objects.select_related("branch")
                .filter(branch=destination_branch, code__iexact=location_code)
                .first()
            )
            if location is None:
                if Location.objects.filter(code__iexact=location_code).exists():
                    return Response(
                        {"detail": f"Wrong branch. Use a {destination_branch.code} destination location."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {"detail": f"Destination location not found in branch {destination_branch.code}."},
                    status=status.HTTP_404_NOT_FOUND,
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

            receiving_scan = PalletReceivingScan.objects.create(
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
                event_type="receive",
                branch=destination_branch,
                product=item.product,
                quantity=quantity,
                destination_location=location,
                source_label=pallet.scan_code,
                destination_label=location.code,
                transfer=pallet.transfer,
                pallet=pallet,
                reference=pallet.transfer.reference,
                entity_name="PalletReceivingScan",
                entity_id=str(receiving_scan.id),
                message=(
                    f"Worker {session.worker_code or 'scanner'} received {_piece_value(quantity)} {item.product.sku} "
                    f"from pallet {pallet.scan_code} "
                    f"to location {location.code} for transfer {pallet.transfer.reference}."
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
            if source_location.branch_id != target_location.branch_id:
                return Response(
                    {"detail": "Source and target locations must belong to the same branch."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if request.user and request.user.is_authenticated:
                require_branch_access(request.user, source_location.branch)

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
                performed_by=request.user if request.user.is_authenticated else None,
            )
            AuditLog.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="scanner_quick_transfer",
                branch=source_location.branch,
                product=product,
                quantity=quantity,
                source_location=source_location,
                destination_location=target_location,
                reference=movement.reference,
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
