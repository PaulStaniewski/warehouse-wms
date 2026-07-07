from rest_framework.routers import DefaultRouter

from operations.viewsets import (
    AuditLogViewSet,
    DeliveryRouteViewSet,
    OrderLineViewSet,
    OrderViewSet,
    PickingTaskViewSet,
    ReturnBatchViewSet,
    ReturnLineViewSet,
    RouteRunViewSet,
    StockMovementViewSet,
    TransferDiscrepancyViewSet,
)
from warehouse.viewsets import BranchViewSet, InventoryItemViewSet, LocationViewSet, ProductViewSet


router = DefaultRouter()
router.register("branches", BranchViewSet, basename="branch")
router.register("locations", LocationViewSet, basename="location")
router.register("products", ProductViewSet, basename="product")
router.register("inventory-items", InventoryItemViewSet, basename="inventory-item")
router.register("delivery-routes", DeliveryRouteViewSet, basename="delivery-route")
router.register("route-runs", RouteRunViewSet, basename="route-run")
router.register("orders", OrderViewSet, basename="order")
router.register("order-lines", OrderLineViewSet, basename="order-line")
router.register("return-batches", ReturnBatchViewSet, basename="return-batch")
router.register("return-lines", ReturnLineViewSet, basename="return-line")
router.register("picking-tasks", PickingTaskViewSet, basename="picking-task")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
router.register("transfer-discrepancies", TransferDiscrepancyViewSet, basename="transfer-discrepancy")
