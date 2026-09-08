import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class EnsureDeploymentAdminTests(TestCase):
    environment = {
        'DJANGO_ADMIN_SETUP_ENABLED': 'True',
        'DJANGO_ADMIN_USERNAME': 'staging-admin',
        'DJANGO_ADMIN_EMAIL': 'staging-admin@example.com',
        'DJANGO_ADMIN_PASSWORD': 'Staging-only-password-4827',
    }

    @patch.dict(os.environ, {'DJANGO_ADMIN_SETUP_ENABLED': 'False'}, clear=False)
    def test_disabled_setup_does_not_create_a_user(self):
        output = StringIO()

        call_command('ensure_deployment_admin', stdout=output)

        self.assertFalse(get_user_model().objects.exists())
        self.assertIn('setup is disabled', output.getvalue())

    @patch.dict(os.environ, environment, clear=False)
    def test_enabled_setup_creates_a_superuser(self):
        call_command('ensure_deployment_admin')

        user = get_user_model().objects.get(username='staging-admin')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password('Staging-only-password-4827'))

    @patch.dict(os.environ, environment, clear=False)
    def test_enabled_setup_restores_existing_user_permissions_and_password(self):
        user = get_user_model().objects.create_user(
            username='staging-admin',
            email='old@example.com',
            password='old-password',
            is_staff=False,
            is_superuser=False,
            is_active=False,
        )

        call_command('ensure_deployment_admin')

        user.refresh_from_db()
        self.assertEqual(user.email, 'staging-admin@example.com')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password('Staging-only-password-4827'))

    @patch.dict(
        os.environ,
        {
            'DJANGO_ADMIN_SETUP_ENABLED': 'True',
            'DJANGO_ADMIN_USERNAME': '',
            'DJANGO_ADMIN_EMAIL': '',
            'DJANGO_ADMIN_PASSWORD': '',
        },
        clear=False,
    )
    def test_enabled_setup_rejects_missing_credentials(self):
        with self.assertRaisesMessage(
            CommandError,
            'required variables are missing',
        ):
            call_command('ensure_deployment_admin')
