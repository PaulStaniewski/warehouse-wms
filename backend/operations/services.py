from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from operations.models import AuditLog, PickingTask, RouteRun


TERMINAL_ROUTE_STATUSES = {
    RouteRun.Status.CLOSED,
    RouteRun.Status.DISPATCHED,
    RouteRun.Status.CANCELLED,
}


def route_departure_at(route_run: RouteRun):
    return timezone.make_aware(
        datetime.combine(route_run.service_date, route_run.departure_time),
        timezone.get_current_timezone(),
    )


def is_route_late(route_run: RouteRun, moment=None) -> bool:
    moment = moment or timezone.now()
    return moment > route_departure_at(route_run)


def route_close_result(route_run: RouteRun) -> str:
    if route_run.closed_at is None:
        return "unknown"

    return "late" if is_route_late(route_run, route_run.closed_at) else "on_time"


def is_route_work_fully_prepared(route_run: RouteRun) -> bool:
    tasks = PickingTask.objects.filter(order_line__order__route_run=route_run)
    if not tasks.exists():
        return False

    return not tasks.filter(quantity_prepared__lt=F("quantity_to_pick")).exists()


@transaction.atomic
def recalculate_route_readiness(route_run: RouteRun) -> bool:
    route_run = RouteRun.objects.select_for_update().get(pk=route_run.pk)
    is_ready = is_route_work_fully_prepared(route_run)

    if not is_ready or route_run.status in TERMINAL_ROUTE_STATUSES:
        return is_ready

    first_ready = route_run.status != RouteRun.Status.READY_TO_CLOSE or route_run.ready_at is None
    route_run.status = RouteRun.Status.READY_TO_CLOSE
    if route_run.ready_at is None:
        route_run.ready_at = timezone.now()
    route_run.save(update_fields=["status", "ready_at", "updated_at"])

    if first_ready:
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.STATUS_CHANGE,
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=f"Route run {route_run.id} is ready to close.",
        )

    return True
