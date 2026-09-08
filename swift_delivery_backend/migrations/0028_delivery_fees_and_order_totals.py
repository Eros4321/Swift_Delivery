from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def backfill_order_totals(apps, schema_editor):
    Order = apps.get_model('swift_delivery_backend', 'Order')
    OrderItem = apps.get_model('swift_delivery_backend', 'OrderItem')

    totals_by_order_id = {}
    order_items = OrderItem.objects.select_related('menu_item').all().iterator()
    for order_item in order_items:
        item_total = order_item.menu_item.price * order_item.quantity
        totals_by_order_id[order_item.order_id] = (
            totals_by_order_id.get(order_item.order_id, Decimal('0.00'))
            + item_total
        )

    for order in Order.objects.all().iterator():
        historical_total = totals_by_order_id.get(order.id, Decimal('0.00'))
        Order.objects.filter(pk=order.pk).update(
            subtotal_amount=historical_total,
            delivery_fee=Decimal('0.00'),
            total_amount=historical_total,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0027_university_google_place_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='university',
            name='delivery_fee',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('500.00'),
                max_digits=10,
                validators=[
                    django.core.validators.MinValueValidator(Decimal('0.00')),
                ],
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='subtotal_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_fee',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='total_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                max_digits=12,
            ),
        ),
        migrations.RunPython(backfill_order_totals, migrations.RunPython.noop),
    ]
