from rest_framework.routers import DefaultRouter

from accounts.viewsets import AuthLoginView, AuthLogoutView, AuthSessionView, CurrentUserBranchMembershipsView
from operations.viewsets import (
    AuditLogViewSet,
    BranchDispatchPolicyViewSet,
    CycleCountReviewQueueViewSet,
    CycleCountSessionViewSet,
    DeliveryRouteViewSet,
    ExternalReturnDocumentViewSet,
    InventoryExceptionSummaryViewSet,
    OrderLineViewSet,
    OrderViewSet,
    PickingShortageViewSet,
    PickingTaskViewSet,
    ReturnBatchViewSet,
    ReturnLineViewSet,
    ReplenishmentRequestViewSet,
    RouteRoundScheduleViewSet,
    RouteRunViewSet,
    SalesCorrectionViewSet,
    ShipmentViewSet,
    StockAdjustmentViewSet,
    StockMovementViewSet,
    TransportOverviewViewSet,
    TransferDiscrepancyActionViewSet,
    TransferDiscrepancyReconciliationViewSet,
    TransferDiscrepancySourceStockVerificationViewSet,
    TransferDiscrepancySourceReviewViewSet,
    TransferDiscrepancyTransitInvestigationViewSet,
    TransferDiscrepancyViewSet,
)
from warehouse.viewsets import BranchViewSet, InventoryItemViewSet, LocationViewSet, ProductViewSet


router = DefaultRouter()
router.register("branches", BranchViewSet, basename="branch")
router.register("locations", LocationViewSet, basename="location")
router.register("products", ProductViewSet, basename="product")
router.register("inventory-items", InventoryItemViewSet, basename="inventory-item")
router.register("cycle-counts", CycleCountSessionViewSet, basename="cycle-count")
router.register("cycle-count-review-queue", CycleCountReviewQueueViewSet, basename="cycle-count-review-queue")
router.register("inventory-exceptions", InventoryExceptionSummaryViewSet, basename="inventory-exception")
router.register("transport-overview", TransportOverviewViewSet, basename="transport-overview")
router.register("delivery-routes", DeliveryRouteViewSet, basename="delivery-route")
router.register("route-round-schedules", RouteRoundScheduleViewSet, basename="route-round-schedule")
router.register("branch-dispatch-policies", BranchDispatchPolicyViewSet, basename="branch-dispatch-policy")
router.register("route-runs", RouteRunViewSet, basename="route-run")
router.register("orders", OrderViewSet, basename="order")
router.register("order-lines", OrderLineViewSet, basename="order-line")
router.register("return-batches", ReturnBatchViewSet, basename="return-batch")
router.register("return-lines", ReturnLineViewSet, basename="return-line")
router.register("return-documents", ExternalReturnDocumentViewSet, basename="return-document")
router.register("sales-corrections", SalesCorrectionViewSet, basename="sales-correction")
router.register("shipments", ShipmentViewSet, basename="shipment")
router.register("picking-tasks", PickingTaskViewSet, basename="picking-task")
router.register("picking-shortages", PickingShortageViewSet, basename="picking-shortage")
router.register("replenishment-requests", ReplenishmentRequestViewSet, basename="replenishment-request")
router.register("stock-adjustments", StockAdjustmentViewSet, basename="stock-adjustment")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
router.register("transfer-discrepancy-actions", TransferDiscrepancyActionViewSet, basename="transfer-discrepancy-action")
router.register("transfer-discrepancies", TransferDiscrepancyViewSet, basename="transfer-discrepancy")
router.register(
    "transfer-discrepancy-source-reviews",
    TransferDiscrepancySourceReviewViewSet,
    basename="transfer-discrepancy-source-review",
)
router.register(
    "transfer-discrepancy-reconciliations",
    TransferDiscrepancyReconciliationViewSet,
    basename="transfer-discrepancy-reconciliation",
)
router.register(
    "transfer-discrepancy-source-stock-verifications",
    TransferDiscrepancySourceStockVerificationViewSet,
    basename="transfer-discrepancy-source-stock-verification",
)
router.register(
    "transfer-discrepancy-transit-investigations",
    TransferDiscrepancyTransitInvestigationViewSet,
    basename="transfer-discrepancy-transit-investigation",
)

me_branch_memberships = CurrentUserBranchMembershipsView.as_view()
auth_session = AuthSessionView.as_view()
auth_login = AuthLoginView.as_view()
auth_logout = AuthLogoutView.as_view()
current_events = AuditLogViewSet.as_view({"get": "current"})
