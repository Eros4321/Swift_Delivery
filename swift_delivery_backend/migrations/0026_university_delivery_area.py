import django.contrib.gis.db.models.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0025_separate_vendor_and_delivery_notes'),
    ]

    operations = [
        migrations.AddField(
            model_name='university',
            name='delivery_area',
            field=django.contrib.gis.db.models.fields.MultiPolygonField(
                blank=True,
                null=True,
                srid=4326,
            ),
        ),
    ]
