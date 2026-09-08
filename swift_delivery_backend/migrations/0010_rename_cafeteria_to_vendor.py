from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0009_cafeteria_vendor_type'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Cafeteria',
            new_name='Vendor',
        ),
        migrations.RenameField(
            model_name='menuitem',
            old_name='cafeteria',
            new_name='vendors',
        ),
        migrations.AlterField(
            model_name='menuitem',
            name='vendors',
            field=models.ManyToManyField(blank=True, related_name='menu_items', to='swift_delivery_backend.vendor'),
        ),
    ]
