from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0023_stable_catalog_ordering'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='logo',
            field=models.ImageField(blank=True, null=True, upload_to='vendor_logos/'),
        ),
    ]
