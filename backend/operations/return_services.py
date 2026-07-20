import hashlib
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from accounts.authorization import require_branch_access
from operations.models import (
    AuditLog,
    ExternalReturnDocument,
    ExternalReturnDocumentLine,
    ReturnAction,
    SalesCorrection,
    SalesCorrectionLine,
    StockMovement,
)
from warehouse.models import InventoryItem, Location, Product


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The operation conflicts with an existing operation."
    default_code = "conflict"


def parse_positive_quantity(value, field_name="quantity") -> Decimal:
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({field_name: "Quantity must be a valid number."})
    if quantity <= 0:
        raise ValidationError({field_name: "Quantity must be positive."})
    return quantity


def validate_operation_id(value):
    operation_id = str(value or "").strip()
    if not operation_id:
        raise ValidationError({"client_operation_id": "client_operation_id is required."})
    try:
        UUID(operation_id)
    except ValueError:
        raise ValidationError({"client_operation_id": "client_operation_id must be a UUID."})
    return operation_id


def payload_fingerprint(payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_returns_area(branch):
    preferred = Location.objects.filter(
        branch=branch,
        code__iexact="RETURNS",
        location_type=Location.LocationType.RETURNS,
        is_active=True,
    ).first()
    if preferred is not None:
        return preferred

    candidates = list(
        Location.objects.filter(
            branch=branch,
            location_type=Location.LocationType.RETURNS,
            is_active=True,
        )
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValidationError({"detail": "Returns Area is not configured for this branch."})
    raise ValidationError({"detail": "More than one active returns location exists for this branch."})


def recalculate_return_document_status(document):
    lines = list(document.lines.all())
    if not lines:
        document.status = ExternalReturnDocument.Status.OPEN
        document.completed_at = None
    else:
        accepted = sum((line.accepted_quantity for line in lines), Decimal("0"))
        rejected = sum((line.rejected_quantity for line in lines), Decimal("0"))
        on_hold = sum((line.on_hold_quantity for line in lines), Decimal("0"))
        remaining = sum((line.remaining_quantity for line in lines), Decimal("0"))
        if accepted == 0 and rejected == 0 and on_hold == 0:
            document.status = ExternalReturnDocument.Status.OPEN
            document.completed_at = None
        elif remaining == 0 and on_hold > 0:
            document.status = ExternalReturnDocument.Status.ON_HOLD
            document.completed_at = None
        elif remaining == 0 and on_hold == 0:
            document.status = ExternalReturnDocument.Status.COMPLETED
            document.completed_at = document.completed_at or timezone.now()
        else:
            document.status = ExternalReturnDocument.Status.IN_PROGRESS
            document.completed_at = None
    document.save(update_fields=["status", "completed_at", "updated_at"])
    return document


RETURN_ACTION_SOURCE_POOLS = {
    ReturnAction.ActionType.ACCEPT_REMAINING: ReturnAction.SourcePool.REMAINING,
    ReturnAction.ActionType.REJECT_REMAINING: ReturnAction.SourcePool.REMAINING,
    ReturnAction.ActionType.PUT_ON_HOLD: ReturnAction.SourcePool.REMAINING,
    ReturnAction.ActionType.ACCEPT_ON_HOLD: ReturnAction.SourcePool.ON_HOLD,
    ReturnAction.ActionType.REJECT_ON_HOLD: ReturnAction.SourcePool.ON_HOLD,
}


def apply_return_action(*, user, document_id, line_id, action_type, quantity, note, client_operation_id):
    operation_id = validate_operation_id(client_operation_id)
    quantity = parse_positive_quantity(quantity)
    note = str(note or "").strip()
    if action_type not in RETURN_ACTION_SOURCE_POOLS:
        raise ValidationError({"action_type": "Unsupported return action type."})
    if action_type == ReturnAction.ActionType.PUT_ON_HOLD and not note:
        raise ValidationError({"note": "A note is required when putting return quantity on hold."})

    fingerprint_payload = {
        "user": user.id,
        "document": document_id,
        "line": line_id,
        "action_type": action_type,
        "source_pool": RETURN_ACTION_SOURCE_POOLS[action_type],
        "quantity": str(quantity),
        "note": note,
    }
    fingerprint = payload_fingerprint(fingerprint_payload)

    existing = ReturnAction.objects.select_related("document", "line").filter(client_operation_id=operation_id).first()
    if existing is not None:
        if existing.payload_fingerprint != fingerprint:
            raise Conflict("client_operation_id has already been used for a different return action.")
        return existing, True

    with transaction.atomic():
        document = (
            ExternalReturnDocument.objects.select_for_update()
            .select_related("branch")
            .get(pk=document_id)
        )
        require_branch_access(user, document.branch)
        if document.status == ExternalReturnDocument.Status.COMPLETED:
            raise ValidationError({"detail": "Completed return documents are read-only."})

        line = (
            ExternalReturnDocumentLine.objects.select_for_update()
            .select_related("document", "product")
            .get(pk=line_id, document=document)
        )
        source_pool = RETURN_ACTION_SOURCE_POOLS[action_type]
        available = line.remaining_quantity if source_pool == ReturnAction.SourcePool.REMAINING else line.on_hold_quantity
        if quantity > available:
            raise ValidationError({"quantity": "Quantity exceeds the selected available quantity."})

        returns_area = None
        movement = None
        inventory_before = None
        inventory_after = None
        if action_type in {ReturnAction.ActionType.ACCEPT_REMAINING, ReturnAction.ActionType.ACCEPT_ON_HOLD}:
            returns_area = resolve_returns_area(document.branch)
            inventory_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=document.branch,
                location=returns_area,
                product=line.product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            inventory_before = inventory_item.quantity_on_hand
            inventory_after = inventory_before + quantity
            inventory_item.quantity_on_hand = inventory_after
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])
            movement = StockMovement.objects.create(
                branch=document.branch,
                product=line.product,
                inventory_item=inventory_item,
                destination_location=returns_area,
                movement_type=StockMovement.MovementType.RETURN_RECEIPT,
                quantity=quantity,
                quantity_before=inventory_before,
                quantity_after=inventory_after,
                reference=document.external_reference,
                performed_by=user,
            )

        if action_type == ReturnAction.ActionType.ACCEPT_REMAINING:
            line.accepted_quantity += quantity
        elif action_type == ReturnAction.ActionType.REJECT_REMAINING:
            line.rejected_quantity += quantity
        elif action_type == ReturnAction.ActionType.PUT_ON_HOLD:
            line.on_hold_quantity += quantity
        elif action_type == ReturnAction.ActionType.ACCEPT_ON_HOLD:
            line.accepted_quantity += quantity
            line.on_hold_quantity -= quantity
        elif action_type == ReturnAction.ActionType.REJECT_ON_HOLD:
            line.rejected_quantity += quantity
            line.on_hold_quantity -= quantity

        if line.remaining_quantity < 0 or line.on_hold_quantity < 0:
            raise ValidationError({"detail": "Return line quantity accounting would become invalid."})
        line.save(update_fields=["accepted_quantity", "rejected_quantity", "on_hold_quantity", "updated_at"])

        action = ReturnAction.objects.create(
            document=document,
            line=line,
            branch=document.branch,
            product=line.product,
            action_type=action_type,
            source_pool=source_pool,
            quantity=quantity,
            performed_by=user,
            note=note,
            client_operation_id=operation_id,
            payload_fingerprint=fingerprint,
            inventory_quantity_before=inventory_before,
            inventory_quantity_after=inventory_after,
            stock_movement=movement,
        )
        if movement is not None:
            movement.external_return_action = action
            movement.save(update_fields=["external_return_action", "updated_at"])

        recalculate_return_document_status(document)
        event_type = f"return_{action_type}"
        AuditLog.objects.create(
            actor=user,
            action_type=AuditLog.ActionType.UPDATE,
            event_type=event_type,
            branch=document.branch,
            product=line.product,
            quantity=quantity,
            destination_location=returns_area,
            reference=document.external_reference,
            entity_name="ExternalReturnDocument",
            entity_id=str(document.id),
            message=(
                f"{user.username} recorded {action.get_action_type_display()} for "
                f"{quantity} of {line.product.sku} on return document {document.external_reference}."
            ),
        )
        if document.status == ExternalReturnDocument.Status.COMPLETED:
            AuditLog.objects.create(
                actor=user,
                action_type=AuditLog.ActionType.STATUS_CHANGE,
                event_type="return_document_completed",
                branch=document.branch,
                reference=document.external_reference,
                entity_name="ExternalReturnDocument",
                entity_id=str(document.id),
                message=f"Return document {document.external_reference} completed.",
            )
        return action, False


def corrected_quantity_for_order_line(order_line, exclude_correction_id=None):
    queryset = SalesCorrectionLine.objects.filter(
        source_order_line=order_line,
        correction__status=SalesCorrection.Status.COMPLETED,
    )
    if exclude_correction_id is not None:
        queryset = queryset.exclude(correction_id=exclude_correction_id)
    return queryset.aggregate(total=Sum("corrected_quantity"))["total"] or Decimal("0")


def remaining_correctable_quantity(order_line, exclude_correction_id=None):
    return order_line.quantity_ordered - corrected_quantity_for_order_line(order_line, exclude_correction_id)


def confirm_sales_correction(*, user, correction_id, client_operation_id):
    operation_id = validate_operation_id(client_operation_id)

    with transaction.atomic():
        correction = (
            SalesCorrection.objects.select_for_update()
            .select_related("branch")
            .prefetch_related("lines")
            .get(pk=correction_id)
        )
        require_branch_access(user, correction.branch)

        lines = list(
            correction.lines.select_for_update()
            .select_related("product", "source_order", "source_order_line")
            .order_by("source_order_line_id", "id")
        )
        payload = {
            "user": user.id,
            "branch": correction.branch_id,
            "correction": correction.id,
            "lines": [(line.id, line.source_order_line_id, str(line.corrected_quantity)) for line in lines],
        }
        fingerprint = payload_fingerprint(payload)

        if correction.confirmation_client_operation_id:
            if correction.confirmation_client_operation_id == operation_id and correction.confirmation_payload_fingerprint == fingerprint:
                return correction, True
            raise Conflict("This sales correction has already been confirmed with a different payload.")

        existing = SalesCorrection.objects.filter(confirmation_client_operation_id=operation_id).first()
        if existing is not None:
            raise Conflict("client_operation_id has already been used for another sales correction.")

        if correction.status != SalesCorrection.Status.DRAFT:
            raise ValidationError({"detail": "Only draft sales corrections can be confirmed."})
        if not lines:
            raise ValidationError({"detail": "A sales correction requires at least one line."})

        source_line_ids = [line.source_order_line_id for line in lines]
        locked_source_lines = {
            line.id: line
            for line in ExternalOrderLineProxy.objects_for_update(source_line_ids)
        }
        returns_area = resolve_returns_area(correction.branch)
        now = timezone.now()

        for line in lines:
            if line.corrected_quantity <= 0:
                raise ValidationError({"quantity": "Correction line quantity must be positive."})
            source_line = locked_source_lines[line.source_order_line_id]
            if source_line.order.branch_id != correction.branch_id:
                raise ValidationError({"detail": "Source sales line belongs to another branch."})
            if source_line.order.status != source_line.order.Status.COMPLETED:
                raise ValidationError({"detail": "Only completed sales can be corrected."})
            remaining = remaining_correctable_quantity(source_line, exclude_correction_id=correction.id)
            if line.corrected_quantity > remaining:
                raise ValidationError({"quantity": "Correction quantity exceeds remaining correctable quantity."})

            inventory_item, _ = InventoryItem.objects.select_for_update().get_or_create(
                branch=correction.branch,
                location=returns_area,
                product=line.product,
                defaults={"quantity_on_hand": Decimal("0"), "quantity_reserved": Decimal("0")},
            )
            before = inventory_item.quantity_on_hand
            after = before + line.corrected_quantity
            inventory_item.quantity_on_hand = after
            inventory_item.save(update_fields=["quantity_on_hand", "updated_at"])
            movement = StockMovement.objects.create(
                branch=correction.branch,
                product=line.product,
                inventory_item=inventory_item,
                destination_location=returns_area,
                movement_type=StockMovement.MovementType.SALES_CORRECTION_RECEIPT,
                quantity=line.corrected_quantity,
                quantity_before=before,
                quantity_after=after,
                reference=correction.reference,
                performed_by=user,
                sales_correction_line=line,
            )
            line.returns_location = returns_area
            line.inventory_quantity_before = before
            line.inventory_quantity_after = after
            line.stock_movement = movement
            line.save(
                update_fields=[
                    "returns_location",
                    "inventory_quantity_before",
                    "inventory_quantity_after",
                    "stock_movement",
                    "updated_at",
                ]
            )

        correction.status = SalesCorrection.Status.COMPLETED
        correction.confirmed_by = user
        correction.confirmed_at = now
        correction.confirmation_client_operation_id = operation_id
        correction.confirmation_payload_fingerprint = fingerprint
        correction.save(
            update_fields=[
                "status",
                "confirmed_by",
                "confirmed_at",
                "confirmation_client_operation_id",
                "confirmation_payload_fingerprint",
                "updated_at",
            ]
        )
        AuditLog.objects.create(
            actor=user,
            action_type=AuditLog.ActionType.STATUS_CHANGE,
            event_type="sales_correction_confirmed",
            branch=correction.branch,
            reference=correction.reference,
            entity_name="SalesCorrection",
            entity_id=str(correction.id),
            message=f"{user.username} confirmed sales correction {correction.reference}.",
        )
        for line in lines:
            AuditLog.objects.create(
                actor=user,
                action_type=AuditLog.ActionType.UPDATE,
                event_type="sales_correction_line_posted",
                branch=correction.branch,
                product=line.product,
                quantity=line.corrected_quantity,
                destination_location=returns_area,
                order=line.source_order,
                reference=correction.reference,
                entity_name="SalesCorrectionLine",
                entity_id=str(line.id),
                message=(
                    f"{user.username} posted {line.corrected_quantity} of {line.product.sku} "
                    f"from sales document {line.source_sales_document_reference} to Returns Area."
                ),
            )
        return correction, False


class ExternalOrderLineProxy:
    @staticmethod
    def objects_for_update(ids):
        from operations.models import OrderLine

        return (
            OrderLine.objects.select_for_update()
            .select_related("order", "product")
            .filter(id__in=ids)
            .order_by("id")
        )
