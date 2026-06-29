from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.api import router
from config.views import health_check
from operations.scanner_views import (
    ScannerLocationContentsView,
    ScannerPickingScanView,
    ScannerProductLookupView,
    ScannerQuickTransferView,
)


urlpatterns = [
    path("api/health/", health_check, name="health-check"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/scanner/picking/scan/", ScannerPickingScanView.as_view(), name="scanner-picking-scan"),
    path("api/scanner/products/lookup/", ScannerProductLookupView.as_view(), name="scanner-product-lookup"),
    path("api/scanner/locations/contents/", ScannerLocationContentsView.as_view(), name="scanner-location-contents"),
    path("api/scanner/quick-transfer/", ScannerQuickTransferView.as_view(), name="scanner-quick-transfer"),
    path("api/", include(router.urls)),
    path("admin/", admin.site.urls),
]
