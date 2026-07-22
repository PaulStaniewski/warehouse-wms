from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.authorization import require_branch_access
from operations.models import (
    AuditLog,
    BranchDispatchPolicy,
    DeliveryRoute,
    RouteRoundSchedule,
    RouteRun,
    RouteRunOverrideHistory,
    Shipment,
    ShipmentRouteAssignment,
)


WEEKDAY_ABBREVIATIONS = {
    0: "MON",
    1: "TUE",
    2: "WED",
    3: "THU",
    4: "FRI",
    5: "SAT",
    6: "SUN",
}

TERMINAL_ROUTE_STATUSES = {
    RouteRun.Status.CLOSED,
    RouteRun.Status.DISPATCHED,
    RouteRun.Status.CANCELLED,
}


def aware_datetime(day, value):
    return timezone.make_aware(datetime.combine(day, value), timezone.get_current_timezone())


def operational_route_code(route: DeliveryRoute) -> str:
    code = route.code.strip().upper()
    if code.startswith("ROUTE-"):
        code = code[6:]
    if "/" in code:
        code = code.rsplit("/", 1)[-1]
    if code.isdigit():
        code = code.zfill(2)
    return code


def operational_identifier(route: DeliveryRoute, service_date, round_number: int) -> str:
    weekday = WEEKDAY_ABBREVIATIONS[service_date.weekday()]
    return f"ROUTE-{operational_route_code(route)}_{weekday}-{round_number}"


def validate_dispatch_schedule(branch, *, exclude_schedule_id=None):
    policy, _ = BranchDispatchPolicy.objects.get_or_create(branch=branch)
    schedules = list(
        RouteRoundSchedule.objects.select_related("route")
        .filter(route__branch=branch, is_active=True)
        .exclude(pk=exclude_schedule_id)
        .order_by("weekday", "departure_time", "dispatch_wave", "route__code")
    )
    by_day_wave = {}
    by_day = {}
    for schedule in schedules:
        by_day_wave.setdefault((schedule.weekday, schedule.dispatch_wave), []).append(schedule)
        by_day.setdefault(schedule.weekday, []).append(schedule)

    for rows in by_day_wave.values():
        if len(rows) > policy.max_routes_per_wave:
            raise ValidationError(f"Dispatch wave {rows[0].dispatch_wave} exceeds the branch maximum of {policy.max_routes_per_wave} routes.")

    for rows in by_day.values():
        ordered_waves = []
        seen_waves = set()
        for schedule in rows:
            if schedule.dispatch_wave in seen_waves:
                continue
            seen_waves.add(schedule.dispatch_wave)
            ordered_waves.append(schedule)
        for previous, current in zip(ordered_waves, ordered_waves[1:]):
            previous_dt = datetime.combine(timezone.localdate(), previous.departure_time)
            current_dt = datetime.combine(timezone.localdate(), current.departure_time)
            gap = (current_dt - previous_dt).total_seconds() / 60
            if gap < policy.min_wave_gap_minutes:
                raise ValidationError(
                    f"Dispatch waves must be at least {policy.min_wave_gap_minutes} minutes apart."
                )


def create_route_run_from_schedule(schedule: RouteRoundSchedule, service_date):
    cutoff_at = aware_datetime(service_date, schedule.cutoff_time)
    departure_at = aware_datetime(service_date, schedule.departure_time)
    defaults = {
        "schedule": schedule,
        "order_cutoff_time": schedule.cutoff_time,
        "sync_time": schedule.cutoff_time,
        "departure_time": schedule.departure_time,
        "cutoff_at": cutoff_at,
        "planned_departure_at": departure_at,
        "dispatch_wave": schedule.dispatch_wave,
        "operational_identifier": operational_identifier(schedule.route, service_date, schedule.round_number),
        "status": RouteRun.Status.OPEN,
    }
    try:
        route_run, created = RouteRun.objects.get_or_create(
            route=schedule.route,
            service_date=service_date,
            run_number=schedule.round_number,
            defaults=defaults,
        )
    except IntegrityError:
        route_run = RouteRun.objects.get(route=schedule.route, service_date=service_date, run_number=schedule.round_number)
        created = False
    if created:
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.CREATE,
            event_type="route_run_created_on_demand",
            branch=schedule.route.branch,
            route_run=route_run,
            reference=route_run.operational_identifier,
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=f"Route run {route_run.operational_identifier} was created on demand.",
        )
    return route_run, created


def eligible_schedules_for_route(route: DeliveryRoute, assignment_at, max_days: int = 7):
    local_assignment = timezone.localtime(assignment_at)
    start_date = local_assignment.date()
    end_date = start_date + timedelta(days=max_days)
    return RouteRoundSchedule.objects.filter(
        route=route,
        is_active=True,
        weekday__in={day.weekday() for day in (start_date + timedelta(days=offset) for offset in range(max_days + 1))},
    ).order_by("weekday", "departure_time", "round_number")


def find_or_create_route_run_for_shipment(shipment: Shipment, route: DeliveryRoute, assignment_at=None):
    assignment_at = assignment_at or shipment.external_created_at or shipment.created_at or timezone.now()
    local_assignment = timezone.localtime(assignment_at)
    for day_offset in range(8):
        candidate_date = local_assignment.date() + timedelta(days=day_offset)
        schedules = RouteRoundSchedule.objects.filter(
            route=route,
            weekday=candidate_date.weekday(),
            is_active=True,
        ).order_by("round_number")
        for schedule in schedules:
            cutoff_at = aware_datetime(candidate_date, schedule.cutoff_time)
            if candidate_date == local_assignment.date() and assignment_at > cutoff_at:
                continue
            route_run, _ = create_route_run_from_schedule(schedule, candidate_date)
            if route_run.status in TERMINAL_ROUTE_STATUSES:
                continue
            return route_run
    return None


def assign_shipment_to_route_run(shipment: Shipment, route: DeliveryRoute, assignment_at=None, actor=None):
    route_run = find_or_create_route_run_for_shipment(shipment, route, assignment_at)
    if route_run is None:
        shipment.status = Shipment.Status.EXCEPTION
        shipment.save(update_fields=["status", "updated_at"])
        return None
    previous_route_run = shipment.route_run
    shipment.route_run = route_run
    shipment.order.route_run = route_run
    shipment.save(update_fields=["route_run", "updated_at"])
    shipment.order.save(update_fields=["route_run", "updated_at"])
    AuditLog.objects.create(
        actor=actor,
        action_type=AuditLog.ActionType.UPDATE,
        event_type="shipment_automatically_assigned_to_route",
        branch=shipment.branch,
        order=shipment.order,
        route_run=route_run,
        reference=shipment.reference,
        entity_name="Shipment",
        entity_id=str(shipment.id),
        message=f"Shipment {shipment.reference} was assigned to route run {route_run.operational_identifier}.",
    )
    return route_run


def route_snapshot(route_run: RouteRun | None) -> str:
    if route_run is None:
        return ""
    identifier = operational_identifier(route_run.route, route_run.service_date, route_run.run_number)
    return f"{route_run.route.branch.code} / {identifier} / {route_run.planned_departure_at or route_run.departure_time}"


def manual_change_shipment_route(user, shipment: Shipment, *, route_run=None, schedule=None, operational_date=None, client_operation_id=None):
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("branch", "order").get(pk=shipment.pk)
        require_branch_access(user, shipment.branch)
        if schedule is not None:
            require_branch_access(user, schedule.route.branch)
            if schedule.route.branch_id != shipment.branch_id:
                raise ValidationError("Target route belongs to another branch.")
            route_run, _ = create_route_run_from_schedule(schedule, operational_date)
        else:
            route_run = RouteRun.objects.select_for_update().select_related("route", "route__branch").get(pk=route_run.pk)
            require_branch_access(user, route_run.route.branch)
        if route_run.route.branch_id != shipment.branch_id:
            raise ValidationError("Target route belongs to another branch.")
        if route_run.status in TERMINAL_ROUTE_STATUSES:
            raise ValidationError("Target route is not eligible.")
        if shipment.route_run_id == route_run.id:
            return shipment, True
        previous = shipment.route_run
        ShipmentRouteAssignment.objects.create(
            shipment=shipment,
            previous_route_run=previous,
            new_route_run=route_run,
            changed_by=user,
            previous_route_snapshot=route_snapshot(previous),
            new_route_snapshot=route_snapshot(route_run),
            client_operation_id=client_operation_id or None,
        )
        shipment.route_run = route_run
        shipment_update_fields = ["route_run", "updated_at"]
        if shipment.document_status == Shipment.DocumentStatus.PRINTED:
            shipment.document_status = Shipment.DocumentStatus.REQUIRES_REFRESH
            shipment_update_fields.append("document_status")
        shipment.save(update_fields=shipment_update_fields)
        shipment.order.route_run = route_run
        shipment.order.save(update_fields=["route_run", "updated_at"])
        AuditLog.objects.create(
            actor=user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="shipment_route_changed",
            branch=shipment.branch,
            order=shipment.order,
            route_run=route_run,
            reference=shipment.reference,
            entity_name="Shipment",
            entity_id=str(shipment.id),
            message=f"{user.username} moved shipment {shipment.reference} to {route_snapshot(route_run)}.",
        )
        return shipment, False


def override_route_run(user, route_run: RouteRun, *, cutoff_at, planned_departure_at, dispatch_wave):
    with transaction.atomic():
        route_run = RouteRun.objects.select_for_update().select_related("route", "route__branch").get(pk=route_run.pk)
        require_branch_access(user, route_run.route.branch, leader_required=True)
        if route_run.status in TERMINAL_ROUTE_STATUSES:
            raise ValidationError("Closed, dispatched, or cancelled route runs cannot be overridden.")
        if cutoff_at >= planned_departure_at:
            raise ValidationError("Cutoff must be before departure.")
        history = RouteRunOverrideHistory.objects.create(
            route_run=route_run,
            changed_by=user,
            previous_cutoff_at=route_run.cutoff_at,
            new_cutoff_at=cutoff_at,
            previous_planned_departure_at=route_run.planned_departure_at,
            new_planned_departure_at=planned_departure_at,
            previous_dispatch_wave=route_run.dispatch_wave,
            new_dispatch_wave=dispatch_wave,
        )
        route_run.cutoff_at = cutoff_at
        route_run.planned_departure_at = planned_departure_at
        route_run.order_cutoff_time = timezone.localtime(cutoff_at).time()
        route_run.departure_time = timezone.localtime(planned_departure_at).time()
        route_run.dispatch_wave = dispatch_wave
        route_run.save(update_fields=["cutoff_at", "planned_departure_at", "order_cutoff_time", "departure_time", "dispatch_wave", "updated_at"])
        AuditLog.objects.create(
            actor=user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type="route_run_time_overridden",
            branch=route_run.route.branch,
            route_run=route_run,
            reference=route_run.operational_identifier,
            entity_name="RouteRun",
            entity_id=str(route_run.id),
            message=f"{user.username} updated route run timing for {route_run.operational_identifier}.",
        )
        return route_run, history
