from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.api import router
from config.views import health_check
from operations.scanner_views import (
    ScannerControlCartItemsView,
    ScannerControlFinishView,
    ScannerControlPrintLabelView,
    ScannerControlTargetView,
    ScannerLocationContentsView,
    ScannerPickingPickView,
    ScannerPickingPrepareView,
    ScannerPickingScanView,
    ScannerProductLookupView,
    ScannerQuickTransferView,
    ScannerSessionCurrentView,
    ScannerSessionEndView,
    ScannerSessionStartView,
)


urlpatterns = [
    path("api/health/", health_check, name="health-check"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/scanner/session/start/", ScannerSessionStartView.as_view(), name="scanner-session-start"),
    path("api/scanner/session/current/", ScannerSessionCurrentView.as_view(), name="scanner-session-current"),
    path("api/scanner/session/end/", ScannerSessionEndView.as_view(), name="scanner-session-end"),
    path("api/scanner/picking/scan/", ScannerPickingScanView.as_view(), name="scanner-picking-scan"),
    path("api/scanner/picking/pick/", ScannerPickingPickView.as_view(), name="scanner-picking-pick"),
    path("api/scanner/picking/prepare/", ScannerPickingPrepareView.as_view(), name="scanner-picking-prepare"),
    path("api/scanner/control/cart-items/", ScannerControlCartItemsView.as_view(), name="scanner-control-cart-items"),
    path("api/scanner/control/target/", ScannerControlTargetView.as_view(), name="scanner-control-target"),
    path("api/scanner/control/print-label/", ScannerControlPrintLabelView.as_view(), name="scanner-control-print-label"),
    path("api/scanner/control/finish/", ScannerControlFinishView.as_view(), name="scanner-control-finish"),
    path("api/scanner/products/lookup/", ScannerProductLookupView.as_view(), name="scanner-product-lookup"),
    path("api/scanner/locations/contents/", ScannerLocationContentsView.as_view(), name="scanner-location-contents"),
    path("api/scanner/quick-transfer/", ScannerQuickTransferView.as_view(), name="scanner-quick-transfer"),
    path("api/", include(router.urls)),
    path("admin/", admin.site.urls),
]
