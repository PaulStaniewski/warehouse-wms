import django_filters
from rest_framework.viewsets import ReadOnlyModelViewSet

from warehouse.models import Branch, InventoryItem, Location, Product
from warehouse.serializers import (
    BranchSerializer,
    InventoryItemSerializer,
    LocationSerializer,
    ProductSerializer,
)


class BranchViewSet(ReadOnlyModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    filterset_fields = ["code", "city", "is_active"]
    search_fields = ["code", "name", "city", "country"]
    ordering_fields = ["code", "name", "city", "created_at", "updated_at"]


class BranchCodeFilterSet(django_filters.FilterSet):
    branch = django_filters.CharFilter(method="filter_branch")

    def filter_branch(self, queryset, name, value):
        if str(value).isdigit():
            return queryset.filter(branch_id=value)
        return queryset.filter(branch__code__iexact=value)


class LocationFilter(BranchCodeFilterSet):
    class Meta:
        model = Location
        fields = ["branch", "code", "location_type", "is_active"]


class LocationViewSet(ReadOnlyModelViewSet):
    queryset = Location.objects.select_related("branch")
    serializer_class = LocationSerializer
    filterset_class = LocationFilter
    search_fields = ["code", "name", "branch__code", "branch__name"]
    ordering_fields = ["branch__code", "code", "location_type", "created_at", "updated_at"]


class ProductViewSet(ReadOnlyModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    filterset_fields = ["sku", "barcode", "is_active"]
    search_fields = ["sku", "name", "barcode"]
    ordering_fields = ["sku", "name", "created_at", "updated_at"]


class InventoryItemFilter(BranchCodeFilterSet):
    class Meta:
        model = InventoryItem
        fields = ["branch", "location", "product"]


class InventoryItemViewSet(ReadOnlyModelViewSet):
    queryset = InventoryItem.objects.select_related("branch", "location", "product")
    serializer_class = InventoryItemSerializer
    filterset_class = InventoryItemFilter
    search_fields = ["branch__code", "location__code", "product__sku", "product__name"]
    ordering_fields = [
        "branch__code",
        "location__code",
        "product__sku",
        "quantity_on_hand",
        "updated_at",
    ]
