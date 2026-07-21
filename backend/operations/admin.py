from django.contrib import admin

from operations.models import (
    AuditLog,
    CartPickedItem,
    CartWorkParticipant,
    CartWorkSession,
    DeliveryRoute,
    ExternalReturnDocument,
    ExternalReturnDocumentLine,
    InterBranchTransfer,
    Order,
    OrderLine,
    PalletReceivingScan,
    PalletReceivingSession,
    PickingJob,
    PickingJobTask,
    PickingShortage,
    PickingShortageAllocation,
    PickingTaskReallocation,
    PickingTask,
    PickingTaskClaim,
    ReplenishmentRequest,
    ReturnAction,
    ReturnBatch,
    ReturnLine,
    RouteRun,
    SalesCorrection,
    SalesCorrectionLine,
    ScannerCart,
    ScannerCustomerLabel,
    ScannerSession,
    Shipment,
    ShipmentLine,
    ShipmentLineQuantityAdjustment,
    ShipmentRouteAssignment,
    ShipmentStatusHistory,
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


class ShipmentLineInline(admin.TabularInline):
    model = ShipmentLine
    extra = 0
    readonly_fields = ["order_line", "product", "line_number", "ordered_quantity"]


class ShipmentRouteAssignmentInline(admin.TabularInline):
    model = ShipmentRouteAssignment
    extra = 0
    readonly_fields = ["previous_route_run", "new_route_run", "changed_by", "reason", "created_at"]


class ShipmentStatusHistoryInline(admin.TabularInline):
    model = ShipmentStatusHistory
    extra = 0
    readonly_fields = ["previous_status", "new_status", "changed_by", "reason", "created_at"]


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["reference", "branch", "status", "shipment_type", "route_run", "document_status", "delivery_date"]
    list_filter = ["branch", "status", "shipment_type", "document_status", "delivery_date"]
    search_fields = ["reference", "external_reference", "order__external_reference", "customer_name", "customer_alias"]
    inlines = [ShipmentLineInline, ShipmentRouteAssignmentInline, ShipmentStatusHistoryInline]


@admin.register(ShipmentLine)
class ShipmentLineAdmin(admin.ModelAdmin):
    list_display = ["shipment", "line_number", "product", "ordered_quantity", "external_line_reference"]
    list_filter = ["product", "shipment__branch", "shipment__status"]
    search_fields = ["shipment__reference", "product__sku", "product__name", "external_line_reference"]


@admin.register(ShipmentLineQuantityAdjustment)
class ShipmentLineQuantityAdjustmentAdmin(admin.ModelAdmin):
    list_display = ["shipment", "shipment_line", "quantity_removed", "adjusted_by", "created_at"]
    list_filter = ["shipment__branch", "created_at"]
    search_fields = ["shipment__reference", "shipment_line__product__sku", "reason"]


@admin.register(ShipmentRouteAssignment)
class ShipmentRouteAssignmentAdmin(admin.ModelAdmin):
    list_display = ["shipment", "previous_route_run", "new_route_run", "changed_by", "created_at"]
    list_filter = ["new_route_run__route__branch", "created_at"]
    search_fields = ["shipment__reference", "reason"]


@admin.register(ShipmentStatusHistory)
class ShipmentStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ["shipment", "previous_status", "new_status", "changed_by", "created_at"]
    list_filter = ["new_status", "created_at"]
    search_fields = ["shipment__reference", "reason"]


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


class ExternalReturnDocumentLineInline(admin.TabularInline):
    model = ExternalReturnDocumentLine
    extra = 0
    readonly_fields = ["accepted_quantity", "rejected_quantity", "on_hold_quantity"]


@admin.register(ExternalReturnDocument)
class ExternalReturnDocumentAdmin(admin.ModelAdmin):
    list_display = ["external_reference", "source_system", "branch", "customer_name", "status", "imported_at"]
    list_filter = ["status", "source_system", "branch"]
    search_fields = ["external_reference", "customer_name", "source_sales_document_reference", "lines__product__sku"]
    inlines = [ExternalReturnDocumentLineInline]


@admin.register(ExternalReturnDocumentLine)
class ExternalReturnDocumentLineAdmin(admin.ModelAdmin):
    list_display = [
        "document",
        "line_number",
        "product",
        "expected_quantity",
        "accepted_quantity",
        "rejected_quantity",
        "on_hold_quantity",
    ]
    list_filter = ["document__branch", "document__status"]
    search_fields = ["document__external_reference", "product__sku", "product__name"]


@admin.register(ReturnAction)
class ReturnActionAdmin(admin.ModelAdmin):
    list_display = ["document", "line", "action_type", "quantity", "performed_by", "created_at"]
    list_filter = ["action_type", "source_pool", "branch"]
    search_fields = ["document__external_reference", "product__sku", "performed_by__username", "client_operation_id"]
    readonly_fields = ["client_operation_id", "payload_fingerprint", "stock_movement"]


class SalesCorrectionLineInline(admin.TabularInline):
    model = SalesCorrectionLine
    extra = 0
    readonly_fields = ["returns_location", "stock_movement", "inventory_quantity_before", "inventory_quantity_after"]


@admin.register(SalesCorrection)
class SalesCorrectionAdmin(admin.ModelAdmin):
    list_display = ["reference", "branch", "status", "created_by", "confirmed_by", "confirmed_at"]
    list_filter = ["status", "branch", "confirmed_at"]
    search_fields = ["reference", "lines__customer_name_snapshot", "lines__source_sales_document_reference"]
    inlines = [SalesCorrectionLineInline]


@admin.register(SalesCorrectionLine)
class SalesCorrectionLineAdmin(admin.ModelAdmin):
    list_display = [
        "correction",
        "product",
        "customer_name_snapshot",
        "source_sales_document_reference",
        "corrected_quantity",
        "returns_location",
    ]
    list_filter = ["correction__branch", "correction__status", "returns_location"]
    search_fields = ["correction__reference", "product__sku", "customer_name_snapshot", "source_sales_document_reference"]


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
        "shortage_quantity",
    ]
    list_filter = ["status", "branch", "assigned_to"]
    search_fields = [
        "order_line__order__external_reference",
        "order_line__product__sku",
        "source_location__code",
    ]


@admin.register(PickingShortage)
class PickingShortageAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "branch",
        "product",
        "quantity",
        "alternative_allocated_quantity",
        "customer_unfulfilled_quantity",
        "reported_location",
        "cart",
        "status",
        "reported_at",
    ]
    list_filter = ["branch", "status", "product", "reported_location"]
    search_fields = ["reference", "product__sku", "order__external_reference", "cart__code", "customer_alias_snapshot"]


@admin.register(PickingShortageAllocation)
class PickingShortageAllocationAdmin(admin.ModelAdmin):
    list_display = ["shortage", "replacement_picking_task", "source_location", "quantity", "picked_quantity", "status"]
    list_filter = ["status", "branch", "source_location"]
    search_fields = ["shortage__reference", "product__sku", "source_location__code"]


@admin.register(PickingTaskReallocation)
class PickingTaskReallocationAdmin(admin.ModelAdmin):
    list_display = ["original_picking_task", "replacement_picking_task", "replacement_location", "quantity", "reason"]
    list_filter = ["branch", "product", "original_location", "replacement_location", "reason"]
    search_fields = [
        "product__sku",
        "original_location__code",
        "replacement_location__code",
        "original_picking_task__order_line__order__external_reference",
    ]


@admin.register(ReplenishmentRequest)
class ReplenishmentRequestAdmin(admin.ModelAdmin):
    list_display = ["reference", "branch", "customer_alias", "product", "quantity", "status", "created_at"]
    list_filter = ["branch", "status", "reason", "product"]
    search_fields = ["reference", "customer_alias", "product__sku", "order_reference", "picking_shortage__cart__code"]


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


@admin.register(CartWorkParticipant)
class CartWorkParticipantAdmin(admin.ModelAdmin):
    list_display = ["cart_work_session", "user", "branch", "status", "current_picking_task", "joined_at", "left_at"]
    list_filter = ["status", "branch"]
    search_fields = ["cart_work_session__cart__code", "user__username", "current_picking_task__order_line__product__sku"]


@admin.register(PickingTaskClaim)
class PickingTaskClaimAdmin(admin.ModelAdmin):
    list_display = ["picking_task", "cart_work_participant", "status", "claimed_at", "released_at"]
    list_filter = ["status"]
    search_fields = ["picking_task__order_line__product__sku", "cart_work_participant__user__username"]


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
