import json
import os
from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        'Load the bundled public catalog into an empty staging database when '
        'DJANGO_CATALOG_IMPORT_ENABLED is true.'
    )

    truthy_values = {'1', 'true', 'yes'}
    allowed_models = {
        'swift_delivery_backend.university',
        'swift_delivery_backend.vendor',
        'swift_delivery_backend.cafeteriacategory',
        'swift_delivery_backend.menuitem',
    }
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / 'fixtures'
        / 'staging_catalog.json'
    )

    def handle(self, *args, **options):
        enabled = os.environ.get(
            'DJANGO_CATALOG_IMPORT_ENABLED',
            'False',
        ).lower() in self.truthy_values

        if not enabled:
            self.stdout.write('Staging catalog import is disabled.')
            return

        environment = os.environ.get('DEPLOYMENT_ENVIRONMENT', '').strip().lower()
        if environment != 'staging':
            raise CommandError(
                'Catalog import is enabled but DEPLOYMENT_ENVIRONMENT is not '
                'set to staging. Refusing to import.'
            )

        fixture_models = self._fixture_models()
        unexpected_models = fixture_models - self.allowed_models
        missing_models = self.allowed_models - fixture_models
        if unexpected_models or missing_models:
            details = []
            if unexpected_models:
                details.append(
                    'unexpected models: ' + ', '.join(sorted(unexpected_models))
                )
            if missing_models:
                details.append(
                    'missing models: ' + ', '.join(sorted(missing_models))
                )
            raise CommandError(
                'The staging catalog fixture failed its model allowlist check ('
                + '; '.join(details)
                + ').'
            )

        catalog_models = [
            apps.get_model(model_label)
            for model_label in sorted(self.allowed_models)
        ]
        existing_counts = {
            model._meta.label: model.objects.count()
            for model in catalog_models
        }
        if any(existing_counts.values()):
            summary = ', '.join(
                f'{label}={count}'
                for label, count in existing_counts.items()
                if count
            )
            self.stdout.write(
                self.style.WARNING(
                    'Staging catalog import skipped because catalog data '
                    f'already exists ({summary}).'
                )
            )
            return

        with transaction.atomic():
            call_command(
                'loaddata',
                str(self.fixture_path),
                verbosity=0,
            )

        imported_counts = {
            model._meta.verbose_name_plural: model.objects.count()
            for model in catalog_models
        }
        summary = ', '.join(
            f'{name}={count}' for name, count in imported_counts.items()
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Staging catalog imported successfully ({summary}).'
            )
        )

    def _fixture_models(self):
        try:
            fixture = json.loads(self.fixture_path.read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise CommandError(
                f'Staging catalog fixture not found: {self.fixture_path}'
            ) from exc
        except json.JSONDecodeError as exc:
            raise CommandError('Staging catalog fixture is not valid JSON.') from exc

        if not isinstance(fixture, list):
            raise CommandError('Staging catalog fixture must contain a JSON list.')

        models = set()
        for index, record in enumerate(fixture):
            if not isinstance(record, dict) or not isinstance(
                record.get('model'), str
            ):
                raise CommandError(
                    f'Staging catalog fixture record {index} has no valid model.'
                )
            models.add(record['model'].lower())
        return models
