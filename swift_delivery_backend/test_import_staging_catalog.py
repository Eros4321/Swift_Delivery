import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import CafeteriaCategory, MenuItem, University, Vendor


class ImportStagingCatalogTests(TestCase):
    enabled_environment = {
        'DJANGO_CATALOG_IMPORT_ENABLED': 'True',
        'DEPLOYMENT_ENVIRONMENT': 'staging',
    }

    @patch.dict(
        os.environ,
        {
            'DJANGO_CATALOG_IMPORT_ENABLED': 'False',
            'DEPLOYMENT_ENVIRONMENT': 'staging',
        },
        clear=False,
    )
    def test_disabled_import_does_not_create_catalog_data(self):
        output = StringIO()

        call_command('import_staging_catalog', stdout=output)

        self.assertFalse(University.objects.exists())
        self.assertFalse(Vendor.objects.exists())
        self.assertIn('import is disabled', output.getvalue())

    @patch.dict(
        os.environ,
        {
            'DJANGO_CATALOG_IMPORT_ENABLED': 'True',
            'DEPLOYMENT_ENVIRONMENT': 'production',
        },
        clear=False,
    )
    def test_import_refuses_to_run_outside_staging(self):
        with self.assertRaisesMessage(
            CommandError,
            'DEPLOYMENT_ENVIRONMENT is not set to staging',
        ):
            call_command('import_staging_catalog')

    @patch.dict(os.environ, enabled_environment, clear=False)
    def test_import_loads_only_the_public_catalog(self):
        call_command('import_staging_catalog')

        self.assertEqual(University.objects.count(), 1)
        self.assertEqual(Vendor.objects.count(), 7)
        self.assertEqual(CafeteriaCategory.objects.count(), 3)
        self.assertEqual(MenuItem.objects.count(), 15)
        self.assertEqual(MenuItem.vendors.through.objects.count(), 15)
        self.assertEqual(
            list(MenuItem.objects.get(pk=1).vendors.values_list('pk', flat=True)),
            [1],
        )

    @patch.dict(os.environ, enabled_environment, clear=False)
    def test_import_does_not_overwrite_an_existing_catalog(self):
        University.objects.create(
            name='Existing staging university',
            latitude='7.000000',
            longitude='4.000000',
        )
        output = StringIO()

        call_command('import_staging_catalog', stdout=output)

        self.assertEqual(University.objects.count(), 1)
        self.assertFalse(Vendor.objects.exists())
        self.assertIn('import skipped', output.getvalue())
