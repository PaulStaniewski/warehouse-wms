from django.contrib import admin

from warehouse.models import Branch, InventoryItem, Location, Product


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "city", "country", "is_active", "updated_at"]
    list_filter = ["is_active", "country"]
    search_fields = ["code", "name", "city"]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ["code", "branch", "location_type", "is_active", "updated_at"]
    list_filter = ["branch", "location_type", "is_active"]
    search_fields = ["code", "name", "branch__code", "branch__name"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["sku", "name", "barcode", "unit_of_measure", "is_active", "updated_at"]
    list_filter = ["is_active", "unit_of_measure"]
    search_fields = ["sku", "name", "barcode"]


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = [
        "product",
        "branch",
        "location",
        "quantity_on_hand",
        "quantity_reserved",
        "updated_at",
    ]
    list_filter = ["branch", "location"]
    search_fields = ["product__sku", "product__name", "location__code", "branch__code"]
