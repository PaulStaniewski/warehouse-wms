from decimal import Decimal
from dataclasses import dataclass

from operations.models import CartPickedItem, CartWorkSession, ScannerCart, ScannerCustomerLabel
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


def _find_label_by_code(code: str):
    return ScannerCustomerLabel.objects.select_related("session", "order").filter(scan_code__iexact=code.strip()).first()


def resolve_contents_code(code: str):
    code = str(code or "").strip()
    if not code:
        raise ContentsLookupError("code query parameter is required.")

    matches = []
    location = Location.objects.select_related("branch").filter(code__iexact=code).first()
    if location:
        matches.append(_location_contents(location))

    cart = ScannerCart.objects.filter(code__iexact=code).first()
    if cart:
        matches.append(_cart_contents(cart))

    label = _find_label_by_code(code)
    if label:
        matches.append(_customer_label_contents(label))

    if len(matches) > 1:
        raise ContentsConflict(
            "Code matches more than one warehouse object.",
            matched_object_types=[match.object_type for match in matches],
        )
    if not matches:
        raise ContentsNotFound("Code not found.")

    return matches[0].data
