# Generated by Django 5.1.1 on 2024-10-06 14:35

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cafeteria',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='cafeteria_images/'),
        ),
    ]
