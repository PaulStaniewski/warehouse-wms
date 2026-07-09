from decimal import Decimal
from dataclasses import dataclass

from operations.models import CartPickedItem, CartWorkSession, ScannerCart, ScannerCustomerLabel, TransferPallet
from operations.services import discrepancy_line_remaining, get_source_verification_totals, reconciliation_next_action
from warehouse.models import InventoryItem, Location


class ContentsLookupError(Exception):
    status_code = 400

    def __init__(self, detail, *, matched_object_types=None):
        super().__init__(detail)
        self.detail = detail
        self.matched_object_types = matched_object_types or []


class ContentsNotFound(ContentsLookupError):
    status_code = 404


class ContentsConflict(ContentsLookupError):
    status_code = 409


@dataclass
class ContentsMatch:
    object_type: str
    data: dict


def piece_quantity(value):
    value = Decimal(value)
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _location_contents(location: Location):
    inventory_items = (
        InventoryItem.objects.select_related("product")
        .filter(location=location, quantity_on_hand__gt=0)
        .order_by("product__sku")
    )
    items = [
        {
            "product_id": item.product_id,
            "sku": item.product.sku,
            "name": item.product.name,
            "quantity": piece_quantity(item.quantity_on_hand),
            "reserved_quantity": piece_quantity(item.quantity_reserved),
        }
        for item in inventory_items
    ]

    return ContentsMatch(
        "location",
        {
            "object_type": "location",
            "code": location.code,
            "title": f"Location {location.code}",
            "status": "active" if location.is_active else "inactive",
            "description": location.name or location.branch.name,
            "items": items,
        },
    )


def _combined_location_contents(code: str, locations: list[Location]):
    inventory_items = (
        InventoryItem.objects.select_related("product", "location", "location__branch")
        .filter(location__in=locations, quantity_on_hand__gt=0)
        .order_by("location__branch__code", "product__sku")
    )
    items = [
        {
            "product_id": item.product_id,
            "sku": item.product.sku,
            "name": item.product.name,
            "quantity": piece_quantity(item.quantity_on_hand),
            "reserved_quantity": piece_quantity(item.quantity_reserved),
            "branch_code": item.location.branch.code,
            "location_code": item.location.code,
        }
        for item in inventory_items
    ]
    branch_codes = ", ".join(location.branch.code for location in locations)
    return ContentsMatch(
        "location",
        {
            "object_type": "location",
            "code": code,
            "title": f"Location {code}",
            "status": "active",
            "description": f"Matching branch locations: {branch_codes}",
            "items": items,
        },
    )


def _cart_contents(cart: ScannerCart):
    active_work = (
        CartWorkSession.objects.select_related("picking_job")
        .filter(cart=cart, status__in=[CartWorkSession.Status.ACTIVE, CartWorkSession.Status.CONTROL])
        .first()
    )
    cart_items = (
        CartPickedItem.objects.select_related("product", "picking_task__order_line__order")
        .filter(cart=cart, quantity_picked__gt=0)
        .order_by("picking_task__order_line__order__external_reference", "product__sku", "created_at")
    )
    if active_work:
        cart_items = cart_items.filter(cart_work_session=active_work)

    items = []
    for item in cart_items:
        order = item.picking_task.order_line.order
        remaining = item.quantity_picked - item.quantity_prepared
        items.append(
            {
                "product_id": item.product_id,
                "sku": item.product.sku,
                "name": item.product.name,
                "quantity": piece_quantity(item.quantity_picked),
                "picked_quantity": piece_quantity(item.quantity_picked),
                "prepared_quantity": piece_quantity(item.quantity_prepared),
                "remaining_quantity": piece_quantity(remaining),
                "order_reference": order.external_reference,
                "customer_name": order.customer_name,
            }
        )

    description = f"Picking Job {active_work.picking_job_id}" if active_work else cart.name
    return ContentsMatch(
        "cart",
        {
            "object_type": "cart",
            "code": cart.code,
            "title": f"Cart {cart.code}",
            "status": cart.status,
            "description": description,
            "items": items,
        },
    )


def _customer_label_contents(label: ScannerCustomerLabel):
    cart_items = (
        CartPickedItem.objects.select_related("product", "picking_task__order_line__order")
        .filter(
            session=label.session,
            picking_task__order_line__order=label.order,
            quantity_prepared__gt=0,
        )
        .order_by("product__sku", "created_at")
    )
    items = [
        {
            "product_id": item.product_id,
            "sku": item.product.sku,
            "name": item.product.name,
            "quantity": piece_quantity(item.quantity_prepared),
            "prepared_quantity": piece_quantity(item.quantity_prepared),
            "order_reference": label.order.external_reference,
            "customer_name": label.order.customer_name,
        }
        for item in cart_items
    ]

    code = label.scan_code
    return ContentsMatch(
        "customer_label",
        {
            "object_type": "customer_label",
            "code": code,
            "title": f"Customer label {code}",
            "status": "ready",
            "description": f"{label.order.customer_name or '-'} / {label.order.external_reference}",
            "items": items,
        },
    )


def _pallet_contents(pallet: TransferPallet):
    discrepancy = getattr(pallet, "discrepancy", None)
    source_review = getattr(discrepancy, "source_review", None) if discrepancy else None
    reconciliation = getattr(discrepancy, "reconciliation", None) if discrepancy else None
    manual_decision = getattr(reconciliation, "manual_decision", None) if reconciliation else None
    source_verification = getattr(reconciliation, "source_stock_verification", None) if reconciliation else None
    transit_investigation = getattr(reconciliation, "transit_investigation", None) if reconciliation else None
    discrepancy_items = {item.pallet_item_id: item for item in discrepancy.items.all()} if discrepancy else {}
    items = []
    for item in pallet.items.select_related("product").order_by("product__sku"):
        remaining = item.expected_quantity - item.received_quantity
        discrepancy_item = discrepancy_items.get(item.id)
        items.append(
            {
                "product_id": item.product_id,
                "sku": item.product.sku,
                "name": item.product.name,
                "quantity": piece_quantity(item.expected_quantity),
                "expected_quantity": piece_quantity(item.expected_quantity),
                "received_quantity": piece_quantity(item.received_quantity),
                "remaining_quantity": piece_quantity(remaining),
                "missing_quantity": piece_quantity(discrepancy_item.discrepancy_quantity) if discrepancy_item else 0,
                "posted_to_unconfirmed_quantity": piece_quantity(discrepancy_item.posted_to_unconfirmed_quantity)
                if discrepancy_item
                else 0,
                "recovered_quantity": piece_quantity(discrepancy_item.recovered_quantity) if discrepancy_item else 0,
                "confirmed_shortage_quantity": piece_quantity(discrepancy_item.confirmed_shortage_quantity)
                if discrepancy_item
                else 0,
                "investigation_remaining_quantity": piece_quantity(discrepancy_line_remaining(discrepancy_item))
                if discrepancy_item
                else 0,
                "discrepancy_type": discrepancy_item.discrepancy_type if discrepancy_item else None,
            }
        )

    transfer = pallet.transfer
    return ContentsMatch(
        "pallet",
        {
            "object_type": "pallet",
            "code": pallet.scan_code,
            "title": f"Pallet {pallet.scan_code}",
            "status": pallet.status,
            "description": (
                f"{transfer.source_branch.code} -> {transfer.destination_branch.code} / {transfer.reference}"
                + (f" / Discrepancy: {discrepancy.reference}" if discrepancy else "")
            ),
            "discrepancy_reference": discrepancy.reference if discrepancy else None,
            "discrepancy_status": discrepancy.status if discrepancy else None,
            "report_printed": bool(discrepancy and discrepancy.report_printed_at),
            "shortage_posted": bool(discrepancy and discrepancy.shortage_posted_at),
            "source_review": {
                "id": source_review.id,
                "reference": source_review.reference,
                "status": source_review.status,
                "finding": source_review.finding,
                "finding_display": source_review.get_finding_display() if source_review.finding else "",
            }
            if source_review
            else None,
            "reconciliation": {
                "id": reconciliation.id,
                "reference": reconciliation.reference,
                "route": reconciliation.route,
                "route_label": reconciliation.get_route_display(),
                "status": reconciliation.status,
                "next_action_label": reconciliation_next_action(
                    reconciliation.route,
                    reconciliation.status,
                    manual_decision is not None,
                ),
                "manual_decision": {
                    "outcome": manual_decision.outcome,
                    "outcome_label": manual_decision.get_outcome_display(),
                    "decided_at": manual_decision.decided_at.isoformat() if manual_decision.decided_at else None,
                    "decided_by_worker_code": manual_decision.decided_by_worker_code,
                }
                if manual_decision
                else None,
            }
            if reconciliation
            else None,
            "source_stock_verification": {
                "id": source_verification.id,
                "reference": source_verification.reference,
                "status": source_verification.status,
                "status_label": source_verification.get_status_display(),
                "total_target_quantity": piece_quantity(get_source_verification_totals(source_verification)["target"]),
                "total_found_quantity": piece_quantity(get_source_verification_totals(source_verification)["found"]),
                "total_remaining_quantity": piece_quantity(get_source_verification_totals(source_verification)["remaining"]),
                "total_unresolved_quantity": piece_quantity(
                    get_source_verification_totals(source_verification)["unresolved"]
                ),
            }
            if source_verification
            else None,
            "transit_investigation": {
                "id": transit_investigation.id,
                "reference": transit_investigation.reference,
                "status": transit_investigation.status,
                "status_label": transit_investigation.get_status_display(),
                "finding": transit_investigation.finding,
                "finding_label": transit_investigation.get_finding_display() if transit_investigation.finding else "",
            }
            if transit_investigation
            else None,
            "items": items,
        },
    )


def _find_label_by_code(code: str):
    return ScannerCustomerLabel.objects.select_related("session", "order").filter(scan_code__iexact=code.strip()).first()


def resolve_contents_code(code: str):
    code = str(code or "").strip()
    if not code:
        raise ContentsLookupError("code query parameter is required.")

    matches = []
    locations = list(Location.objects.select_related("branch").filter(code__iexact=code).order_by("branch__code"))
    if len(locations) == 1:
        matches.append(_location_contents(locations[0]))
    elif len(locations) > 1:
        matches.append(_combined_location_contents(code, locations))

    cart = ScannerCart.objects.filter(code__iexact=code).first()
    if cart:
        matches.append(_cart_contents(cart))

    label = _find_label_by_code(code)
    if label:
        matches.append(_customer_label_contents(label))

    pallet = TransferPallet.objects.select_related(
        "transfer",
        "transfer__source_branch",
        "transfer__destination_branch",
    ).filter(scan_code__iexact=code).first()
    if pallet:
        matches.append(_pallet_contents(pallet))

    if len(matches) > 1:
        raise ContentsConflict(
            "Code matches more than one warehouse object.",
            matched_object_types=[match.object_type for match in matches],
        )
    if not matches:
        raise ContentsNotFound("Code not found.")

    return matches[0].data
