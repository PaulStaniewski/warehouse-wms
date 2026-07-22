from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from operations.models import (
    AuditLog,
    PickingTask,
    RouteRun,
    TransferDiscrepancy,
    TransferDiscrepancyReconciliation,
    TransferDiscrepancySourceStockVerification,
    TransferDiscrepancySourceStockVerificationItem,
    TransferDiscrepancySourceReview,
    TransferDiscrepancyTransitInvestigation,
)
from warehouse.models import Location


TERMINAL_ROUTE_STATUSES = {
    RouteRun.Status.CLOSED,
    RouteRun.Status.DISPATCHED,
    RouteRun.Status.CANCELLED,
}

DISCREPANCY_LOCATION_CODE = "UNCONFIRMED"


class DiscrepancyLocationMissing(ValueError):
    pass


def get_discrepancy_location(destination_branch):
    location = Location.objects.filter(branch=destination_branch, code__iexact=DISCREPANCY_LOCATION_CODE).first()
    if location is None:
        raise DiscrepancyLocationMissing(
            f"Discrepancy location {DISCREPANCY_LOCATION_CODE} is missing for branch {destination_branch.code}."
        )
    return location


def discrepancy_line_remaining(item) -> Decimal:
    return item.posted_to_unconfirmed_quantity - item.recovered_quantity - item.confirmed_shortage_quantity


def get_discrepancy_investigation_totals(discrepancy) -> dict[str, Decimal]:
    items = list(discrepancy.items.all())
    total_posted = sum((item.posted_to_unconfirmed_quantity for item in items), Decimal("0"))
    total_recovered = sum((item.recovered_quantity for item in items), Decimal("0"))
    total_confirmed_shortage = sum((item.confirmed_shortage_quantity for item in items), Decimal("0"))
    total_remaining = sum((discrepancy_line_remaining(item) for item in items), Decimal("0"))
    return {
        "posted": total_posted,
        "recovered": total_recovered,
        "confirmed_shortage": total_confirmed_shortage,
        "remaining": total_remaining,
    }


RECONCILIATION_NEXT_ACTIONS = {
    TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION: (
        "Verify whether the confirmed shortage quantity still physically exists at the source branch."
    ),
    TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION: (
        "Investigate the transfer between source dispatch and destination receiving."
    ),
    TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION: (
        "A manual reconciliation decision is required because the available evidence is inconclusive."
    ),
}


def reconciliation_route_for_finding(finding: str) -> str:
    mapping = {
        TransferDiscrepancySourceReview.Finding.SOURCE_SHORTAGE_FOUND: (
            TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION
        ),
        TransferDiscrepancySourceReview.Finding.DISPATCH_EVIDENCE_MATCHES: (
            TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION
        ),
        TransferDiscrepancySourceReview.Finding.INCONCLUSIVE: (
            TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION
        ),
    }
    return mapping[finding]


def reconciliation_next_action(route: str, status: str | None = None, has_manual_decision: bool = False) -> str:
    if status == TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED:
        if route == TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION:
            return "Review the transit investigation finding and record the final reconciliation outcome."
        return "Review the unresolved source stock and record the final reconciliation outcome."
    if status == TransferDiscrepancyReconciliation.Status.COMPLETED and has_manual_decision:
        return "The reconciliation has been completed with a final manual outcome."
    if route == TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION and status == TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
        return "Review the complete discrepancy evidence and record the final reconciliation outcome."
    return RECONCILIATION_NEXT_ACTIONS[route]


TRANSIT_INVESTIGATION_NEXT_ACTIONS = {
    TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION: "Begin transit investigation.",
    TransferDiscrepancyTransitInvestigation.Status.INVESTIGATING: (
        "Review the transfer, route and receiving evidence and record the transit investigation finding."
    ),
    TransferDiscrepancyTransitInvestigation.Status.COMPLETED: (
        "The transit investigation is complete. A final manual reconciliation decision is required."
    ),
}


def transit_investigation_next_action(status: str) -> str:
    return TRANSIT_INVESTIGATION_NEXT_ACTIONS[status]


DISCREPANCY_ACTION_LABELS = {
    "review_destination_shortage": "Review destination shortage",
    "begin_source_review": "Begin source review",
    "complete_source_review": "Complete source review",
    "acknowledge_reconciliation": "Acknowledge reconciliation",
    "begin_source_stock_verification": "Begin source stock verification",
    "continue_source_stock_verification": "Continue source stock verification",
    "complete_source_search": "Complete source search",
    "begin_transit_investigation": "Begin transit investigation",
    "complete_transit_investigation": "Complete transit investigation",
    "record_final_reconciliation_outcome": "Record final reconciliation outcome",
}


def _action_base(discrepancy: TransferDiscrepancy) -> dict:
    totals = get_discrepancy_investigation_totals(discrepancy)
    return {
        "discrepancy_reference": discrepancy.reference,
        "transfer_reference": discrepancy.transfer.reference,
        "pallet_reference": discrepancy.pallet.scan_code,
        "source_branch": discrepancy.transfer.source_branch.code,
        "destination_branch": discrepancy.transfer.destination_branch.code,
        "confirmed_shortage_quantity": str(totals["confirmed_shortage"]),
        "created_at": discrepancy.created_at,
    }


def _action_row(
    discrepancy: TransferDiscrepancy,
    action_type: str,
    target,
    target_type: str,
    target_url: str,
    visible_branches: list[str],
) -> dict:
    return {
        **_action_base(discrepancy),
        "action_type": action_type,
        "action_label": DISCREPANCY_ACTION_LABELS[action_type],
        "target_type": target_type,
        "target_reference": target.reference,
        "target_url": target_url,
        "route": getattr(target, "route", ""),
        "route_label": target.get_route_display() if hasattr(target, "get_route_display") else "",
        "current_status": target.status,
        "current_status_label": target.get_status_display(),
        "waiting_since": getattr(target, "updated_at", None) or getattr(target, "created_at", None),
        "visible_branches": visible_branches,
    }


def _source_stock_action_type(verification: TransferDiscrepancySourceStockVerification) -> str:
    if verification.status == TransferDiscrepancySourceStockVerification.Status.PENDING_VERIFICATION:
        return "begin_source_stock_verification"
    if verification.recoveries.exists():
        return "complete_source_search"
    return "continue_source_stock_verification"


def build_transfer_discrepancy_action_queue() -> list[dict]:
    discrepancies = (
        TransferDiscrepancy.objects.select_related(
            "pallet",
            "transfer",
            "transfer__source_branch",
            "transfer__destination_branch",
            "source_review",
            "reconciliation",
            "reconciliation__source_stock_verification",
            "reconciliation__transit_investigation",
            "reconciliation__manual_decision",
        )
        .prefetch_related("items", "reconciliation__source_stock_verification__recoveries")
        .exclude(status=TransferDiscrepancy.Status.RESOLVED)
        .order_by("created_at")
    )
    rows = []
    for discrepancy in discrepancies:
        reconciliation = getattr(discrepancy, "reconciliation", None)
        if reconciliation is not None:
            if reconciliation.status == TransferDiscrepancyReconciliation.Status.COMPLETED:
                continue
            if getattr(reconciliation, "manual_decision", None) is None:
                if (
                    reconciliation.status == TransferDiscrepancyReconciliation.Status.MANUAL_ACTION_REQUIRED
                    or (
                        reconciliation.route == TransferDiscrepancyReconciliation.Route.MANUAL_RECONCILIATION
                        and reconciliation.status == TransferDiscrepancyReconciliation.Status.IN_PROGRESS
                    )
                ):
                    rows.append(
                        _action_row(
                            discrepancy,
                            "record_final_reconciliation_outcome",
                            reconciliation,
                            "reconciliation",
                            f"/wms/discrepancy-reconciliations/{reconciliation.id}",
                            [discrepancy.transfer.source_branch.code, discrepancy.transfer.destination_branch.code],
                        )
                    )
                    continue
            transit_investigation = getattr(reconciliation, "transit_investigation", None)
            if transit_investigation is not None and transit_investigation.status != TransferDiscrepancyTransitInvestigation.Status.COMPLETED:
                action_type = (
                    "begin_transit_investigation"
                    if transit_investigation.status == TransferDiscrepancyTransitInvestigation.Status.PENDING_INVESTIGATION
                    else "complete_transit_investigation"
                )
                rows.append(
                    _action_row(
                        discrepancy,
                        action_type,
                        transit_investigation,
                        "transit_investigation",
                        f"/wms/transit-investigations/{transit_investigation.id}",
                        [discrepancy.transfer.source_branch.code, discrepancy.transfer.destination_branch.code],
                    )
                )
                continue
            source_stock_verification = getattr(reconciliation, "source_stock_verification", None)
            if (
                source_stock_verification is not None
                and source_stock_verification.status
                not in [
                    TransferDiscrepancySourceStockVerification.Status.COMPLETED,
                    TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED,
                ]
            ):
                rows.append(
                    _action_row(
                        discrepancy,
                        _source_stock_action_type(source_stock_verification),
                        source_stock_verification,
                        "source_stock_verification",
                        f"/wms/source-stock-verifications/{source_stock_verification.id}",
                        [discrepancy.transfer.source_branch.code],
                    )
                )
                continue
            if reconciliation.status == TransferDiscrepancyReconciliation.Status.PENDING_ACTION:
                rows.append(
                    _action_row(
                        discrepancy,
                        "acknowledge_reconciliation",
                        reconciliation,
                        "reconciliation",
                        f"/wms/discrepancy-reconciliations/{reconciliation.id}",
                        [discrepancy.transfer.source_branch.code, discrepancy.transfer.destination_branch.code],
                    )
                )
                continue

        source_review = getattr(discrepancy, "source_review", None)
        if source_review is not None and source_review.status != TransferDiscrepancySourceReview.Status.COMPLETED:
            action_type = (
                "begin_source_review"
                if source_review.status == TransferDiscrepancySourceReview.Status.PENDING_REVIEW
                else "complete_source_review"
            )
            rows.append(
                _action_row(
                    discrepancy,
                    action_type,
                    source_review,
                    "source_review",
                    f"/wms/source-discrepancy-reviews/{source_review.id}",
                    [discrepancy.transfer.source_branch.code],
                )
            )
            continue

        if discrepancy.status in [TransferDiscrepancy.Status.OPEN, TransferDiscrepancy.Status.INVESTIGATING]:
            rows.append(
                _action_row(
                    discrepancy,
                    "review_destination_shortage",
                    discrepancy,
                    "discrepancy",
                    f"/wms/discrepancies/{discrepancy.id}",
                    [discrepancy.transfer.destination_branch.code],
                )
            )
    return rows


def ensure_reconciliation_for_source_review(source_review):
    if source_review.status != TransferDiscrepancySourceReview.Status.COMPLETED:
        raise ValueError("Source review must be completed before reconciliation.")
    if source_review.discrepancy.status != TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
        raise ValueError("Reconciliation requires a confirmed-shortage discrepancy.")

    route = reconciliation_route_for_finding(source_review.finding)
    reconciliation, created = TransferDiscrepancyReconciliation.objects.get_or_create(
        discrepancy=source_review.discrepancy,
        defaults={
            "source_review": source_review,
            "route": route,
        },
    )
    if reconciliation.source_review_id != source_review.id:
        raise ValueError("Reconciliation source review does not match the discrepancy.")
    if created:
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.CREATE,
            entity_name="TransferDiscrepancyReconciliation",
            entity_id=str(reconciliation.id),
            message=(
                f"Reconciliation case {reconciliation.reference} was created for discrepancy "
                f"{source_review.discrepancy.reference} with route: {reconciliation.get_route_display()}."
            ),
        )
    return reconciliation, created


SOURCE_VERIFICATION_NEXT_ACTIONS = {
    TransferDiscrepancySourceStockVerification.Status.PENDING_VERIFICATION: "Begin source stock verification.",
    TransferDiscrepancySourceStockVerification.Status.INVESTIGATING: (
        "Search the source warehouse for the remaining confirmed shortage quantity."
    ),
    TransferDiscrepancySourceStockVerification.Status.COMPLETED: (
        "All target shortage quantity was physically found at the source branch and restored to inventory."
    ),
    TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED: (
        "The source search was completed with unresolved quantity."
    ),
}


def source_verification_item_remaining(item) -> Decimal:
    return item.target_quantity - item.found_quantity


def get_source_verification_totals(verification) -> dict[str, Decimal]:
    items = list(verification.items.all())
    total_target = sum((item.target_quantity for item in items), Decimal("0"))
    total_found = sum((item.found_quantity for item in items), Decimal("0"))
    total_remaining = sum((source_verification_item_remaining(item) for item in items), Decimal("0"))
    total_unresolved = (
        total_remaining
        if verification.status == TransferDiscrepancySourceStockVerification.Status.COMPLETED_UNRESOLVED
        else Decimal("0")
    )
    return {
        "target": total_target,
        "found": total_found,
        "remaining": total_remaining,
        "unresolved": total_unresolved,
    }


def source_verification_next_action(status: str) -> str:
    return SOURCE_VERIFICATION_NEXT_ACTIONS[status]


def ensure_source_stock_verification_for_reconciliation(reconciliation):
    if reconciliation.route != TransferDiscrepancyReconciliation.Route.SOURCE_STOCK_VERIFICATION:
        return None, False
    if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
        raise ValueError("Reconciliation must be in progress before source stock verification.")

    verification, created = TransferDiscrepancySourceStockVerification.objects.get_or_create(
        reconciliation=reconciliation,
    )
    if created:
        items = reconciliation.discrepancy.items.select_related("product").filter(confirmed_shortage_quantity__gt=0)
        for item in items:
            TransferDiscrepancySourceStockVerificationItem.objects.create(
                verification=verification,
                discrepancy_item=item,
                product=item.product,
                target_quantity=item.confirmed_shortage_quantity,
            )
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.CREATE,
            entity_name="TransferDiscrepancySourceStockVerification",
            entity_id=str(verification.id),
            message=(
                f"Source stock verification {verification.reference} was created for reconciliation "
                f"{reconciliation.reference}."
            ),
        )
    return verification, created


def ensure_transit_investigation_for_reconciliation(reconciliation):
    if reconciliation.route != TransferDiscrepancyReconciliation.Route.TRANSIT_INVESTIGATION:
        return None, False
    if reconciliation.status != TransferDiscrepancyReconciliation.Status.IN_PROGRESS:
        raise ValueError("Reconciliation must be in progress before transit investigation.")

    investigation, created = TransferDiscrepancyTransitInvestigation.objects.get_or_create(
        reconciliation=reconciliation,
    )
    if created:
        AuditLog.objects.create(
            action_type=AuditLog.ActionType.CREATE,
            entity_name="TransferDiscrepancyTransitInvestigation",
            entity_id=str(investigation.id),
            message=(
                f"Transit investigation {investigation.reference} was created for reconciliation "
                f"{reconciliation.reference}."
            ),
        )
    return investigation, created


def complete_source_verification_if_finished(verification, worker_code: str) -> tuple[bool, bool]:
    totals = get_source_verification_totals(verification)
    if totals["remaining"] > 0:
        verification.save(update_fields=["updated_at"])
        return False, False

    verification_completed = False
    reconciliation_completed = False
    now = timezone.now()
    if verification.status != TransferDiscrepancySourceStockVerification.Status.COMPLETED:
        verification.status = TransferDiscrepancySourceStockVerification.Status.COMPLETED
        verification.completed_at = now
        verification.completed_by_worker_code = worker_code
        verification.save(update_fields=["status", "completed_at", "completed_by_worker_code", "updated_at"])
        verification_completed = True

    reconciliation = verification.reconciliation
    if reconciliation.status != TransferDiscrepancyReconciliation.Status.COMPLETED:
        reconciliation.status = TransferDiscrepancyReconciliation.Status.COMPLETED
        reconciliation.completed_at = now
        reconciliation.completed_by_worker_code = worker_code
        reconciliation.save(update_fields=["status", "completed_at", "completed_by_worker_code", "updated_at"])
        reconciliation_completed = True

    return verification_completed, reconciliation_completed


def finalize_discrepancy_if_complete(discrepancy, worker_code: str) -> tuple[bool, str | None]:
    totals = get_discrepancy_investigation_totals(discrepancy)
    if totals["remaining"] > 0:
        discrepancy.save(update_fields=["updated_at"])
        return False, None

    now = timezone.now()
    if totals["confirmed_shortage"] > 0:
        review, review_created = TransferDiscrepancySourceReview.objects.get_or_create(
            discrepancy=discrepancy,
            defaults={"source_branch": discrepancy.transfer.source_branch},
        )
        if review_created:
            AuditLog.objects.create(
                action_type=AuditLog.ActionType.CREATE,
                entity_name="TransferDiscrepancySourceReview",
                entity_id=str(review.id),
                message=(
                    f"Source review {review.reference} was created for confirmed shortage discrepancy "
                    f"{discrepancy.reference}."
                ),
            )

        if discrepancy.status == TransferDiscrepancy.Status.CONFIRMED_SHORTAGE:
            return False, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE

        discrepancy.status = TransferDiscrepancy.Status.CONFIRMED_SHORTAGE
        discrepancy.confirmed_shortage_at = now
        discrepancy.confirmed_shortage_by_worker_code = worker_code
        discrepancy.save(
            update_fields=[
                "status",
                "confirmed_shortage_at",
                "confirmed_shortage_by_worker_code",
                "updated_at",
            ]
        )
        return True, TransferDiscrepancy.Status.CONFIRMED_SHORTAGE

    if discrepancy.status == TransferDiscrepancy.Status.RESOLVED:
        return False, TransferDiscrepancy.Status.RESOLVED

    discrepancy.status = TransferDiscrepancy.Status.RESOLVED
    discrepancy.resolved_at = now
    discrepancy.resolved_by_worker_code = worker_code
    discrepancy.save(update_fields=["status", "resolved_at", "resolved_by_worker_code", "updated_at"])
    return True, TransferDiscrepancy.Status.RESOLVED


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


def effective_task_required_quantity(task: PickingTask) -> Decimal:
    customer_unfulfilled = sum(
        (shortage.customer_unfulfilled_quantity for shortage in task.shortages.all()),
        Decimal("0"),
    )
    return task.quantity_to_pick - task.shortage_quantity + customer_unfulfilled


def is_task_effectively_prepared(task: PickingTask) -> bool:
    return task.quantity_prepared >= effective_task_required_quantity(task)


def is_route_work_fully_prepared(route_run: RouteRun) -> bool:
    from operations.models import Shipment

    shipments = list(
        Shipment.objects.filter(route_run=route_run)
        .exclude(status=Shipment.Status.CANCELLED)
        .prefetch_related("lines__order_line__picking_tasks__shortages")
    )
    if shipments:
        for shipment in shipments:
            if shipment.status not in {
                Shipment.Status.PREPARED,
                Shipment.Status.DOCUMENTS_POSTED,
                Shipment.Status.READY_FOR_DISPATCH,
                Shipment.Status.DISPATCHED,
                Shipment.Status.COMPLETED,
            }:
                return False
            from operations.operational_projections import shipment_line_progress

            for line in shipment.lines.all():
                progress = shipment_line_progress(line)
                if progress.effective_quantity <= 0:
                    continue
                if progress.state != "prepared":
                    return False
        return True

    tasks = list(
        PickingTask.objects.prefetch_related("shortages")
        .filter(order_line__order__route_run=route_run)
        .exclude(status=PickingTask.Status.CANCELLED)
    )
    if not tasks:
        return False

    return all(is_task_effectively_prepared(task) for task in tasks)


def is_picking_job_work_fully_prepared(picking_job) -> bool:
    tasks = list(
        PickingTask.objects.prefetch_related("shortages")
        .filter(job_task__picking_job=picking_job)
        .exclude(status=PickingTask.Status.CANCELLED)
    )
    if not tasks:
        return False

    return all(is_task_effectively_prepared(task) for task in tasks)


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
