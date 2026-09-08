from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0010_rename_cafeteria_to_vendor'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Category',
            new_name='CafeteriaCategory',
        ),
        migrations.AlterModelOptions(
            name='cafeteriacategory',
            options={'verbose_name_plural': 'cafeteria categories'},
        ),
    ]
