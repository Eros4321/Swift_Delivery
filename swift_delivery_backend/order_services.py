import secrets

from django.db import IntegrityError, transaction


ORDER_ID_MINIMUM = 10_000_000
ORDER_ID_RANGE = 90_000_000
ORDER_ID_GENERATION_ATTEMPTS = 10


class OrderIdGenerationError(RuntimeError):
    pass


def generate_order_id():
    """Return a cryptographically secure eight-digit numeric order ID."""
    return str(ORDER_ID_MINIMUM + secrets.randbelow(ORDER_ID_RANGE))


def create_order_with_generated_id(*, order_model, **order_data):
    """Create an order, retrying only when its generated public ID collides."""
    for _ in range(ORDER_ID_GENERATION_ATTEMPTS):
        candidate = generate_order_id()
        try:
            # The savepoint keeps the surrounding order transaction usable when
            # the database rejects a duplicate candidate.
            with transaction.atomic():
                return order_model.objects.create(
                    order_id=candidate,
                    **order_data,
                )
        except IntegrityError:
            if order_model.objects.filter(order_id=candidate).exists():
                continue
            raise

    raise OrderIdGenerationError(
        'Unable to allocate a unique order ID. Please try again.'
    )
