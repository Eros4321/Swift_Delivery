import secrets

from django.db import migrations, models

import swift_delivery_backend.order_services


ORDER_ID_MINIMUM = 10_000_000
ORDER_ID_RANGE = 90_000_000


def backfill_order_ids(apps, schema_editor):
    Order = apps.get_model('swift_delivery_backend', 'Order')
    database_alias = schema_editor.connection.alias
    assigned_ids = set(
        Order.objects.using(database_alias)
        .exclude(order_id__isnull=True)
        .values_list('order_id', flat=True)
    )

    orders = Order.objects.using(database_alias).filter(order_id__isnull=True)
    for order in orders.iterator():
        while True:
            candidate = str(
                ORDER_ID_MINIMUM + secrets.randbelow(ORDER_ID_RANGE)
            )
            if candidate not in assigned_ids:
                break
        Order.objects.using(database_alias).filter(pk=order.pk).update(
            order_id=candidate,
        )
        assigned_ids.add(candidate)


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0028_delivery_fees_and_order_totals'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='order_id',
            field=models.CharField(
                editable=False,
                max_length=8,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_order_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='order_id',
            field=models.CharField(
                db_index=True,
                default=swift_delivery_backend.order_services.generate_order_id,
                editable=False,
                max_length=8,
                unique=True,
            ),
        ),
    ]
