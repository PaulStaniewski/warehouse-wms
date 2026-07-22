"""Authoritative idempotent boundary for externally sourced outbound demand."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from operations.models import AuditLog, DeliveryRoute, Order, OrderLine, PickingTask, Shipment, ShipmentLine
from operations.route_services import assign_shipment_to_route_run
from warehouse.models import Branch, InventoryItem, Location, Product


@dataclass(frozen=True)
class ExternalShipmentLineInput:
    external_line_reference: str
    line_number: int
    product_sku: str
    quantity: Decimal


@dataclass(frozen=True)
class ExternalShipmentInput:
    source_system: str
    external_order_reference: str
    external_shipment_reference: str
    shipment_reference: str
    branch_code: str
    route_code: str
    customer_name: str
    external_created_at: datetime
    lines: tuple[ExternalShipmentLineInput, ...]


def _source_location(branch: Branch, product: Product) -> Location:
    inventory = (
        InventoryItem.objects.select_related("location")
        .filter(branch=branch, product=product, location__is_active=True)
        .order_by("-quantity_on_hand", "location__code")
        .first()
    )
    if inventory:
        return inventory.location
    location = branch.locations.filter(location_type=Location.LocationType.PICKING, is_active=True).order_by("code").first()
    if location is None:
        raise ValidationError(f"Branch {branch.code} has no active picking location for imported work.")
    return location


def _synchronize_new_or_unstarted_task(line: ShipmentLine) -> PickingTask | None:
    effective = line.ordered_quantity - line.cancelled_quantity
    tasks = list(line.order_line.picking_tasks.select_for_update().order_by("id"))
    worked = [task for task in tasks if task.quantity_picked > 0 or task.quantity_prepared > 0]
    active = [task for task in tasks if task.status != PickingTask.Status.CANCELLED]
    if effective <= 0:
        if worked:
            raise ValidationError("A zero-effective imported line cannot retain picked or prepared work.")
        for task in active:
            task.status = PickingTask.Status.CANCELLED
            task.save(update_fields=["status", "updated_at"])
        return None
    if worked and sum((task.quantity_to_pick for task in active), Decimal("0")) != effective:
        raise ValidationError("External refresh cannot change quantity after warehouse work has started.")
    if active:
        primary = active[0]
        primary.quantity_to_pick = effective
        primary.save(update_fields=["quantity_to_pick", "updated_at"])
        for duplicate in active[1:]:
            if duplicate.quantity_picked or duplicate.quantity_prepared:
                raise ValidationError("Imported line has contradictory active picking work.")
            duplicate.status = PickingTask.Status.CANCELLED
            duplicate.save(update_fields=["status", "updated_at"])
        return primary
    return PickingTask.objects.create(
        branch=line.shipment.branch,
        order_line=line.order_line,
        source_location=_source_location(line.shipment.branch, line.product),
        status=PickingTask.Status.OPEN,
        quantity_to_pick=effective,
    )


@transaction.atomic
def upsert_external_shipment(payload: ExternalShipmentInput, *, actor=None) -> tuple[Shipment, bool]:
    branch = Branch.objects.select_for_update().get(code=payload.branch_code)
    route = DeliveryRoute.objects.select_related("branch").get(branch=branch, code=payload.route_code, is_active=True)
    order, order_created = Order.objects.select_for_update().get_or_create(
        external_reference=payload.external_order_reference,
        defaults={"branch": branch, "customer_name": payload.customer_name, "status": Order.Status.IMPORTED},
    )
    if order.branch_id != branch.id:
        raise ValidationError("External order belongs to another branch.")
    shipment, shipment_created = Shipment.objects.select_for_update().get_or_create(
        source_system=payload.source_system,
        external_reference=payload.external_shipment_reference,
        defaults={
            "reference": payload.shipment_reference,
            "branch": branch,
            "order": order,
            "external_order_reference": payload.external_order_reference,
            "customer_name": payload.customer_name,
            "external_created_at": payload.external_created_at,
            "status": Shipment.Status.PICKING,
            "picking_lists_posted_at": timezone.now(),
        },
    )
    if shipment.branch_id != branch.id or shipment.order_id != order.id:
        raise ValidationError("External shipment identity conflicts with its persisted branch or order.")

    seen_line_numbers = set()
    for item in payload.lines:
        if item.quantity <= 0:
            raise ValidationError("Imported line quantity must be positive.")
        if item.line_number in seen_line_numbers:
            raise ValidationError("External payload contains a duplicate line number.")
        seen_line_numbers.add(item.line_number)
        product = Product.objects.get(sku=item.product_sku, is_active=True)
        order_line, _ = OrderLine.objects.select_for_update().get_or_create(
            order=order,
            line_number=item.line_number,
            defaults={"product": product, "quantity_ordered": item.quantity},
        )
        if order_line.product_id != product.id:
            raise ValidationError("External order line product identity changed.")
        line, _ = ShipmentLine.objects.select_for_update().get_or_create(
            shipment=shipment,
            line_number=item.line_number,
            defaults={
                "order_line": order_line,
                "product": product,
                "external_line_reference": item.external_line_reference,
                "ordered_quantity": item.quantity,
            },
        )
        if line.order_line_id != order_line.id or line.product_id != product.id:
            raise ValidationError("External shipment line identity conflicts with its order line.")
        if line.ordered_quantity != item.quantity:
            if line.cancelled_quantity or order_line.quantity_picked:
                raise ValidationError("External refresh cannot rewrite original quantity after fulfilment activity.")
            line.ordered_quantity = item.quantity
            order_line.quantity_ordered = item.quantity
            line.save(update_fields=["ordered_quantity", "updated_at"])
            order_line.save(update_fields=["quantity_ordered", "updated_at"])
        _synchronize_new_or_unstarted_task(line)

    if shipment.route_run_id is None:
        route_run = assign_shipment_to_route_run(shipment, route, payload.external_created_at, actor=actor)
        if route_run is None:
            raise ValidationError("No eligible route round exists for imported shipment demand.")
    if shipment_created:
        AuditLog.objects.create(
            actor=actor,
            action_type=AuditLog.ActionType.CREATE,
            event_type="external_shipment_imported",
            branch=branch,
            order=order,
            route_run=shipment.route_run,
            reference=shipment.reference,
            entity_name="Shipment",
            entity_id=str(shipment.id),
            message=f"Imported external shipment {shipment.reference} through the operational boundary.",
        )
    return shipment, shipment_created or order_created

