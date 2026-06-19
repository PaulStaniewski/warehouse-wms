from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, F, Q
from django.db import models
from django.utils import timezone

from warehouse.models import Branch, InventoryItem, Location, Product

PRIORITY_LOCK_WINDOW_MINUTES = 15


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DeliveryRoute(TimestampedModel):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="delivery_routes")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["branch__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="unique_delivery_route_code_per_branch"),
        ]
        indexes = [
            models.Index(fields=["branch", "code"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.branch.code} / {self.code} - {self.name}"


class RouteRun(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        SYNCING = "syncing", "Syncing"
        PICKING = "picking", "Picking"
        READY_TO_CLOSE = "ready_to_close", "Ready to close"
        CLOSED = "closed", "Closed"
        DISPATCHED = "dispatched", "Dispatched"
        CANCELLED = "cancelled", "Cancelled"

    route = models.ForeignKey(DeliveryRoute, on_delete=models.PROTECT, related_name="runs")
    service_date = models.DateField()
    run_number = models.PositiveIntegerField()
    order_cutoff_time = models.TimeField()
    sync_time = models.TimeField()
    departure_time = models.TimeField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["service_date", "route__code", "run_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["route", "service_date", "run_number"],
                name="unique_route_run_per_service_date",
            ),
        ]
        indexes = [
            models.Index(fields=["route", "service_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["service_date", "departure_time"]),
        ]

    def __str__(self) -> str:
        return f"{self.route.code} / run {self.run_number} / {self.departure_time}"

    @property
    def orders_count(self) -> int:
        return self.orders.count()

    @property
    def order_lines_count(self) -> int:
        return OrderLine.objects.filter(order__route_run=self).count()

    @property
    def picked_lines_count(self) -> int:
        return OrderLine.objects.filter(
            order__route_run=self,
            quantity_picked__gte=F("quantity_ordered"),
        ).count()

    @property
    def pending_lines_count(self) -> int:
        return OrderLine.objects.filter(
            order__route_run=self,
            quantity_picked__lt=F("quantity_ordered"),
        ).count()

    @property
    def has_pending_work(self) -> bool:
        return self.pending_lines_count > 0

    @property
    def is_urgent(self) -> bool:
        if self.status in {self.Status.CLOSED, self.Status.DISPATCHED, self.Status.CANCELLED}:
            return False
        if not self.has_pending_work:
            return False

        now = timezone.localtime()
        departure_at = timezone.make_aware(
            datetime.combine(self.service_date, self.departure_time),
            timezone.get_current_timezone(),
        )
        return now <= departure_at <= now + timedelta(minutes=PRIORITY_LOCK_WINDOW_MINUTES)

    @property
    def is_selectable(self) -> bool:
        if self.status in {self.Status.CLOSED, self.Status.DISPATCHED, self.Status.CANCELLED}:
            return False
        if not self.has_pending_work:
            return False

        urgent_exists = RouteRun.objects.exclude(
            status__in=[self.Status.CLOSED, self.Status.DISPATCHED, self.Status.CANCELLED],
        ).annotate(
            pending_lines=Count(
                "orders__lines",
                filter=Q(orders__lines__quantity_picked__lt=F("orders__lines__quantity_ordered")),
            ),
        ).filter(pending_lines__gt=0)

        return not any(run.is_urgent for run in urgent_exists) or self.is_urgent


class Order(TimestampedModel):
    class Status(models.TextChoices):
        IMPORTED = "imported", "Imported"
        ALLOCATED = "allocated", "Allocated"
        PICKING = "picking", "Picking"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="orders")
    route_run = models.ForeignKey(
        RouteRun,
        on_delete=models.SET_NULL,
        related_name="orders",
        blank=True,
        null=True,
    )
    external_reference = models.CharField(max_length=128, unique=True)
    customer_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.IMPORTED)
    requested_ship_date = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["external_reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["branch", "status"]),
        ]

    def __str__(self) -> str:
        return self.external_reference


class OrderLine(TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_lines")
    line_number = models.PositiveIntegerField()
    quantity_ordered = models.DecimalField(max_digits=12, decimal_places=3)
    quantity_picked = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["order", "line_number"]
        constraints = [
            models.UniqueConstraint(fields=["order", "line_number"], name="unique_order_line_number"),
            models.CheckConstraint(check=models.Q(quantity_ordered__gt=0), name="order_line_quantity_positive"),
            models.CheckConstraint(check=models.Q(quantity_picked__gte=0), name="order_line_picked_non_negative"),
        ]
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"{self.order.external_reference} / {self.line_number}"


class ReturnBatch(TimestampedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        VERIFIED = "verified", "Verified"
        PUT_AWAY = "put_away", "Put away"
        CLOSED = "closed", "Closed"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="return_batches")
    reference = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.RECEIVED)
    received_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["branch", "status"]),
        ]

    def __str__(self) -> str:
        return self.reference


class ReturnLine(TimestampedModel):
    class Condition(models.TextChoices):
        SELLABLE = "sellable", "Sellable"
        DAMAGED = "damaged", "Damaged"
        QUARANTINE = "quarantine", "Quarantine"

    return_batch = models.ForeignKey(ReturnBatch, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="return_lines")
    line_number = models.PositiveIntegerField()
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    condition = models.CharField(max_length=32, choices=Condition.choices, default=Condition.SELLABLE)

    class Meta:
        ordering = ["return_batch", "line_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["return_batch", "line_number"],
                name="unique_return_line_number",
            ),
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="return_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["return_batch"]),
            models.Index(fields=["product"]),
            models.Index(fields=["condition"]),
        ]

    def __str__(self) -> str:
        return f"{self.return_batch.reference} / {self.line_number}"


class PickingTask(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="picking_tasks")
    order_line = models.ForeignKey(OrderLine, on_delete=models.CASCADE, related_name="picking_tasks")
    source_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="picking_tasks")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="picking_tasks",
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)
    quantity_to_pick = models.DecimalField(max_digits=12, decimal_places=3)
    quantity_picked = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["status", "created_at"]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["source_location"]),
            models.Index(fields=["assigned_to"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity_to_pick__gt=0), name="picking_quantity_positive"),
            models.CheckConstraint(check=models.Q(quantity_picked__gte=0), name="picking_picked_non_negative"),
        ]

    def __str__(self) -> str:
        return f"Pick {self.order_line.product.sku} for {self.order_line.order.external_reference}"


class StockMovement(TimestampedModel):
    class MovementType(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        PICK = "pick", "Pick"
        RETURN = "return", "Return"
        ADJUSTMENT = "adjustment", "Adjustment"
        TRANSFER = "transfer", "Transfer"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="stock_movements")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_movements")
    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
        blank=True,
        null=True,
    )
    source_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="outgoing_stock_movements",
        blank=True,
        null=True,
    )
    destination_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="incoming_stock_movements",
        blank=True,
        null=True,
    )
    movement_type = models.CharField(max_length=32, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    reference = models.CharField(max_length=128, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "movement_type"]),
            models.Index(fields=["product"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="stock_movement_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.movement_type} {self.product.sku} x {self.quantity}"


class AuditLog(models.Model):
    class ActionType(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        STATUS_CHANGE = "status_change", "Status change"
        SYSTEM = "system", "System"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    action_type = models.CharField(max_length=32, choices=ActionType.choices)
    entity_name = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=64, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action_type"]),
            models.Index(fields=["entity_name", "entity_id"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action_type} {self.entity_name} {self.entity_id}".strip()
