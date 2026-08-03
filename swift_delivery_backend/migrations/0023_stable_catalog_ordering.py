from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0022_savedcartnote'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='vendor',
            options={'ordering': ['id']},
        ),
        migrations.AlterModelOptions(
            name='cafeteriacategory',
            options={
                'ordering': ['id'],
                'verbose_name_plural': 'cafeteria categories',
            },
        ),
        migrations.AlterModelOptions(
            name='menuitem',
            options={'ordering': ['category_id', 'id']},
        ),
    ]
