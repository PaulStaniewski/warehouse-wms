"""Canonical read projections for outbound operational data.

Commercial demand remains on Order/OrderLine.  ShipmentLine owns the effective
fulfilment quantity, PickingTask owns warehouse work, and scanner/control rows
provide employee-attributed evidence.  These helpers deliberately do not write
state; application services are responsible for transitions and audit events.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q

from operations.models import PickingTask, RouteRun, Shipment, ShipmentLine


ZERO = Decimal("0")

ACTIVE_SHIPMENT_STATUSES = (
    Shipment.Status.PENDING_ACTIVATION,
    Shipment.Status.ACTIVE,
    Shipment.Status.PICKING,
    Shipment.Status.PICKED,
    Shipment.Status.CONTROLLED,
    Shipment.Status.PREPARED,
    Shipment.Status.DOCUMENTS_POSTED,
    Shipment.Status.READY_FOR_DISPATCH,
    Shipment.Status.EXCEPTION,
)

TERMINAL_ACTIVE_BOARD_STATUSES = (
    RouteRun.Status.CLOSED,
    RouteRun.Status.CANCELLED,
    RouteRun.Status.DISPATCHED,
)

TERMINAL_SHIPMENT_STATUSES = (
    Shipment.Status.DISPATCHED,
    Shipment.Status.COMPLETED,
    Shipment.Status.CANCELLED,
)


def open_shipment_query() -> Q:
    """Canonical current-work definition used by Shipment list and audits."""
    return (
        ~Q(status__in=TERMINAL_SHIPMENT_STATUSES)
        & (
            Q(route_run__isnull=True)
            | ~Q(route_run__status__in=TERMINAL_ACTIVE_BOARD_STATUSES)
        )
    )


def open_shipment_queryset(queryset):
    return queryset.filter(open_shipment_query())


def shipment_is_open(shipment: Shipment) -> bool:
    return (
        shipment.status not in TERMINAL_SHIPMENT_STATUSES
        and (
            shipment.route_run_id is None
            or shipment.route_run.status not in TERMINAL_ACTIVE_BOARD_STATUSES
        )
    )


def active_route_run_queryset(queryset):
    """Return the canonical dispatch-board RouteRun set in authoritative order."""
    return (
        queryset.exclude(status__in=TERMINAL_ACTIVE_BOARD_STATUSES)
        .filter(shipments__status__in=ACTIVE_SHIPMENT_STATUSES)
        .distinct()
        .order_by(
            "service_date",
            "planned_departure_at",
            "dispatch_wave",
            "departure_time",
            "schedule__departure_time",
            "route__code",
            "run_number",
            "id",
        )
    )


def route_run_has_remaining_pickable_work(route_run: RouteRun) -> bool:
    """Return whether scanner picking still has canonical effective work."""
    if route_run.status in TERMINAL_ACTIVE_BOARD_STATUSES:
        return False
    for shipment in route_run.shipments.all():
        if shipment.status not in ACTIVE_SHIPMENT_STATUSES:
            continue
        for line in shipment.lines.all():
            if line.ordered_quantity <= line.cancelled_quantity:
                continue
            for task in line.order_line.picking_tasks.all():
                if task.status in {PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED}:
                    continue
                if task.quantity_picked + task.shortage_quantity < task.quantity_to_pick:
                    return True
    return False


@dataclass(frozen=True)
class ShipmentLineProgress:
    original_quantity: Decimal
    effective_quantity: Decimal
    removed_quantity: Decimal
    task_target_quantity: Decimal
    picked_quantity: Decimal
    controlled_quantity: Decimal
    prepared_quantity: Decimal
    shortage_quantity: Decimal
    remaining_to_pick: Decimal
    state: str
    blocking_reason: str


@dataclass(frozen=True)
class RouteRunWorkloadProjection:
    unstarted: int = 0
    started: int = 0
    picked: int = 0
    prepared: int = 0

    @property
    def total(self) -> int:
        return self.unstarted + self.started + self.picked + self.prepared


@dataclass(frozen=True)
class ShipmentOperationalProjection:
    effective_quantity: Decimal = ZERO
    picked_quantity: Decimal = ZERO
    controlled_quantity: Decimal = ZERO
    prepared_quantity: Decimal = ZERO
    shortage_quantity: Decimal = ZERO
    remaining_to_pick: Decimal = ZERO

    @property
    def progress_percent(self) -> float:
        required = max(self.effective_quantity - self.shortage_quantity, ZERO)
        if required <= ZERO:
            return 0.0
        return round(float((self.picked_quantity / required) * 100), 1)


def _tasks_for_line(line: ShipmentLine) -> list[PickingTask]:
    return list(line.order_line.picking_tasks.all())


def shipment_line_progress(line: ShipmentLine) -> ShipmentLineProgress:
    tasks = _tasks_for_line(line)
    active_tasks = [task for task in tasks if task.status != PickingTask.Status.CANCELLED]
    original = line.ordered_quantity
    removed = line.cancelled_quantity
    effective = original - removed
    target = sum((task.quantity_to_pick for task in active_tasks), ZERO)
    picked = sum((task.quantity_picked for task in tasks), ZERO)
    # The existing accepted scanner workflow records successful control and
    # preparation together on PickingTask.quantity_prepared.  Until control is
    # split into a distinct persisted step, both projections intentionally use
    # that same evidence rather than inventing a second counter.
    prepared = sum((task.quantity_prepared for task in tasks), ZERO)
    controlled = prepared
    shortage = sum((task.shortage_quantity for task in active_tasks), ZERO)
    required_pick = max(effective - shortage, ZERO)
    remaining = max(required_pick - picked, ZERO)

    if line.shipment.status == Shipment.Status.CANCELLED or effective == ZERO:
        state, reason = "cancelled", "No active fulfilment quantity."
    elif not active_tasks:
        state, reason = "unstarted", "Picking work has not been posted."
    elif prepared >= required_pick and remaining == ZERO:
        state, reason = "prepared", ""
    elif picked >= required_pick and remaining == ZERO:
        state, reason = "picked", "Control and preparation are incomplete."
    elif picked > ZERO or any(task.status == PickingTask.Status.IN_PROGRESS for task in active_tasks):
        state, reason = "started", "Picking is incomplete."
    else:
        state, reason = "unstarted", "Picking has not started."

    if shortage > ZERO and state != "prepared":
        reason = "Picking shortage requires resolution."

    return ShipmentLineProgress(
        original_quantity=original,
        effective_quantity=effective,
        removed_quantity=removed,
        task_target_quantity=target,
        picked_quantity=picked,
        controlled_quantity=controlled,
        prepared_quantity=prepared,
        shortage_quantity=shortage,
        remaining_to_pick=remaining,
        state=state,
        blocking_reason=reason,
    )


def shipment_operational_projection(shipment: Shipment) -> ShipmentOperationalProjection:
    values = {
        "effective_quantity": ZERO,
        "picked_quantity": ZERO,
        "controlled_quantity": ZERO,
        "prepared_quantity": ZERO,
        "shortage_quantity": ZERO,
        "remaining_to_pick": ZERO,
    }
    for line in shipment.lines.all():
        progress = shipment_line_progress(line)
        values["effective_quantity"] += progress.effective_quantity
        values["picked_quantity"] += progress.picked_quantity
        values["controlled_quantity"] += progress.controlled_quantity
        values["prepared_quantity"] += progress.prepared_quantity
        values["shortage_quantity"] += progress.shortage_quantity
        values["remaining_to_pick"] += progress.remaining_to_pick
    return ShipmentOperationalProjection(**values)

def route_run_workload_projection(route_run: RouteRun) -> RouteRunWorkloadProjection:
    if route_run.status in {RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED}:
        return RouteRunWorkloadProjection()
    counts = {"unstarted": 0, "started": 0, "picked": 0, "prepared": 0}
    shipments = list(route_run.shipments.all())
    for shipment in shipments:
        if shipment.status == Shipment.Status.CANCELLED:
            continue
        for line in shipment.lines.all():
            progress = shipment_line_progress(line)
            if progress.effective_quantity <= ZERO or progress.state == "cancelled":
                continue
            counts[progress.state] += 1
    if not shipments:
        for order in route_run.orders.all():
            for line in order.lines.all():
                tasks = [task for task in line.picking_tasks.all() if task.status != PickingTask.Status.CANCELLED]
                if not tasks:
                    counts["unstarted"] += 1
                    continue
                picked = sum((task.quantity_picked for task in tasks), ZERO)
                prepared = sum((task.quantity_prepared for task in tasks), ZERO)
                required = sum((task.quantity_to_pick - task.shortage_quantity for task in tasks), ZERO)
                if prepared >= required:
                    counts["prepared"] += 1
                elif picked >= required:
                    counts["picked"] += 1
                elif picked > ZERO or any(task.status == PickingTask.Status.IN_PROGRESS for task in tasks):
                    counts["started"] += 1
                else:
                    counts["unstarted"] += 1
    return RouteRunWorkloadProjection(**counts)


def route_run_quantity_progress(route_run: RouteRun) -> tuple[Decimal, Decimal]:
    shipments = list(route_run.shipments.all())
    if shipments:
        projections = [shipment_operational_projection(shipment) for shipment in shipments if shipment.status != Shipment.Status.CANCELLED]
        required = sum((item.effective_quantity - item.shortage_quantity for item in projections), ZERO)
        picked = sum((item.picked_quantity for item in projections), ZERO)
        return required, picked
    tasks = [
        task
        for order in route_run.orders.all()
        for line in order.lines.all()
        for task in line.picking_tasks.all()
        if task.status != PickingTask.Status.CANCELLED
    ]
    required = sum((task.quantity_to_pick - task.shortage_quantity for task in tasks), ZERO)
    picked = sum((task.quantity_picked for task in tasks), ZERO)
    return required, picked


def route_run_is_fully_prepared(route_run: RouteRun) -> bool:
    workload = route_run_workload_projection(route_run)
    return workload.total > 0 and workload.prepared == workload.total

