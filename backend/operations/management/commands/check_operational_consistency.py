import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F, Q

from operations.models import CartPickedItem, PickingTask, RouteRun, Shipment, ShipmentLine
from operations.operational_projections import route_run_workload_projection, shipment_line_progress


class Command(BaseCommand):
    help = "Read-only validation of the persisted outbound operational graph."

    def add_arguments(self, parser):
        parser.add_argument("--branch")
        parser.add_argument("--fail-on-error", action="store_true")
        parser.add_argument("--include-closed", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        branch_code = options.get("branch")
        issues = []

        def add(code, message, **context):
            issues.append({"code": code, "message": message, **context})

        shipments = Shipment.objects.select_related("branch", "order", "route_run__route__branch")
        lines = ShipmentLine.objects.select_related("shipment__branch", "order_line__order", "product").prefetch_related(
            "order_line__picking_tasks"
        )
        route_runs = RouteRun.objects.select_related("route__branch").prefetch_related(
            "shipments__lines__order_line__picking_tasks"
        )
        if branch_code:
            shipments = shipments.filter(branch__code=branch_code)
            lines = lines.filter(shipment__branch__code=branch_code)
            route_runs = route_runs.filter(route__branch__code=branch_code)
        if not options["include_closed"]:
            route_runs = route_runs.exclude(status__in=[RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED])

        for shipment in shipments:
            if shipment.order.branch_id != shipment.branch_id:
                add("shipment_order_branch_mismatch", "Shipment and order branches differ.", shipment=shipment.reference)
            if shipment.route_run_id and shipment.route_run.route.branch_id != shipment.branch_id:
                add("shipment_route_branch_mismatch", "Shipment and route branches differ.", shipment=shipment.reference)

        for line in lines:
            progress = shipment_line_progress(line)
            reference = f"{line.shipment.reference}:{line.line_number}"
            if line.order_line.order_id != line.shipment.order_id or line.order_line.product_id != line.product_id:
                add("shipment_line_identity_mismatch", "Shipment line does not match its order line.", line=reference)
            if progress.effective_quantity < 0 or progress.removed_quantity < 0:
                add("negative_line_quantity", "Effective or removed quantity is negative.", line=reference)
            if progress.effective_quantity + progress.removed_quantity != progress.original_quantity:
                add("line_quantity_equation", "Effective plus removed does not equal original quantity.", line=reference)
            if progress.picked_quantity > progress.effective_quantity:
                add("picked_exceeds_effective", "Picked quantity exceeds effective quantity.", line=reference)
            if progress.controlled_quantity > progress.picked_quantity:
                add("controlled_exceeds_picked", "Controlled quantity exceeds picked quantity.", line=reference)
            if progress.prepared_quantity > progress.controlled_quantity:
                add("prepared_exceeds_controlled", "Prepared quantity exceeds controlled quantity.", line=reference)
            active_tasks = [task for task in line.order_line.picking_tasks.all() if task.status != PickingTask.Status.CANCELLED]
            if progress.effective_quantity == 0 and active_tasks:
                add("active_task_zero_effective", "Zero-effective line has active picking work.", line=reference)
            if active_tasks and progress.task_target_quantity != progress.effective_quantity:
                add("task_target_mismatch", "Active task targets do not equal effective quantity.", line=reference)
            for task in active_tasks:
                shipment_route_id = line.shipment.route_run_id
                if shipment_route_id != line.order_line.order.route_run_id:
                    add(
                        "task_route_context_mismatch",
                        "Picking task order RouteRun differs from its canonical Shipment RouteRun.",
                        task=task.id,
                    )
                if (
                    line.shipment.route_run
                    and line.shipment.route_run.status in {
                        RouteRun.Status.CLOSED,
                        RouteRun.Status.CANCELLED,
                        RouteRun.Status.DISPATCHED,
                    }
                    and task.status not in {PickingTask.Status.COMPLETED, PickingTask.Status.CANCELLED}
                    and task.quantity_picked + task.shortage_quantity < task.quantity_to_pick
                ):
                    add("scanner_task_terminal_route", "Scanner-visible task belongs to a terminal RouteRun.", task=task.id)
                if task.branch_id != line.shipment.branch_id or task.source_location.branch_id != task.branch_id:
                    add("task_branch_mismatch", "Picking task crosses branch boundaries.", task=task.id)
                if task.quantity_picked > task.quantity_to_pick:
                    add("task_picked_exceeds_target", "Task picked quantity exceeds target.", task=task.id)
                if task.quantity_prepared > task.quantity_picked:
                    add("task_prepared_exceeds_picked", "Task prepared quantity exceeds picked quantity.", task=task.id)

        terminal_runs = RouteRun.objects.filter(status__in=[RouteRun.Status.CLOSED, RouteRun.Status.CANCELLED])
        if branch_code:
            terminal_runs = terminal_runs.filter(route__branch__code=branch_code)
        terminal_runs = terminal_runs.prefetch_related("shipments__lines__order_line__picking_tasks")
        for route_run in terminal_runs:
            projection = route_run_workload_projection(route_run)
            if projection.total:
                add("terminal_route_active_workload", "Closed or cancelled route retains active workload.", route_run=route_run.id)

        for route_run in route_runs:
            projection = route_run_workload_projection(route_run)
            expected = sum(
                1
                for shipment in route_run.shipments.all()
                if shipment.status != Shipment.Status.CANCELLED
                for line in shipment.lines.all()
                if shipment_line_progress(line).effective_quantity > 0
            )
            if projection.total != expected:
                add("route_projection_total_mismatch", "Route workload buckets differ from effective lines.", route_run=route_run.id)

        orphan_items = CartPickedItem.objects.filter(
            Q(picking_task__isnull=True) | ~Q(picking_task__order_line__shipment_line__shipment__route_run=F("route_run"))
        )
        if branch_code:
            orphan_items = orphan_items.filter(route_run__route__branch__code=branch_code)
        for item_id in orphan_items.values_list("id", flat=True):
            add("orphaned_cart_picked_item", "Cart picked item is detached from its task route.", cart_picked_item=item_id)

        duplicate_rounds = RouteRun.objects.values("route_id", "service_date", "run_number").annotate(total=Count("id")).filter(total__gt=1)
        if branch_code:
            duplicate_rounds = duplicate_rounds.filter(route__branch__code=branch_code)
        for row in duplicate_rounds:
            add("duplicate_route_round", "Duplicate route/date/round identity.", **row)

        summary = {"branch": branch_code or "all", "errors": len(issues), "issues": issues}
        if options["json"]:
            self.stdout.write(json.dumps(summary, default=str, sort_keys=True))
        else:
            self.stdout.write(f"Operational consistency: {len(issues)} error(s) for {summary['branch']}.")
            for issue in issues:
                self.stdout.write(f"- {issue['code']}: {issue['message']}")
        if issues and options["fail_on_error"]:
            raise CommandError(f"Operational consistency check found {len(issues)} error(s).")

