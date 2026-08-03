from django.db import migrations


def normalize_nigerian_phone_number(phone_number):
    phone_number = ''.join(str(phone_number).split())
    if phone_number.startswith('0'):
        phone_number = f"+234{phone_number[1:]}"
    return phone_number


def normalize_customer_phone_numbers(apps, schema_editor):
    Customer = apps.get_model('swift_delivery_backend', 'Customer')
    for customer in Customer.objects.all():
        normalized_phone_number = normalize_nigerian_phone_number(customer.phone_number)
        if normalized_phone_number != customer.phone_number:
            if Customer.objects.exclude(pk=customer.pk).filter(phone_number=normalized_phone_number).exists():
                continue
            customer.phone_number = normalized_phone_number
            customer.save(update_fields=['phone_number'])


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0019_remove_order_campus_location_and_more'),
    ]

    operations = [
        migrations.RunPython(normalize_customer_phone_numbers, migrations.RunPython.noop),
    ]
