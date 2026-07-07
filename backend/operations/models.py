from datetime import datetime, timedelta
import uuid

from django.conf import settings
from django.db.models import Count, F, Q
from django.db import models
from django.utils import timezone

from warehouse.models import Branch, InventoryItem, Location, Product

PRIORITY_LOCK_WINDOW_MINUTES = 15


def generate_customer_label_scan_code():
    return f"CL-{uuid.uuid4().hex[:10].upper()}"


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
    ready_at = models.DateTimeField(blank=True, null=True)
    documents_printed_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

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
            models.Index(fields=["closed_at"]),
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
        PICKED = "picked", "Picked"
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
    quantity_prepared = models.DecimalField(max_digits=12, decimal_places=3, default=0)

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
            models.CheckConstraint(check=models.Q(quantity_prepared__gte=0), name="picking_prepared_non_negative"),
        ]

    def __str__(self) -> str:
        return f"Pick {self.order_line.product.sku} for {self.order_line.order.external_reference}"


class PickingJob(TimestampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        IN_PROGRESS = "in_progress", "In progress"
        PICKED = "picked", "Picked"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Mode(models.TextChoices):
        MERGED = "merged", "Merged"
        SEPARATE = "separate", "Separate"

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.AVAILABLE)
    mode = models.CharField(max_length=32, choices=Mode.choices)
    route_runs = models.ManyToManyField(RouteRun, related_name="picking_jobs")
    tasks = models.ManyToManyField(PickingTask, through="PickingJobTask", related_name="picking_jobs")
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["status", "created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["mode"]),
            models.Index(fields=["started_at"]),
        ]

    def __str__(self) -> str:
        return f"Picking job {self.id} / {self.mode} / {self.status}"


class PickingJobTask(TimestampedModel):
    picking_job = models.ForeignKey(PickingJob, on_delete=models.CASCADE, related_name="job_tasks")
    picking_task = models.OneToOneField(PickingTask, on_delete=models.PROTECT, related_name="job_task")

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["picking_job", "picking_task"], name="unique_task_per_picking_job"),
        ]
        indexes = [
            models.Index(fields=["picking_job"]),
        ]

    def __str__(self) -> str:
        return f"Job {self.picking_job_id} / task {self.picking_task_id}"


class ScannerCart(TimestampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        IN_USE = "in_use", "In use"

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.AVAILABLE)

    class Meta:
        ordering = ["code"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.code


class ScannerSession(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    cart = models.ForeignKey(ScannerCart, on_delete=models.PROTECT, related_name="sessions")
    worker_code = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["worker_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.cart.code} / {self.status}"


class CartWorkSession(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CONTROL = "control", "Control"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    cart = models.ForeignKey(ScannerCart, on_delete=models.PROTECT, related_name="work_sessions")
    picking_job = models.ForeignKey(PickingJob, on_delete=models.PROTECT, related_name="cart_work_sessions")
    confirmed_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="confirmed_cart_work_sessions",
        blank=True,
        null=True,
    )
    scanner_session = models.OneToOneField(
        ScannerSession,
        on_delete=models.PROTECT,
        related_name="cart_work_session",
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart"],
                condition=models.Q(status__in=["active", "control"]),
                name="unique_active_cart_work_per_cart",
            ),
            models.UniqueConstraint(
                fields=["picking_job"],
                condition=models.Q(status__in=["active", "control"]),
                name="unique_active_cart_work_per_job",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.cart.code} / job {self.picking_job_id} / {self.status}"


class CartPickedItem(TimestampedModel):
    session = models.ForeignKey(ScannerSession, on_delete=models.CASCADE, related_name="picked_items")
    cart_work_session = models.ForeignKey(
        CartWorkSession,
        on_delete=models.CASCADE,
        related_name="picked_items",
        blank=True,
        null=True,
    )
    cart = models.ForeignKey(ScannerCart, on_delete=models.PROTECT, related_name="picked_items")
    route_run = models.ForeignKey(RouteRun, on_delete=models.PROTECT, related_name="cart_items")
    picking_task = models.ForeignKey(PickingTask, on_delete=models.CASCADE, related_name="cart_items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="cart_items")
    quantity_picked = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantity_prepared = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["session", "picking_task"], name="unique_cart_item_per_session_task"),
            models.CheckConstraint(check=models.Q(quantity_picked__gte=0), name="cart_item_picked_non_negative"),
            models.CheckConstraint(check=models.Q(quantity_prepared__gte=0), name="cart_item_prepared_non_negative"),
        ]
        indexes = [
            models.Index(fields=["session", "product"]),
            models.Index(fields=["cart_work_session", "product"]),
            models.Index(fields=["cart"]),
            models.Index(fields=["route_run"]),
        ]

    def __str__(self) -> str:
        return f"{self.cart.code} / {self.product.sku}"


class ScannerCustomerLabel(TimestampedModel):
    session = models.ForeignKey(ScannerSession, on_delete=models.CASCADE, related_name="customer_labels")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="scanner_labels")
    scan_code = models.CharField(max_length=32, unique=True, editable=False, default=generate_customer_label_scan_code)
    printer_code = models.CharField(max_length=64)
    printed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-printed_at"]
        constraints = [
            models.UniqueConstraint(fields=["session", "order"], name="unique_scanner_label_per_session_order"),
        ]
        indexes = [
            models.Index(fields=["scan_code"]),
            models.Index(fields=["printer_code"]),
            models.Index(fields=["printed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.scan_code} / {self.order.external_reference}"

    def save(self, *args, **kwargs):
        if self.pk:
            existing = ScannerCustomerLabel.objects.filter(pk=self.pk).only("scan_code").first()
            if existing and existing.scan_code:
                self.scan_code = existing.scan_code
        if not self.scan_code:
            self.scan_code = generate_customer_label_scan_code()
        super().save(*args, **kwargs)


class InterBranchTransfer(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RELEASED = "released", "Released"
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVING = "receiving", "Receiving"
        RECEIVED = "received", "Received"
        CLOSED_WITH_DISCREPANCY = "closed_with_discrepancy", "Closed with discrepancy"
        CANCELLED = "cancelled", "Cancelled"

    reference = models.CharField(max_length=128, unique=True)
    source_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="outgoing_transfers")
    destination_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="incoming_transfers")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    released_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["destination_branch", "status"]),
        ]

    def __str__(self) -> str:
        return self.reference


class TransferPallet(TimestampedModel):
    class Status(models.TextChoices):
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVING = "receiving", "Receiving"
        RECEIVED = "received", "Received"
        CLOSED_WITH_DISCREPANCY = "closed_with_discrepancy", "Closed with discrepancy"
        CANCELLED = "cancelled", "Cancelled"

    transfer = models.ForeignKey(InterBranchTransfer, on_delete=models.PROTECT, related_name="pallets")
    scan_code = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.IN_TRANSIT)
    released_at = models.DateTimeField(blank=True, null=True)
    receiving_started_at = models.DateTimeField(blank=True, null=True)
    received_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["scan_code"]
        indexes = [
            models.Index(fields=["scan_code"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.scan_code


class TransferPalletItem(TimestampedModel):
    pallet = models.ForeignKey(TransferPallet, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_pallet_items")
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["product__sku"]
        constraints = [
            models.UniqueConstraint(fields=["pallet", "product"], name="unique_product_per_transfer_pallet"),
            models.CheckConstraint(check=models.Q(expected_quantity__gt=0), name="transfer_pallet_expected_positive"),
            models.CheckConstraint(check=models.Q(received_quantity__gte=0), name="transfer_pallet_received_non_negative"),
        ]
        indexes = [
            models.Index(fields=["pallet"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"{self.pallet.scan_code} / {self.product.sku}"


class PalletReceivingSession(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    pallet = models.ForeignKey(TransferPallet, on_delete=models.PROTECT, related_name="receiving_sessions")
    current_pallet_item = models.ForeignKey(
        TransferPalletItem,
        on_delete=models.PROTECT,
        related_name="pending_receiving_sessions",
        blank=True,
        null=True,
    )
    pending_quantity = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    worker_code = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["pallet"],
                condition=models.Q(status="active"),
                name="unique_active_receiving_session_per_pallet",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.pallet.scan_code} / {self.status}"


class PalletReceivingScan(TimestampedModel):
    receiving_session = models.ForeignKey(PalletReceivingSession, on_delete=models.PROTECT, related_name="scans")
    pallet = models.ForeignKey(TransferPallet, on_delete=models.PROTECT, related_name="receiving_scans")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pallet_receiving_scans")
    destination_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="pallet_receiving_scans")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    worker_code = models.CharField(max_length=64, blank=True)
    scanned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-scanned_at"]
        indexes = [
            models.Index(fields=["pallet"]),
            models.Index(fields=["product"]),
            models.Index(fields=["destination_location"]),
            models.Index(fields=["scanned_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.pallet.scan_code} / {self.product.sku} / {self.quantity}"


class TransferDiscrepancy(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"
        CONFIRMED_SHORTAGE = "confirmed_shortage", "Confirmed shortage"

    reference = models.CharField(max_length=64, unique=True)
    pallet = models.OneToOneField(TransferPallet, on_delete=models.PROTECT, related_name="discrepancy")
    transfer = models.ForeignKey(InterBranchTransfer, on_delete=models.PROTECT, related_name="discrepancies")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)
    created_by_worker_code = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.reference


class TransferDiscrepancyItem(TimestampedModel):
    class DiscrepancyType(models.TextChoices):
        SHORTAGE = "shortage", "Shortage"
        SURPLUS = "surplus", "Surplus"
        WRONG_LOCATION = "wrong_location", "Wrong location"

    discrepancy = models.ForeignKey(TransferDiscrepancy, on_delete=models.CASCADE, related_name="items")
    pallet_item = models.ForeignKey(TransferPalletItem, on_delete=models.PROTECT, related_name="discrepancy_items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_discrepancy_items")
    discrepancy_type = models.CharField(max_length=32, choices=DiscrepancyType.choices)
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    difference_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    discrepancy_quantity = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        ordering = ["product__sku"]
        constraints = [
            models.UniqueConstraint(fields=["discrepancy", "pallet_item"], name="unique_discrepancy_item_per_pallet_item"),
            models.CheckConstraint(check=models.Q(discrepancy_quantity__gt=0), name="transfer_discrepancy_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["discrepancy"]),
            models.Index(fields=["product"]),
            models.Index(fields=["discrepancy_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.discrepancy.reference} / {self.product.sku}"


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
