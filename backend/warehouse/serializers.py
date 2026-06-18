from rest_framework import serializers

from warehouse.models import Branch, InventoryItem, Location, Product


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id",
            "code",
            "name",
            "city",
            "country",
            "is_active",
            "created_at",
            "updated_at",
        ]


class LocationSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = Location
        fields = [
            "id",
            "branch",
            "branch_code",
            "code",
            "name",
            "location_type",
            "is_active",
            "created_at",
            "updated_at",
        ]


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "barcode",
            "unit_of_measure",
            "is_active",
            "created_at",
            "updated_at",
        ]


class InventoryItemSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "branch",
            "branch_code",
            "location",
            "location_code",
            "product",
            "product_sku",
            "quantity_on_hand",
            "quantity_reserved",
            "created_at",
            "updated_at",
        ]
