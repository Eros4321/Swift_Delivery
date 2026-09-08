from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('swift_delivery_backend', '0026_university_delivery_area'),
    ]

    operations = [
        migrations.AddField(
            model_name='university',
            name='google_place_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                null=True,
            ),
        ),
    ]
