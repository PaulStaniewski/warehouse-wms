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


class BranchDispatchPolicy(TimestampedModel):
    branch = models.OneToOneField(Branch, on_delete=models.PROTECT, related_name="dispatch_policy")
    max_routes_per_wave = models.PositiveIntegerField(default=3)
    min_wave_gap_minutes = models.PositiveIntegerField(default=10)

    class Meta:
        verbose_name_plural = "branch dispatch policies"

    def __str__(self) -> str:
        return f"{self.branch.code} dispatch policy"


class RouteRoundSchedule(TimestampedModel):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    route = models.ForeignKey(DeliveryRoute, on_delete=models.PROTECT, related_name="round_schedules")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    round_number = models.PositiveIntegerField()
    cutoff_time = models.TimeField()
    departure_time = models.TimeField()
    dispatch_wave = models.CharField(max_length=32)
    operational_label = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["route__branch__code", "weekday", "departure_time", "route__code", "round_number"]
        constraints = [
            models.UniqueConstraint(fields=["route", "weekday", "round_number"], name="unique_route_round_schedule"),
            models.CheckConstraint(check=models.Q(cutoff_time__lt=models.F("departure_time")), name="route_round_cutoff_before_departure"),
        ]
        indexes = [
            models.Index(fields=["route", "weekday", "is_active"]),
            models.Index(fields=["weekday", "dispatch_wave"]),
        ]

    def __str__(self) -> str:
        return f"{self.route.code} / {self.get_weekday_display()} / round {self.round_number}"


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
    schedule = models.ForeignKey(RouteRoundSchedule, on_delete=models.PROTECT, related_name="route_runs", blank=True, null=True)
    service_date = models.DateField()
    run_number = models.PositiveIntegerField()
    order_cutoff_time = models.TimeField()
    sync_time = models.TimeField()
    departure_time = models.TimeField()
    cutoff_at = models.DateTimeField(blank=True, null=True)
    planned_departure_at = models.DateTimeField(blank=True, null=True)
    dispatch_wave = models.CharField(max_length=32, blank=True)
    operational_identifier = models.CharField(max_length=96, blank=True)
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
            models.Index(fields=["operational_identifier"]),
            models.Index(fields=["dispatch_wave"]),
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


class RouteRunOverrideHistory(TimestampedModel):
    route_run = models.ForeignKey(RouteRun, on_delete=models.CASCADE, related_name="override_history")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="route_run_overrides", blank=True, null=True)
    previous_cutoff_at = models.DateTimeField(blank=True, null=True)
    new_cutoff_at = models.DateTimeField(blank=True, null=True)
    previous_planned_departure_at = models.DateTimeField(blank=True, null=True)
    new_planned_departure_at = models.DateTimeField(blank=True, null=True)
    previous_dispatch_wave = models.CharField(max_length=32, blank=True)
    new_dispatch_wave = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["route_run", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.route_run_id} override at {self.created_at}"


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
    customer_alias = models.CharField(max_length=128, blank=True)
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


class Shipment(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_ACTIVATION = "pending_activation", "Pending activation"
        ACTIVE = "active", "Active"
        PICKING = "picking", "Picking"
        PICKED = "picked", "Picked"
        CONTROLLED = "controlled", "Controlled"
        PREPARED = "prepared", "Prepared"
        DOCUMENTS_POSTED = "documents_posted", "Documents posted"
        READY_FOR_DISPATCH = "ready_for_dispatch", "Ready for dispatch"
        DISPATCHED = "dispatched", "Dispatched"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        EXCEPTION = "exception", "Exception"

    class ShipmentType(models.TextChoices):
        CUSTOMER_DELIVERY = "customer_delivery", "Customer delivery"
        BRANCH_COLLECTION = "branch_collection", "Branch collection"
        COURIER_DISPATCH = "courier_dispatch", "Courier dispatch"
        INTER_BRANCH = "inter_branch", "Inter-branch transfer"

    class DocumentStatus(models.TextChoices):
        NOT_AVAILABLE = "not_available", "Not available"
        AVAILABLE = "available", "Available"
        PREVIEWED = "previewed", "Previewed"
        PRINTED = "printed", "Printed"
        POSTED = "posted", "Posted"
        REQUIRES_REFRESH = "requires_refresh", "Requires refresh"

    reference = models.CharField(max_length=128, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="shipments")
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="shipment")
    route_run = models.ForeignKey(
        RouteRun,
        on_delete=models.PROTECT,
        related_name="shipments",
        blank=True,
        null=True,
    )
    inter_branch_transfer = models.ForeignKey(
        "InterBranchTransfer",
        on_delete=models.PROTECT,
        related_name="shipments",
        blank=True,
        null=True,
    )
    shipment_type = models.CharField(max_length=32, choices=ShipmentType.choices, default=ShipmentType.CUSTOMER_DELIVERY)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_ACTIVATION)
    document_status = models.CharField(max_length=32, choices=DocumentStatus.choices, default=DocumentStatus.NOT_AVAILABLE)
    source_system = models.CharField(max_length=64, default="AX")
    external_reference = models.CharField(max_length=128)
    external_order_reference = models.CharField(max_length=128, blank=True)
    external_status = models.CharField(max_length=64, blank=True)
    external_customer_account = models.CharField(max_length=128, blank=True)
    external_delivery_reference = models.CharField(max_length=128, blank=True)
    external_notes = models.TextField(blank=True)
    external_created_at = models.DateTimeField(blank=True, null=True)
    external_updated_at = models.DateTimeField(blank=True, null=True)
    customer_name = models.CharField(max_length=255, blank=True)
    customer_alias = models.CharField(max_length=128, blank=True)
    recipient_account = models.CharField(max_length=128, blank=True)
    delivery_name = models.CharField(max_length=255, blank=True)
    delivery_address = models.TextField(blank=True)
    delivery_date = models.DateField(blank=True, null=True)
    payment_method = models.CharField(max_length=64, blank=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="activated_shipments",
        blank=True,
        null=True,
    )
    picking_lists_posted_at = models.DateTimeField(blank=True, null=True)
    picking_lists_posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="posted_picking_list_shipments",
        blank=True,
        null=True,
    )
    prepared_at = models.DateTimeField(blank=True, null=True)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="prepared_shipments",
        blank=True,
        null=True,
    )
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="cancelled_shipments",
        blank=True,
        null=True,
    )
    cancellation_reason = models.TextField(blank=True)
    documents_printed_at = models.DateTimeField(blank=True, null=True)
    documents_printed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="printed_shipment_documents",
        blank=True,
        null=True,
    )
    document_print_count = models.PositiveIntegerField(default=0)
    documents_posted_at = models.DateTimeField(blank=True, null=True)
    documents_posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="posted_shipment_documents",
        blank=True,
        null=True,
    )
    picking_route_confirmed_at = models.DateTimeField(blank=True, null=True)
    picking_route_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="confirmed_shipment_picking_routes",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["source_system", "external_reference"], name="unique_shipment_external_reference_per_source"),
        ]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["branch", "delivery_date"]),
            models.Index(fields=["route_run", "status"]),
            models.Index(fields=["external_reference"]),
            models.Index(fields=["customer_alias"]),
            models.Index(fields=["document_status"]),
        ]

    def __str__(self) -> str:
        return self.reference


class ShipmentLine(TimestampedModel):
    class ServiceStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        PICKING = "picking", "Picking"
        PICKED = "picked", "Picked"
        CONTROLLED = "controlled", "Controlled"
        PREPARED = "prepared", "Prepared"
        COMPLETED = "completed", "Completed"
        SHORTAGE = "shortage", "Shortage"
        CANCELLED = "cancelled", "Cancelled"

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines")
    order_line = models.OneToOneField(OrderLine, on_delete=models.PROTECT, related_name="shipment_line")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="shipment_lines")
    line_number = models.PositiveIntegerField()
    external_line_reference = models.CharField(max_length=128, blank=True)
    ordered_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    cancelled_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    delivery_date = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["shipment", "line_number"]
        constraints = [
            models.UniqueConstraint(fields=["shipment", "line_number"], name="unique_shipment_line_number"),
            models.CheckConstraint(check=models.Q(ordered_quantity__gt=0), name="shipment_line_ordered_quantity_positive"),
            models.CheckConstraint(check=models.Q(cancelled_quantity__gte=0), name="shipment_line_cancelled_non_negative"),
        ]
        indexes = [
            models.Index(fields=["shipment"]),
            models.Index(fields=["product"]),
            models.Index(fields=["external_line_reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.shipment.reference} / {self.line_number}"


class ShipmentLineQuantityAdjustment(TimestampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="line_quantity_adjustments")
    shipment_line = models.ForeignKey(ShipmentLine, on_delete=models.CASCADE, related_name="quantity_adjustments")
    quantity_removed = models.DecimalField(max_digits=12, decimal_places=3)
    previous_effective_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    new_effective_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipment_line_quantity_adjustments",
        blank=True,
        null=True,
    )
    reason = models.TextField()
    client_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity_removed__gt=0), name="ship_line_adj_removed_positive"),
            models.CheckConstraint(check=models.Q(previous_effective_quantity__gte=0), name="ship_line_adj_prev_non_negative"),
            models.CheckConstraint(check=models.Q(new_effective_quantity__gte=0), name="ship_line_adj_new_non_negative"),
        ]
        indexes = [
            models.Index(fields=["shipment", "created_at"], name="ship_line_adj_shipment_idx"),
            models.Index(fields=["shipment_line", "created_at"], name="ship_line_adj_line_idx"),
            models.Index(fields=["client_operation_id"], name="ship_line_adj_client_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.shipment.reference} / line {self.shipment_line.line_number} / -{self.quantity_removed}"


class ShipmentRouteAssignment(TimestampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="route_assignments")
    previous_route_run = models.ForeignKey(
        RouteRun,
        on_delete=models.PROTECT,
        related_name="previous_shipment_assignments",
        blank=True,
        null=True,
    )
    new_route_run = models.ForeignKey(
        RouteRun,
        on_delete=models.PROTECT,
        related_name="new_shipment_assignments",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipment_route_assignments",
        blank=True,
        null=True,
    )
    reason = models.TextField(blank=True)
    previous_route_snapshot = models.CharField(max_length=255, blank=True)
    new_route_snapshot = models.CharField(max_length=255)
    client_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shipment", "created_at"]),
            models.Index(fields=["new_route_run"]),
            models.Index(fields=["client_operation_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.shipment.reference} -> {self.new_route_snapshot}"


class ShipmentStatusHistory(TimestampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="status_history")
    previous_status = models.CharField(max_length=32, blank=True)
    new_status = models.CharField(max_length=32)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipment_status_changes",
        blank=True,
        null=True,
    )
    reason = models.TextField()
    client_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shipment", "created_at"]),
            models.Index(fields=["new_status"]),
            models.Index(fields=["client_operation_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.shipment.reference}: {self.previous_status} -> {self.new_status}"


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


class ExternalReturnDocument(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="external_return_documents")
    external_reference = models.CharField(max_length=128)
    source_system = models.CharField(max_length=64, default="AX")
    customer_name = models.CharField(max_length=255)
    customer_alias = models.CharField(max_length=128, blank=True)
    source_sales_document_reference = models.CharField(max_length=128, blank=True)
    external_created_at = models.DateTimeField(blank=True, null=True)
    imported_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-imported_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "external_reference"],
                name="unique_external_return_document_reference_per_source",
            ),
        ]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["external_reference"]),
            models.Index(fields=["source_sales_document_reference"]),
            models.Index(fields=["customer_name"]),
            models.Index(fields=["imported_at"]),
        ]

    def __str__(self) -> str:
        return self.external_reference


class ExternalReturnDocumentLine(TimestampedModel):
    document = models.ForeignKey(ExternalReturnDocument, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="external_return_lines")
    line_number = models.PositiveIntegerField()
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    accepted_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    rejected_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    on_hold_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["document", "line_number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "line_number"], name="unique_external_return_line_number"),
            models.CheckConstraint(check=models.Q(expected_quantity__gt=0), name="external_return_expected_positive"),
            models.CheckConstraint(check=models.Q(accepted_quantity__gte=0), name="external_return_accepted_non_negative"),
            models.CheckConstraint(check=models.Q(rejected_quantity__gte=0), name="external_return_rejected_non_negative"),
            models.CheckConstraint(check=models.Q(on_hold_quantity__gte=0), name="external_return_on_hold_non_negative"),
        ]
        indexes = [
            models.Index(fields=["document"]),
            models.Index(fields=["product"]),
        ]

    @property
    def remaining_quantity(self):
        return self.expected_quantity - self.accepted_quantity - self.rejected_quantity - self.on_hold_quantity

    @property
    def is_completed(self) -> bool:
        return self.remaining_quantity == 0 and self.on_hold_quantity == 0

    def __str__(self) -> str:
        return f"{self.document.external_reference} / {self.line_number}"


class ReturnAction(TimestampedModel):
    class ActionType(models.TextChoices):
        ACCEPT_REMAINING = "accept_remaining", "Accept remaining quantity"
        REJECT_REMAINING = "reject_remaining", "Reject remaining quantity"
        PUT_ON_HOLD = "put_on_hold", "Put remaining quantity on hold"
        ACCEPT_ON_HOLD = "accept_on_hold", "Accept on-hold quantity"
        REJECT_ON_HOLD = "reject_on_hold", "Reject on-hold quantity"

    class SourcePool(models.TextChoices):
        REMAINING = "remaining", "Remaining"
        ON_HOLD = "on_hold", "On hold"

    document = models.ForeignKey(ExternalReturnDocument, on_delete=models.PROTECT, related_name="actions")
    line = models.ForeignKey(ExternalReturnDocumentLine, on_delete=models.PROTECT, related_name="actions")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="return_actions")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="return_actions")
    action_type = models.CharField(max_length=32, choices=ActionType.choices)
    source_pool = models.CharField(max_length=32, choices=SourcePool.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="return_actions",
        blank=True,
        null=True,
    )
    note = models.TextField(blank=True)
    client_operation_id = models.CharField(max_length=64, unique=True)
    payload_fingerprint = models.CharField(max_length=64)
    inventory_quantity_before = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    inventory_quantity_after = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    stock_movement = models.OneToOneField(
        "StockMovement",
        on_delete=models.SET_NULL,
        related_name="return_action",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["document", "created_at"]),
            models.Index(fields=["line", "created_at"]),
            models.Index(fields=["branch", "action_type"]),
            models.Index(fields=["product"]),
            models.Index(fields=["performed_by"]),
            models.Index(fields=["client_operation_id"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="return_action_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.document.external_reference} / {self.action_type} / {self.quantity}"


class SalesCorrection(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    reference = models.CharField(max_length=64, unique=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="sales_corrections")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_sales_corrections",
        blank=True,
        null=True,
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="confirmed_sales_corrections",
        blank=True,
        null=True,
    )
    confirmed_at = models.DateTimeField(blank=True, null=True)
    note = models.TextField(blank=True)
    confirmation_client_operation_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    confirmation_payload_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["confirmed_at"]),
            models.Index(fields=["created_by"]),
            models.Index(fields=["confirmed_by"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"SC-{self.id:06d}"
            super().save(update_fields=["reference", "updated_at"])

    def __str__(self) -> str:
        return self.reference or f"Sales correction {self.id}"


class SalesCorrectionLine(TimestampedModel):
    correction = models.ForeignKey(SalesCorrection, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="sales_correction_lines")
    source_order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="sales_correction_lines")
    source_order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name="sales_correction_lines")
    customer_name_snapshot = models.CharField(max_length=255)
    customer_alias_snapshot = models.CharField(max_length=128, blank=True)
    source_sales_document_reference = models.CharField(max_length=128)
    sold_quantity_snapshot = models.DecimalField(max_digits=12, decimal_places=3)
    corrected_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    returns_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="sales_correction_lines",
        blank=True,
        null=True,
    )
    stock_movement = models.OneToOneField(
        "StockMovement",
        on_delete=models.SET_NULL,
        related_name="posted_sales_correction_line",
        blank=True,
        null=True,
    )
    inventory_quantity_before = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    inventory_quantity_after = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)

    class Meta:
        ordering = ["correction", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["correction", "source_order_line"],
                name="unique_sales_correction_source_line_per_correction",
            ),
            models.CheckConstraint(check=models.Q(sold_quantity_snapshot__gt=0), name="sales_correction_sold_positive"),
            models.CheckConstraint(check=models.Q(corrected_quantity__gte=0), name="sales_correction_corrected_non_negative"),
        ]
        indexes = [
            models.Index(fields=["correction"]),
            models.Index(fields=["product"]),
            models.Index(fields=["source_order_line"]),
            models.Index(fields=["source_sales_document_reference"]),
            models.Index(fields=["customer_name_snapshot"]),
        ]

    def __str__(self) -> str:
        return f"{self.correction.reference} / {self.product.sku}"


class PickingTask(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        PICKED = "picked", "Picked"
        WAITING_REPLENISHMENT = "waiting_replenishment", "Waiting for replenishment"
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
    shortage_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
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
            models.CheckConstraint(check=models.Q(shortage_quantity__gte=0), name="picking_shortage_non_negative"),
            models.CheckConstraint(check=models.Q(quantity_prepared__gte=0), name="picking_prepared_non_negative"),
        ]

    def __str__(self) -> str:
        return f"Pick {self.order_line.product.sku} for {self.order_line.order.external_reference}"


class PickingShortage(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open - awaiting later stock search"
        FOUND = "found", "Found"
        CONFIRMED_MISSING = "confirmed_missing", "Confirmed missing"

    reference = models.CharField(max_length=128, unique=True, blank=True, null=True)
    picking_task = models.ForeignKey(PickingTask, on_delete=models.PROTECT, related_name="shortages")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="picking_shortages")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="picking_shortages")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="picking_shortages")
    reported_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="reported_picking_shortages")
    unconfirmed_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="unconfirmed_picking_shortages")
    cart = models.ForeignKey("ScannerCart", on_delete=models.SET_NULL, related_name="picking_shortages", blank=True, null=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    alternative_allocated_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    customer_unfulfilled_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    recovered_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    confirmed_missing_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    customer_alias_snapshot = models.CharField(max_length=128, blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reported_picking_shortages",
        blank=True,
        null=True,
    )
    reported_by_worker_code = models.CharField(max_length=64, blank=True)
    reported_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)
    confirmation_nonce = models.CharField(max_length=128, unique=True)
    client_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)
    found_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        related_name="found_picking_shortages",
        blank=True,
        null=True,
    )
    found_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="found_picking_shortages",
        blank=True,
        null=True,
    )
    found_by_worker_code = models.CharField(max_length=64, blank=True)
    found_at = models.DateTimeField(blank=True, null=True)
    confirmed_missing_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="confirmed_missing_picking_shortages",
        blank=True,
        null=True,
    )
    confirmed_missing_by_worker_code = models.CharField(max_length=64, blank=True)
    confirmed_missing_at = models.DateTimeField(blank=True, null=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-reported_at"]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["product"]),
            models.Index(fields=["reported_location"]),
            models.Index(fields=["reported_at"]),
            models.Index(fields=["reference"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="picking_shortage_quantity_positive"),
            models.CheckConstraint(
                check=models.Q(alternative_allocated_quantity__gte=0),
                name="picking_shortage_alternative_allocated_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(customer_unfulfilled_quantity__gte=0),
                name="picking_shortage_customer_unfulfilled_non_negative",
            ),
            models.CheckConstraint(check=models.Q(recovered_quantity__gte=0), name="picking_shortage_recovered_non_negative"),
            models.CheckConstraint(
                check=models.Q(confirmed_missing_quantity__gte=0),
                name="picking_shortage_confirmed_missing_non_negative",
            ),
        ]

    @property
    def unresolved_quantity(self):
        return self.quantity - self.recovered_quantity - self.confirmed_missing_quantity

    @property
    def location_missing_quantity(self):
        return self.quantity

    @property
    def unresolved_unconfirmed_quantity(self):
        return self.unresolved_quantity

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"PS-{self.id:06d}"
            super().save(update_fields=["reference"])

    def __str__(self) -> str:
        return self.reference or f"Picking shortage {self.id}"


class PickingShortageAllocation(TimestampedModel):
    class Status(models.TextChoices):
        ALLOCATED = "allocated", "Allocated"
        PICKING = "picking", "Picking"
        COMPLETED = "completed", "Completed"
        RELEASED = "released", "Released"

    shortage = models.ForeignKey(PickingShortage, on_delete=models.PROTECT, related_name="allocations")
    original_picking_task = models.ForeignKey(
        PickingTask,
        on_delete=models.PROTECT,
        related_name="shortage_original_allocations",
    )
    replacement_picking_task = models.OneToOneField(
        PickingTask,
        on_delete=models.PROTECT,
        related_name="shortage_replacement_allocation",
    )
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="picking_shortage_allocations")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="picking_shortage_allocations")
    source_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="picking_shortage_allocations")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    picked_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ALLOCATED)

    class Meta:
        ordering = ["source_location__code", "created_at"]
        indexes = [
            models.Index(fields=["shortage"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["product"]),
            models.Index(fields=["source_location"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="picking_shortage_allocation_quantity_positive"),
            models.CheckConstraint(check=models.Q(picked_quantity__gte=0), name="picking_shortage_allocation_picked_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.shortage.reference} / {self.source_location.code} / {self.quantity}"


class PickingTaskReallocation(TimestampedModel):
    class Reason(models.TextChoices):
        SYSTEM_STOCK_UNAVAILABLE = "system_stock_unavailable", "System stock unavailable"

    original_picking_task = models.ForeignKey(
        PickingTask,
        on_delete=models.PROTECT,
        related_name="system_reallocations",
    )
    replacement_picking_task = models.OneToOneField(
        PickingTask,
        on_delete=models.PROTECT,
        related_name="system_reallocation_source",
    )
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="picking_task_reallocations")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="picking_task_reallocations")
    original_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="outgoing_picking_task_reallocations",
    )
    replacement_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="incoming_picking_task_reallocations",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=64, choices=Reason.choices, default=Reason.SYSTEM_STOCK_UNAVAILABLE)

    class Meta:
        ordering = ["replacement_location__code", "created_at"]
        indexes = [
            models.Index(fields=["original_picking_task"]),
            models.Index(fields=["replacement_picking_task"]),
            models.Index(fields=["branch", "product"]),
            models.Index(fields=["original_location"]),
            models.Index(fields=["replacement_location"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="picking_task_reallocation_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"Reallocated {self.quantity} {self.product.sku} from {self.original_location.code} to {self.replacement_location.code}"


class ReplenishmentRequest(TimestampedModel):
    class Reason(models.TextChoices):
        PICKING_SHORTAGE = "picking_shortage", "Picking shortage"
        SYSTEM_STOCK_UNAVAILABLE = "system_stock_unavailable", "System stock unavailable"

    class Status(models.TextChoices):
        PENDING_ORDER = "pending_order", "Pending order"
        ORDERED_MANUALLY = "ordered_manually", "Ordered manually"
        EXPORTED_TO_AX = "exported_to_ax", "Exported to AX"
        CANCELLED = "cancelled", "Cancelled"

    reference = models.CharField(max_length=128, unique=True, blank=True, null=True)
    picking_shortage = models.OneToOneField(
        PickingShortage,
        on_delete=models.PROTECT,
        related_name="replenishment_request",
        blank=True,
        null=True,
    )
    picking_task = models.ForeignKey(
        PickingTask,
        on_delete=models.PROTECT,
        related_name="replenishment_requests",
        blank=True,
        null=True,
    )
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="replenishment_requests")
    customer_alias = models.CharField(max_length=128)
    order_reference = models.CharField(max_length=128)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="replenishment_requests")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=32, choices=Reason.choices, default=Reason.PICKING_SHORTAGE)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_ORDER)
    external_system = models.CharField(max_length=64, default="AX")
    external_reference = models.CharField(max_length=128, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_replenishment_requests",
        blank=True,
        null=True,
    )
    ordered_at = models.DateTimeField(blank=True, null=True)
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ordered_replenishment_requests",
        blank=True,
        null=True,
    )
    ordered_by_worker_code = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["product"]),
            models.Index(fields=["customer_alias"]),
            models.Index(fields=["order_reference"]),
            models.Index(fields=["reference"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="replenishment_request_quantity_positive"),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"REP-{self.id:06d}"
            super().save(update_fields=["reference"])

    def __str__(self) -> str:
        return self.reference or f"Replenishment request {self.id}"


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


class CartWorkParticipant(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LEFT = "left", "Left"

    class PickingDirection(models.TextChoices):
        BEGINNING = "beginning", "Beginning"
        END = "end", "End"
        MANUAL = "manual", "Manual selection"

    class WorkState(models.TextChoices):
        ACTIVE = "active", "Active"
        WAITING_FOR_AVAILABLE_LINE = "waiting_for_available_line", "Waiting for available line"
        COMPLETED_PARTICIPATION = "completed_participation", "Completed participation"

    cart_work_session = models.ForeignKey(CartWorkSession, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cart_work_participations")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="cart_work_participants")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    picking_direction = models.CharField(
        max_length=32,
        choices=PickingDirection.choices,
        default=PickingDirection.BEGINNING,
    )
    work_state = models.CharField(max_length=64, choices=WorkState.choices, default=WorkState.ACTIVE)
    current_picking_task = models.ForeignKey(
        PickingTask,
        on_delete=models.PROTECT,
        related_name="current_cart_work_participants",
        blank=True,
        null=True,
    )
    confirmed_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="participant_confirmed_cart_work",
        blank=True,
        null=True,
    )
    joined_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(blank=True, null=True)
    current_task_claimed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["joined_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart_work_session", "user"],
                condition=models.Q(status="active"),
                name="unique_active_participant_per_cart_work_user",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="active"),
                name="unique_active_cart_work_participant_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["cart_work_session", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["picking_direction"]),
            models.Index(fields=["work_state"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} / {self.cart_work_session.cart.code} / {self.status}"


class PickingTaskClaim(TimestampedModel):
    class Status(models.TextChoices):
        CLAIMED = "claimed", "Claimed"
        RELEASED = "released", "Released"
        COMPLETED = "completed", "Completed"

    picking_task = models.ForeignKey(PickingTask, on_delete=models.CASCADE, related_name="task_claims")
    cart_work_participant = models.ForeignKey(CartWorkParticipant, on_delete=models.CASCADE, related_name="task_claims")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.CLAIMED)
    claimed_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(default=timezone.now)
    released_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-claimed_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["picking_task"],
                condition=models.Q(status="claimed"),
                name="unique_active_claim_per_picking_task",
            ),
            models.UniqueConstraint(
                fields=["cart_work_participant"],
                condition=models.Q(status="claimed"),
                name="unique_active_claim_per_participant",
            ),
        ]
        indexes = [
            models.Index(fields=["picking_task", "status"]),
            models.Index(fields=["cart_work_participant", "status"]),
        ]

    def __str__(self) -> str:
        return f"Task {self.picking_task_id} / {self.cart_work_participant.user} / {self.status}"


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


class TransferPalletArrival(TimestampedModel):
    pallet = models.OneToOneField(TransferPallet, on_delete=models.PROTECT, related_name="arrival")
    scanned_at = models.DateTimeField(default=timezone.now)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="transfer_pallet_arrivals",
        blank=True,
        null=True,
    )
    scanned_by_worker_code = models.CharField(max_length=64, blank=True)
    client_operation_id = models.CharField(max_length=128, blank=True, null=True, unique=True)

    class Meta:
        ordering = ["-scanned_at"]
        indexes = [models.Index(fields=["scanned_at"])]

    def __str__(self) -> str:
        return f"{self.pallet.scan_code} arrived"


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
    report_printed_at = models.DateTimeField(blank=True, null=True)
    report_print_count = models.PositiveIntegerField(default=0)
    last_report_printer_code = models.CharField(max_length=64, blank=True)
    shortage_posted_at = models.DateTimeField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolved_by_worker_code = models.CharField(max_length=64, blank=True)
    confirmed_shortage_at = models.DateTimeField(blank=True, null=True)
    confirmed_shortage_by_worker_code = models.CharField(max_length=64, blank=True)

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
    posted_to_unconfirmed_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    posted_to_unconfirmed_at = models.DateTimeField(blank=True, null=True)
    recovered_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    last_recovered_at = models.DateTimeField(blank=True, null=True)
    confirmed_shortage_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    last_confirmed_shortage_at = models.DateTimeField(blank=True, null=True)

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
        RECEIVING_DISCREPANCY = "receiving_discrepancy", "Receiving discrepancy"
        DISCREPANCY_RECOVERY = "discrepancy_recovery", "Discrepancy recovery"
        DISCREPANCY_SHORTAGE = "discrepancy_shortage", "Discrepancy shortage"
        SOURCE_DISCREPANCY_RECOVERY = "source_discrepancy_recovery", "Source discrepancy recovery"
        PICKING_SHORTAGE = "picking_shortage", "Picking shortage"
        PICKING_SHORTAGE_FOUND = "picking_shortage_found", "Picking shortage found"
        PICKING_SHORTAGE_CONFIRMED_MISSING = "picking_shortage_confirmed_missing", "Picking shortage confirmed missing"
        RETURN_RECEIPT = "return_receipt", "Return receipt"
        SALES_CORRECTION_RECEIPT = "sales_correction_receipt", "Sales correction receipt"

    class AdjustmentDirection(models.TextChoices):
        INCREASE = "increase", "Increase"
        DECREASE = "decrease", "Decrease"

    class AdjustmentReason(models.TextChoices):
        COUNT_CORRECTION = "count_correction", "Count correction"
        DAMAGED_STOCK = "damaged_stock", "Damaged stock"
        FOUND_STOCK = "found_stock", "Found stock"
        DATA_ENTRY_CORRECTION = "data_entry_correction", "Data entry correction"
        OTHER = "other", "Other"

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
    movement_type = models.CharField(max_length=64, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    quantity_before = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    quantity_after = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    reference = models.CharField(max_length=128, blank=True)
    adjustment_direction = models.CharField(
        max_length=16,
        choices=AdjustmentDirection.choices,
        blank=True,
    )
    adjustment_reason = models.CharField(
        max_length=64,
        choices=AdjustmentReason.choices,
        blank=True,
    )
    adjustment_note = models.TextField(blank=True)
    cycle_count_line = models.OneToOneField(
        "CycleCountLine",
        on_delete=models.PROTECT,
        related_name="reconciliation_stock_movement",
        blank=True,
        null=True,
    )
    cycle_count_recount = models.OneToOneField(
        "CycleCountRecount",
        on_delete=models.PROTECT,
        related_name="reconciliation_stock_movement",
        blank=True,
        null=True,
    )
    external_return_action = models.ForeignKey(
        "ReturnAction",
        on_delete=models.SET_NULL,
        related_name="stock_movements",
        blank=True,
        null=True,
    )
    sales_correction_line = models.ForeignKey(
        "SalesCorrectionLine",
        on_delete=models.SET_NULL,
        related_name="stock_movements",
        blank=True,
        null=True,
    )
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


class ScannerQuickTransferOperation(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    client_operation_id = models.CharField(max_length=64, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="scanner_quick_transfer_operations")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="scanner_quick_transfer_operations")
    source_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="scanner_quick_transfer_source_operations",
    )
    destination_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="scanner_quick_transfer_destination_operations",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="scanner_quick_transfer_operations",
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    stock_movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        related_name="scanner_quick_transfer_operation",
        blank=True,
        null=True,
    )
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["client_operation_id"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["performed_by", "status"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="scanner_quick_transfer_quantity_positive"),
        ]

    def __str__(self) -> str:
        return self.client_operation_id


class CycleCountSession(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        AWAITING_REVIEW = "awaiting_review", "Awaiting review"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="cycle_count_sessions")
    reference = models.CharField(max_length=64, unique=True, blank=True)
    name = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_cycle_count_sessions",
        blank=True,
        null=True,
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="opened_cycle_count_sessions",
        blank=True,
        null=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_cycle_count_sessions",
        blank=True,
        null=True,
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="cancelled_cycle_count_sessions",
        blank=True,
        null=True,
    )
    snapshot_at = models.DateTimeField(blank=True, null=True)
    opened_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.reference or f"Cycle count session {self.id}"


class CycleCountLocation(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        CANCELLED = "cancelled", "Cancelled"

    session = models.ForeignKey(CycleCountSession, on_delete=models.CASCADE, related_name="locations")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="cycle_count_locations")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="cycle_count_locations")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="started_cycle_count_locations",
        blank=True,
        null=True,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="submitted_cycle_count_locations",
        blank=True,
        null=True,
    )
    started_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["location__code"]
        constraints = [
            models.UniqueConstraint(fields=["session", "location"], name="unique_cycle_count_location_per_session"),
        ]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["session", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.session.reference} / {self.location.code}"


class CycleCountLine(TimestampedModel):
    class ReconciliationStatus(models.TextChoices):
        NO_VARIANCE = "no_variance", "No variance"
        PENDING_REVIEW = "pending_review", "Pending review"
        ADJUSTMENT_APPLIED = "adjustment_applied", "Adjustment applied"
        NO_ADJUSTMENT_REQUIRED = "no_adjustment_required", "No adjustment required"

    session = models.ForeignKey(CycleCountSession, on_delete=models.CASCADE, related_name="lines")
    cycle_count_location = models.ForeignKey(CycleCountLocation, on_delete=models.CASCADE, related_name="lines")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="cycle_count_lines")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="cycle_count_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="cycle_count_lines")
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    counted_quantity = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="counted_cycle_count_lines",
        blank=True,
        null=True,
    )
    counted_at = models.DateTimeField(blank=True, null=True)
    is_expected = models.BooleanField(default=True)
    movement_after_snapshot = models.BooleanField(default=False)
    reconciliation_status = models.CharField(
        max_length=32,
        choices=ReconciliationStatus.choices,
        blank=True,
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reconciled_cycle_count_lines",
        blank=True,
        null=True,
    )
    reconciled_at = models.DateTimeField(blank=True, null=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["location__code", "product__sku"]
        constraints = [
            models.UniqueConstraint(fields=["cycle_count_location", "product"], name="unique_cycle_count_line_product_per_location"),
            models.CheckConstraint(check=models.Q(expected_quantity__gte=0), name="cycle_count_expected_non_negative"),
            models.CheckConstraint(
                check=models.Q(counted_quantity__gte=0) | models.Q(counted_quantity__isnull=True),
                name="cycle_count_counted_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "location"]),
            models.Index(fields=["branch", "product"]),
            models.Index(fields=["movement_after_snapshot"]),
            models.Index(fields=["reconciliation_status"]),
        ]

    @property
    def variance_quantity(self):
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.expected_quantity

    def __str__(self) -> str:
        return f"{self.session.reference} / {self.location.code} / {self.product.sku}"


class CycleCountRecount(TimestampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        ACCEPTED = "accepted", "Accepted"
        CANCELLED = "cancelled", "Cancelled"

    original_line = models.ForeignKey(CycleCountLine, on_delete=models.PROTECT, related_name="recounts")
    session = models.ForeignKey(CycleCountSession, on_delete=models.PROTECT, related_name="recounts")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="cycle_count_recounts")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="cycle_count_recounts")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="cycle_count_recounts")
    reference = models.CharField(max_length=64, unique=True, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.REQUESTED)
    reason = models.TextField()
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="requested_cycle_count_recounts",
        blank=True,
        null=True,
    )
    requested_at = models.DateTimeField(default=timezone.now)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="started_cycle_count_recounts",
        blank=True,
        null=True,
    )
    started_at = models.DateTimeField(blank=True, null=True)
    baseline_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    baseline_at = models.DateTimeField(default=timezone.now)
    counted_quantity = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="counted_cycle_count_recounts",
        blank=True,
        null=True,
    )
    counted_at = models.DateTimeField(blank=True, null=True)
    movement_after_baseline = models.BooleanField(default=False)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="accepted_cycle_count_recounts",
        blank=True,
        null=True,
    )
    accepted_at = models.DateTimeField(blank=True, null=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="cancelled_cycle_count_recounts",
        blank=True,
        null=True,
    )
    cancelled_at = models.DateTimeField(blank=True, null=True)
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["session", "status"]),
            models.Index(fields=["original_line", "status"]),
            models.Index(fields=["reference"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(counted_quantity__gte=0) | models.Q(counted_quantity__isnull=True),
                name="cycle_count_recount_counted_non_negative",
            ),
            models.UniqueConstraint(
                fields=["original_line"],
                condition=models.Q(status__in=["requested", "in_progress", "submitted"]),
                name="unique_active_cycle_count_recount_per_line",
            ),
        ]

    @property
    def variance_quantity(self):
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.baseline_quantity

    def __str__(self) -> str:
        return self.reference or f"Cycle count recount {self.id}"


class TransferDiscrepancyRecovery(TimestampedModel):
    discrepancy = models.ForeignKey(TransferDiscrepancy, on_delete=models.PROTECT, related_name="recoveries")
    discrepancy_item = models.ForeignKey(TransferDiscrepancyItem, on_delete=models.PROTECT, related_name="recoveries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_discrepancy_recoveries")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    source_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="discrepancy_recovery_sources")
    destination_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="discrepancy_recovery_destinations")
    worker_code = models.CharField(max_length=64, blank=True)
    recovered_at = models.DateTimeField(default=timezone.now)
    client_operation_id = models.CharField(max_length=128, unique=True)
    stock_movement = models.ForeignKey(
        StockMovement,
        on_delete=models.PROTECT,
        related_name="discrepancy_recoveries",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-recovered_at"]
        indexes = [
            models.Index(fields=["discrepancy"]),
            models.Index(fields=["discrepancy_item"]),
            models.Index(fields=["product"]),
            models.Index(fields=["client_operation_id"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="transfer_discrepancy_recovery_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.discrepancy.reference} / {self.product.sku} / {self.quantity}"


class TransferDiscrepancyShortageConfirmation(TimestampedModel):
    discrepancy = models.ForeignKey(TransferDiscrepancy, on_delete=models.PROTECT, related_name="shortage_confirmations")
    discrepancy_item = models.ForeignKey(
        TransferDiscrepancyItem,
        on_delete=models.PROTECT,
        related_name="shortage_confirmations",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_discrepancy_shortage_confirmations")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unconfirmed_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="discrepancy_shortage_confirmations",
    )
    worker_code = models.CharField(max_length=64, blank=True)
    confirmed_at = models.DateTimeField(default=timezone.now)
    client_operation_id = models.CharField(max_length=128, unique=True)
    stock_movement = models.ForeignKey(
        StockMovement,
        on_delete=models.PROTECT,
        related_name="discrepancy_shortage_confirmations",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-confirmed_at"]
        indexes = [
            models.Index(fields=["discrepancy"]),
            models.Index(fields=["discrepancy_item"]),
            models.Index(fields=["product"]),
            models.Index(fields=["client_operation_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name="transfer_discrepancy_shortage_confirmation_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.discrepancy.reference} / {self.product.sku} / {self.quantity}"


class TransferDiscrepancySourceReview(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending review"
        INVESTIGATING = "investigating", "Investigating"
        COMPLETED = "completed", "Completed"

    class Finding(models.TextChoices):
        SOURCE_SHORTAGE_FOUND = "source_shortage_found", "Source shortage found"
        DISPATCH_EVIDENCE_MATCHES = "dispatch_evidence_matches", "Dispatch evidence matches expected quantity"
        INCONCLUSIVE = "inconclusive", "Inconclusive"

    reference = models.CharField(max_length=64, unique=True, blank=True)
    discrepancy = models.OneToOneField(
        TransferDiscrepancy,
        on_delete=models.PROTECT,
        related_name="source_review",
    )
    source_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="transfer_discrepancy_source_reviews")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_REVIEW)
    finding = models.CharField(max_length=64, choices=Finding.choices, blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    started_by_worker_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    completed_by_worker_code = models.CharField(max_length=64, blank=True)
    finding_note = models.TextField(blank=True)
    client_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["source_branch", "status"]),
            models.Index(fields=["client_operation_id"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"SRV-{self.id:06d}"
            super().save(update_fields=["reference", "updated_at"])

    def __str__(self) -> str:
        return self.reference or f"Source review {self.id}"


class TransferDiscrepancyReconciliation(TimestampedModel):
    class Route(models.TextChoices):
        SOURCE_STOCK_VERIFICATION = "source_stock_verification", "Source stock verification required"
        TRANSIT_INVESTIGATION = "transit_investigation", "Transit investigation required"
        MANUAL_RECONCILIATION = "manual_reconciliation", "Manual reconciliation required"

    class Status(models.TextChoices):
        PENDING_ACTION = "pending_action", "Pending action"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        MANUAL_ACTION_REQUIRED = "manual_action_required", "Manual action required"

    reference = models.CharField(max_length=64, unique=True, blank=True)
    discrepancy = models.OneToOneField(
        TransferDiscrepancy,
        on_delete=models.PROTECT,
        related_name="reconciliation",
    )
    source_review = models.OneToOneField(
        TransferDiscrepancySourceReview,
        on_delete=models.PROTECT,
        related_name="reconciliation",
    )
    route = models.CharField(max_length=64, choices=Route.choices)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_ACTION)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    acknowledged_by_worker_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    completed_by_worker_code = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["route"]),
            models.Index(fields=["status"]),
            models.Index(fields=["route", "status"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"REC-{self.id:06d}"
            super().save(update_fields=["reference", "updated_at"])

    def __str__(self) -> str:
        return self.reference or f"Reconciliation {self.id}"


class TransferDiscrepancyManualReconciliationDecision(TimestampedModel):
    class Outcome(models.TextChoices):
        SOURCE_LOSS_CONFIRMED = "source_loss_confirmed", "Source loss confirmed"
        TRANSIT_LOSS_CONFIRMED = "transit_loss_confirmed", "Transit loss confirmed"
        UNRESOLVED_LOSS_CLOSED = "unresolved_loss_closed", "Unresolved loss - cause not determined"
        ADMINISTRATIVE_ERROR = "administrative_error", "Administrative or process error"

    reconciliation = models.OneToOneField(
        TransferDiscrepancyReconciliation,
        on_delete=models.PROTECT,
        related_name="manual_decision",
    )
    outcome = models.CharField(max_length=64, choices=Outcome.choices)
    decision_note = models.TextField()
    decided_at = models.DateTimeField(default=timezone.now)
    decided_by_worker_code = models.CharField(max_length=64)
    client_operation_id = models.CharField(max_length=128, unique=True)

    class Meta:
        ordering = ["-decided_at"]
        indexes = [
            models.Index(fields=["outcome"]),
            models.Index(fields=["decided_at"]),
            models.Index(fields=["client_operation_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.reconciliation.reference} / {self.get_outcome_display()}"


class TransferDiscrepancyTransitInvestigation(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_INVESTIGATION = "pending_investigation", "Pending investigation"
        INVESTIGATING = "investigating", "Investigating"
        COMPLETED = "completed", "Completed"

    class Finding(models.TextChoices):
        TRANSIT_IRREGULARITY_FOUND = "transit_irregularity_found", "Transit irregularity found"
        NO_TRANSIT_IRREGULARITY_IDENTIFIED = (
            "no_transit_irregularity_identified",
            "No transit irregularity identified",
        )
        INCONCLUSIVE = "inconclusive", "Inconclusive"

    reference = models.CharField(max_length=64, unique=True, blank=True)
    reconciliation = models.OneToOneField(
        TransferDiscrepancyReconciliation,
        on_delete=models.PROTECT,
        related_name="transit_investigation",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_INVESTIGATION)
    finding = models.CharField(max_length=64, choices=Finding.choices, blank=True)
    finding_note = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    started_by_worker_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    completed_by_worker_code = models.CharField(max_length=64, blank=True)
    completion_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["finding"]),
            models.Index(fields=["completion_operation_id"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"TRI-{self.id:06d}"
            super().save(update_fields=["reference", "updated_at"])

    def __str__(self) -> str:
        return self.reference or f"Transit investigation {self.id}"


class TransferDiscrepancySourceStockVerification(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_VERIFICATION = "pending_verification", "Pending verification"
        INVESTIGATING = "investigating", "Investigating"
        COMPLETED = "completed", "Completed"
        COMPLETED_UNRESOLVED = "completed_unresolved", "Completed with unresolved stock"

    reference = models.CharField(max_length=64, unique=True, blank=True)
    reconciliation = models.OneToOneField(
        TransferDiscrepancyReconciliation,
        on_delete=models.PROTECT,
        related_name="source_stock_verification",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_VERIFICATION)
    started_at = models.DateTimeField(blank=True, null=True)
    started_by_worker_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    completed_by_worker_code = models.CharField(max_length=64, blank=True)
    search_completed_at = models.DateTimeField(blank=True, null=True)
    search_completed_by_worker_code = models.CharField(max_length=64, blank=True)
    search_completion_note = models.TextField(blank=True)
    search_completion_operation_id = models.CharField(max_length=128, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["search_completion_operation_id"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"SSV-{self.id:06d}"
            super().save(update_fields=["reference", "updated_at"])

    def __str__(self) -> str:
        return self.reference or f"Source stock verification {self.id}"


class TransferDiscrepancySourceStockVerificationItem(TimestampedModel):
    verification = models.ForeignKey(
        TransferDiscrepancySourceStockVerification,
        on_delete=models.CASCADE,
        related_name="items",
    )
    discrepancy_item = models.ForeignKey(
        TransferDiscrepancyItem,
        on_delete=models.PROTECT,
        related_name="source_stock_verification_items",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="source_stock_verification_items")
    target_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    found_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    last_found_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["product__sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["verification", "discrepancy_item"],
                name="unique_source_stock_verification_item_per_discrepancy_item",
            ),
            models.CheckConstraint(check=models.Q(target_quantity__gt=0), name="source_stock_verification_target_positive"),
            models.CheckConstraint(check=models.Q(found_quantity__gte=0), name="source_stock_verification_found_non_negative"),
        ]
        indexes = [
            models.Index(fields=["verification"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"{self.verification.reference} / {self.product.sku}"


class TransferDiscrepancySourceStockRecovery(TimestampedModel):
    verification = models.ForeignKey(
        TransferDiscrepancySourceStockVerification,
        on_delete=models.PROTECT,
        related_name="recoveries",
    )
    verification_item = models.ForeignKey(
        TransferDiscrepancySourceStockVerificationItem,
        on_delete=models.PROTECT,
        related_name="recoveries",
    )
    discrepancy = models.ForeignKey(TransferDiscrepancy, on_delete=models.PROTECT, related_name="source_stock_recoveries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="source_stock_recoveries")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    destination_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="source_stock_recoveries")
    worker_code = models.CharField(max_length=64, blank=True)
    recovered_at = models.DateTimeField(default=timezone.now)
    client_operation_id = models.CharField(max_length=128, unique=True)
    stock_movement = models.ForeignKey(
        StockMovement,
        on_delete=models.PROTECT,
        related_name="source_stock_recoveries",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-recovered_at"]
        indexes = [
            models.Index(fields=["verification"]),
            models.Index(fields=["verification_item"]),
            models.Index(fields=["discrepancy"]),
            models.Index(fields=["product"]),
            models.Index(fields=["client_operation_id"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="source_stock_recovery_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.verification.reference} / {self.product.sku} / {self.quantity}"


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
    event_type = models.CharField(max_length=64, blank=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    checked_quantity = models.DecimalField(max_digits=12, decimal_places=3, blank=True, null=True)
    source_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        related_name="source_audit_logs",
        blank=True,
        null=True,
    )
    destination_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        related_name="destination_audit_logs",
        blank=True,
        null=True,
    )
    source_label = models.CharField(max_length=128, blank=True)
    destination_label = models.CharField(max_length=128, blank=True)
    cart = models.ForeignKey(
        "ScannerCart",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    route_run = models.ForeignKey(
        "RouteRun",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    transfer = models.ForeignKey(
        "InterBranchTransfer",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    pallet = models.ForeignKey(
        "TransferPallet",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    discrepancy = models.ForeignKey(
        "TransferDiscrepancy",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    result = models.CharField(max_length=64, blank=True)
    reference = models.CharField(max_length=128, blank=True)
    entity_name = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=64, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action_type"]),
            models.Index(fields=["event_type"]),
            models.Index(fields=["branch", "event_type"]),
            models.Index(fields=["product"]),
            models.Index(fields=["cart"]),
            models.Index(fields=["order"]),
            models.Index(fields=["route_run"]),
            models.Index(fields=["transfer"]),
            models.Index(fields=["pallet"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["entity_name", "entity_id"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action_type} {self.entity_name} {self.entity_id}".strip()
