from rest_framework.routers import DefaultRouter

from operations.viewsets import (
    AuditLogViewSet,
    OrderLineViewSet,
    OrderViewSet,
    PickingTaskViewSet,
    ReturnBatchViewSet,
    ReturnLineViewSet,
    StockMovementViewSet,
)
from warehouse.viewsets import BranchViewSet, InventoryItemViewSet, LocationViewSet, ProductViewSet


router = DefaultRouter()
router.register("branches", BranchViewSet, basename="branch")
router.register("locations", LocationViewSet, basename="location")
router.register("products", ProductViewSet, basename="product")
router.register("inventory-items", InventoryItemViewSet, basename="inventory-item")
router.register("orders", OrderViewSet, basename="order")
router.register("order-lines", OrderLineViewSet, basename="order-line")
router.register("return-batches", ReturnBatchViewSet, basename="return-batch")
router.register("return-lines", ReturnLineViewSet, basename="return-line")
router.register("picking-tasks", PickingTaskViewSet, basename="picking-task")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
