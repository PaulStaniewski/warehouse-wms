from decimal import Decimal, InvalidOperation


DISCRETE_UNIT_CODES = frozenset({"pc", "pcs", "piece", "pieces", "ea", "each", "unit", "units"})


def product_uses_discrete_quantities(product) -> bool:
    return str(product.unit_of_measure or "").strip().lower() in DISCRETE_UNIT_CODES


def product_quantity_error(product, quantity) -> str | None:
    try:
        value = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        return "Quantity must be a valid number."
    if product_uses_discrete_quantities(product) and value != value.to_integral_value():
        return "This product must be handled in whole units."
    return None


def quantity_has_fraction(quantity) -> bool:
    value = Decimal(str(quantity))
    return value != value.to_integral_value()