from django.db import models
from django.core.exceptions import ValidationError


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Branch(TimestampedModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Location(TimestampedModel):
    class LocationType(models.TextChoices):
        STORAGE = "storage", "Storage"
        PICKING = "picking", "Picking"
        RECEIVING = "receiving", "Receiving"
        SHIPPING = "shipping", "Shipping"
        RETURNS = "returns", "Returns"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="locations")
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255, blank=True)
    location_type = models.CharField(
        max_length=32,
        choices=LocationType.choices,
        default=LocationType.STORAGE,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["branch__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="unique_location_code_per_branch"),
        ]
        indexes = [
            models.Index(fields=["branch", "code"]),
            models.Index(fields=["location_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.branch.code} / {self.code}"


class Product(TimestampedModel):
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=128, unique=True, blank=True, null=True)
    unit_of_measure = models.CharField(max_length=32, default="pcs")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sku"]
        indexes = [
            models.Index(fields=["sku"]),
            models.Index(fields=["barcode"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"


class InventoryItem(TimestampedModel):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="inventory_items")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="inventory_items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="inventory_items")
    quantity_on_hand = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantity_reserved = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["branch__code", "location__code", "product__sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "location", "product"],
                name="unique_inventory_product_per_location",
            ),
            models.CheckConstraint(
                check=models.Q(quantity_on_hand__gte=0),
                name="inventory_quantity_on_hand_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(quantity_reserved__gte=0),
                name="inventory_quantity_reserved_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["branch"]),
            models.Index(fields=["location"]),
            models.Index(fields=["product"]),
            models.Index(fields=["branch", "product"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.sku} at {self.location.code}: {self.quantity_on_hand}"

    def clean(self):
        super().clean()
        if self.branch_id and self.location_id and self.location.branch_id != self.branch_id:
            raise ValidationError({"location": "Inventory location must belong to the inventory branch."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
