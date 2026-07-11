from django.contrib import admin

from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkSession,
    DeliveryRoute,
    InterBranchTransfer,
    Order,
    OrderLine,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingJobTask,
    PickingTask,
    ReturnBatch,
    ReturnLine,
    RouteRun,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    StockMovement,
    TransferDiscrepancy,
    TransferDiscrepancyItem,
    TransferDiscrepancyManualReconciliationDecision,
    TransferDiscrepancyReconciliation,
    TransferDiscrepancyRecovery,
    TransferDiscrepancyShortageConfirmation,
    TransferDiscrepancySourceStockRecovery,
    TransferDiscrepancySourceStockVerification,
    TransferDiscrepancySourceStockVerificationItem,
    TransferDiscrepancySourceReview,
    TransferDiscrepancyTransitInvestigation,
    TransferPallet,
    TransferPalletArrival,
    TransferPalletItem,
)


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(DeliveryRoute)
class DeliveryRouteAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "branch", "is_active", "updated_at"]
    list_filter = ["branch", "is_active"]
    search_fields = ["code", "name", "branch__code", "branch__name"]


@admin.register(RouteRun)
class RouteRunAdmin(admin.ModelAdmin):
    list_display = [
        "route",
        "service_date",
        "run_number",
        "departure_time",
        "status",
        "orders_count",
        "pending_lines_count",
        "is_urgent",
        "is_selectable",
    ]
    list_filter = ["status", "service_date", "route__branch", "route"]
    search_fields = ["route__code", "route__name", "route__branch__code"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "external_reference",
        "branch",
        "route_run",
        "status",
        "customer_name",
        "requested_ship_date",
        "created_at",
    ]
    list_filter = ["status", "branch", "route_run", "requested_ship_date"]
    search_fields = ["external_reference", "customer_name", "route_run__route__code"]
    inlines = [OrderLineInline]


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ["order", "line_number", "product", "quantity_ordered", "quantity_picked"]
    list_filter = ["order__branch"]
    search_fields = ["order__external_reference", "product__sku", "product__name"]


class ReturnLineInline(admin.TabularInline):
    model = ReturnLine
    extra = 0


@admin.register(ReturnBatch)
class ReturnBatchAdmin(admin.ModelAdmin):
    list_display = ["reference", "branch", "status", "received_at", "created_at"]
    list_filter = ["status", "branch"]
    search_fields = ["reference"]
    inlines = [ReturnLineInline]


@admin.register(ReturnLine)
class ReturnLineAdmin(admin.ModelAdmin):
    list_display = ["return_batch", "line_number", "product", "quantity", "condition"]
    list_filter = ["condition", "return_batch__branch"]
    search_fields = ["return_batch__reference", "product__sku", "product__name"]


@admin.register(PickingTask)
class PickingTaskAdmin(admin.ModelAdmin):
    list_display = [
        "order_line",
        "branch",
        "source_location",
        "assigned_to",
        "status",
        "quantity_to_pick",
        "quantity_picked",
    ]
    list_filter = ["status", "branch", "assigned_to"]
    search_fields = [
        "order_line__order__external_reference",
        "order_line__product__sku",
        "source_location__code",
    ]


class PickingJobTaskInline(admin.TabularInline):
    model = PickingJobTask
    extra = 0


@admin.register(PickingJob)
class PickingJobAdmin(admin.ModelAdmin):
    list_display = ["id", "mode", "status", "started_at", "completed_at", "created_at"]
    list_filter = ["mode", "status"]
    search_fields = ["id", "route_runs__route__code"]
    filter_horizontal = ["route_runs"]
    inlines = [PickingJobTaskInline]


@admin.register(PickingJobTask)
class PickingJobTaskAdmin(admin.ModelAdmin):
    list_display = ["picking_job", "picking_task", "created_at"]
    search_fields = ["picking_job__id", "picking_task__order_line__order__external_reference"]


@admin.register(ScannerCart)
class ScannerCartAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "status", "updated_at"]
    list_filter = ["status"]
    search_fields = ["code", "name"]


@admin.register(ScannerSession)
class ScannerSessionAdmin(admin.ModelAdmin):
    list_display = ["cart", "worker_code", "status", "started_at", "ended_at"]
    list_filter = ["status", "cart"]
    search_fields = ["cart__code", "worker_code"]


@admin.register(CartWorkSession)
class CartWorkSessionAdmin(admin.ModelAdmin):
    list_display = ["id", "cart", "picking_job", "status", "started_at", "finished_at"]
    list_filter = ["status", "cart"]
    search_fields = ["cart__code", "picking_job__id"]


@admin.register(CartPickedItem)
class CartPickedItemAdmin(admin.ModelAdmin):
    list_display = ["cart", "picking_task", "product", "quantity_picked", "quantity_prepared", "created_at"]
    list_filter = ["cart", "route_run"]
    search_fields = ["cart__code", "product__sku", "picking_task__order_line__order__external_reference"]


@admin.register(ScannerCustomerLabel)
class ScannerCustomerLabelAdmin(admin.ModelAdmin):
    list_display = ["scan_code", "session", "order", "printer_code", "printed_at"]
    list_filter = ["printer_code"]
    search_fields = ["scan_code", "order__external_reference", "session__cart__code"]


@admin.register(InterBranchTransfer)
class InterBranchTransferAdmin(admin.ModelAdmin):
    list_display = ["reference", "source_branch", "destination_branch", "status", "released_at", "completed_at"]
    list_filter = ["status", "source_branch", "destination_branch"]
    search_fields = ["reference"]


@admin.register(TransferPallet)
class TransferPalletAdmin(admin.ModelAdmin):
    list_display = ["scan_code", "transfer", "status", "released_at", "receiving_started_at", "received_at"]
    list_filter = ["status", "transfer__source_branch", "transfer__destination_branch"]
    search_fields = ["scan_code", "transfer__reference"]


@admin.register(TransferPalletItem)
class TransferPalletItemAdmin(admin.ModelAdmin):
    list_display = ["pallet", "product", "expected_quantity", "received_quantity"]
    search_fields = ["pallet__scan_code", "product__sku", "product__name"]


@admin.register(TransferPalletArrival)
class TransferPalletArrivalAdmin(admin.ModelAdmin):
    list_display = ["pallet", "scanned_at", "scanned_by", "scanned_by_worker_code"]
    search_fields = ["pallet__scan_code", "pallet__transfer__reference", "scanned_by__username", "scanned_by_worker_code"]


@admin.register(PalletReceivingSession)
class PalletReceivingSessionAdmin(admin.ModelAdmin):
    list_display = ["pallet", "status", "worker_code", "current_pallet_item", "pending_quantity", "started_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["pallet__scan_code", "worker_code"]


@admin.register(PalletReceivingScan)
class PalletReceivingScanAdmin(admin.ModelAdmin):
    list_display = ["pallet", "product", "destination_location", "quantity", "worker_code", "scanned_at"]
    list_filter = ["pallet__transfer__destination_branch", "product", "destination_location"]
    search_fields = ["pallet__scan_code", "product__sku", "destination_location__code"]


class TransferDiscrepancyItemInline(admin.TabularInline):
    model = TransferDiscrepancyItem
    extra = 0


@admin.register(TransferDiscrepancy)
class TransferDiscrepancyAdmin(admin.ModelAdmin):
    list_display = ["reference", "pallet", "transfer", "status", "created_by_worker_code", "created_at"]
    list_filter = ["status", "transfer__source_branch", "transfer__destination_branch"]
    search_fields = ["reference", "pallet__scan_code", "transfer__reference"]
    inlines = [TransferDiscrepancyItemInline]


@admin.register(TransferDiscrepancyItem)
class TransferDiscrepancyItemAdmin(admin.ModelAdmin):
    list_display = [
        "discrepancy",
        "product",
        "discrepancy_type",
        "expected_quantity",
        "received_quantity",
        "difference_quantity",
        "discrepancy_quantity",
        "recovered_quantity",
        "confirmed_shortage_quantity",
    ]
    list_filter = ["discrepancy_type", "product"]
    search_fields = ["discrepancy__reference", "product__sku", "product__name"]


@admin.register(TransferDiscrepancyRecovery)
class TransferDiscrepancyRecoveryAdmin(admin.ModelAdmin):
    list_display = ["discrepancy", "product", "quantity", "source_location", "destination_location", "worker_code", "recovered_at"]
    list_filter = ["product", "source_location", "destination_location"]
    search_fields = ["discrepancy__reference", "product__sku", "destination_location__code", "client_operation_id"]


@admin.register(TransferDiscrepancyShortageConfirmation)
class TransferDiscrepancyShortageConfirmationAdmin(admin.ModelAdmin):
    list_display = ["discrepancy", "product", "quantity", "unconfirmed_location", "worker_code", "confirmed_at"]
    list_filter = ["product", "unconfirmed_location"]
    search_fields = ["discrepancy__reference", "product__sku", "unconfirmed_location__code", "client_operation_id"]


@admin.register(TransferDiscrepancySourceReview)
class TransferDiscrepancySourceReviewAdmin(admin.ModelAdmin):
    list_display = ["reference", "discrepancy", "source_branch", "status", "finding", "created_at"]
    list_filter = ["status", "finding", "source_branch"]
    search_fields = [
        "reference",
        "discrepancy__reference",
        "discrepancy__pallet__scan_code",
        "discrepancy__transfer__reference",
    ]


@admin.register(TransferDiscrepancyReconciliation)
class TransferDiscrepancyReconciliationAdmin(admin.ModelAdmin):
    list_display = ["reference", "discrepancy", "source_review", "route", "status", "created_at"]
    list_filter = ["route", "status", "discrepancy__transfer__source_branch", "discrepancy__transfer__destination_branch"]
    search_fields = [
        "reference",
        "source_review__reference",
        "discrepancy__reference",
        "discrepancy__pallet__scan_code",
        "discrepancy__transfer__reference",
    ]


@admin.register(TransferDiscrepancyManualReconciliationDecision)
class TransferDiscrepancyManualReconciliationDecisionAdmin(admin.ModelAdmin):
    list_display = ["reconciliation", "outcome", "decided_by_worker_code", "decided_at"]
    list_filter = ["outcome", "decided_at"]
    search_fields = ["reconciliation__reference", "decision_note", "client_operation_id"]


@admin.register(TransferDiscrepancyTransitInvestigation)
class TransferDiscrepancyTransitInvestigationAdmin(admin.ModelAdmin):
    list_display = ["reference", "reconciliation", "status", "finding", "started_at", "completed_at"]
    list_filter = ["status", "finding", "reconciliation__discrepancy__transfer__source_branch"]
    search_fields = [
        "reference",
        "reconciliation__reference",
        "reconciliation__discrepancy__reference",
        "reconciliation__discrepancy__pallet__scan_code",
        "reconciliation__discrepancy__transfer__reference",
    ]


class TransferDiscrepancySourceStockVerificationItemInline(admin.TabularInline):
    model = TransferDiscrepancySourceStockVerificationItem
    extra = 0


@admin.register(TransferDiscrepancySourceStockVerification)
class TransferDiscrepancySourceStockVerificationAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "reconciliation",
        "status",
        "started_at",
        "completed_at",
        "search_completed_at",
        "created_at",
    ]
    list_filter = ["status", "reconciliation__discrepancy__transfer__source_branch"]
    search_fields = [
        "reference",
        "reconciliation__reference",
        "reconciliation__discrepancy__reference",
        "reconciliation__discrepancy__pallet__scan_code",
    ]
    inlines = [TransferDiscrepancySourceStockVerificationItemInline]


@admin.register(TransferDiscrepancySourceStockRecovery)
class TransferDiscrepancySourceStockRecoveryAdmin(admin.ModelAdmin):
    list_display = ["verification", "product", "quantity", "destination_location", "worker_code", "recovered_at"]
    list_filter = ["product", "destination_location"]
    search_fields = ["verification__reference", "product__sku", "destination_location__code", "client_operation_id"]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["movement_type", "product", "branch", "quantity", "reference", "performed_by", "created_at"]
    list_filter = ["movement_type", "branch", "performed_by"]
    search_fields = ["product__sku", "product__name", "reference"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["event_type", "action_type", "branch", "product", "reference", "actor", "created_at"]
    list_filter = ["event_type", "action_type", "branch", "entity_name", "actor"]
    search_fields = [
        "entity_name",
        "entity_id",
        "message",
        "reference",
        "actor__username",
        "product__sku",
        "cart__code",
        "order__external_reference",
        "route_run__route__code",
        "transfer__reference",
        "pallet__scan_code",
        "discrepancy__reference",
    ]
    readonly_fields = ["created_at"]
