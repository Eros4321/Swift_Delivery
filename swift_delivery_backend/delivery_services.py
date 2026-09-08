from decimal import Decimal, InvalidOperation


MONEY_QUANTUM = Decimal('0.01')


class DeliveryFeeConfigurationError(ValueError):
    pass


def normalize_money(value):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DeliveryFeeConfigurationError(
            'University delivery fee configuration is invalid.'
        ) from error
    if not amount.is_finite() or amount < Decimal('0.00'):
        raise DeliveryFeeConfigurationError(
            'University delivery fee configuration is invalid.'
        )
    return amount.quantize(MONEY_QUANTUM)


def calculate_cart_subtotal(cart):
    subtotal = sum(
        (
            item.menu_item.price * item.quantity
            for item in cart.cart_items.all()
        ),
        Decimal('0.00'),
    )
    return subtotal.quantize(MONEY_QUANTUM)


def calculate_order_items_subtotal(order_items):
    subtotal = sum(
        (
            menu_item.price * quantity
            for menu_item, quantity in order_items
        ),
        Decimal('0.00'),
    )
    return subtotal.quantize(MONEY_QUANTUM)


def calculate_delivery_fee(*, university, cart, delivery_point):
    # cart and delivery_point are deliberate extension points for future
    # distance-, item-, or vendor-specific pricing.
    del cart, delivery_point
    return normalize_money(university.delivery_fee)
