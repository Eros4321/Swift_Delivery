import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction


class Command(BaseCommand):
    help = (
        'Create or restore a deployment administrator from environment '
        'variables when DJANGO_ADMIN_SETUP_ENABLED is true.'
    )

    truthy_values = {'1', 'true', 'yes'}

    def handle(self, *args, **options):
        enabled = os.environ.get(
            'DJANGO_ADMIN_SETUP_ENABLED',
            'False',
        ).lower() in self.truthy_values

        if not enabled:
            self.stdout.write('Deployment administrator setup is disabled.')
            return

        username = os.environ.get('DJANGO_ADMIN_USERNAME', '').strip()
        email = os.environ.get('DJANGO_ADMIN_EMAIL', '').strip()
        password = os.environ.get('DJANGO_ADMIN_PASSWORD', '')

        missing = [
            name
            for name, value in (
                ('DJANGO_ADMIN_USERNAME', username),
                ('DJANGO_ADMIN_EMAIL', email),
                ('DJANGO_ADMIN_PASSWORD', password),
            )
            if not value
        ]
        if missing:
            raise CommandError(
                'Administrator setup is enabled but required variables are '
                f'missing: {", ".join(missing)}.'
            )

        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError('DJANGO_ADMIN_EMAIL is not a valid email.') from exc

        user_model = get_user_model()

        with transaction.atomic():
            user, created = user_model.objects.get_or_create(
                **{
                    user_model.USERNAME_FIELD: username,
                    'defaults': {'email': email},
                }
            )

            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                raise CommandError('DJANGO_ADMIN_PASSWORD: ' + ' '.join(exc.messages)) from exc

            user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            user.set_password(password)
            user.save()

        action = 'created' if created else 'restored'
        self.stdout.write(
            self.style.SUCCESS(
                f'Deployment administrator {action} successfully.'
            )
        )
