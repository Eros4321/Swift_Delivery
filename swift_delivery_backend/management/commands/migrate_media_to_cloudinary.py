from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from Swift_Delivery.storage import CloudinaryMediaStorage
from swift_delivery_backend.models import MenuItem, Vendor


class Command(BaseCommand):
    help = (
        'Preview or upload database-referenced local media files to Cloudinary. '
        'The command is a dry run unless --execute is supplied.'
    )

    media_fields = (
        (Vendor, 'image'),
        (Vendor, 'logo'),
        (MenuItem, 'image'),
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Upload files and update image names in the database.',
        )
        parser.add_argument(
            '--folder',
            default=settings.CLOUDINARY_FOLDER,
            help='Cloudinary folder prefix (defaults to CLOUDINARY_FOLDER).',
        )

    def handle(self, *args, **options):
        execute = options['execute']
        media_root = Path(settings.MEDIA_ROOT)
        references = list(self._references())

        if not references:
            self.stdout.write('No database-referenced media files were found.')
            return

        if not execute:
            self._preview(references, media_root, options['folder'])
            return

        if not settings.CLOUDINARY_URL:
            raise CommandError('CLOUDINARY_URL is not configured.')

        storage = CloudinaryMediaStorage(folder=options['folder'])
        migrated_names = {}
        uploaded = 0
        reused = 0
        missing = 0

        for instance, field_name, current_name in references:
            source_path = media_root / Path(current_name)
            if not source_path.is_file():
                missing += 1
                self.stderr.write(f'Missing local file: {source_path}')
                continue

            if current_name in migrated_names:
                cloudinary_name = migrated_names[current_name]
                reused += 1
            elif storage.exists(current_name):
                cloudinary_name = storage.canonical_name(current_name)
                migrated_names[current_name] = cloudinary_name
                reused += 1
            else:
                with source_path.open('rb') as source_file:
                    cloudinary_name = storage.save(
                        current_name,
                        File(source_file, name=Path(current_name).name),
                    )
                migrated_names[current_name] = cloudinary_name
                uploaded += 1

            if cloudinary_name != current_name:
                setattr(instance, field_name, cloudinary_name)
                instance.save(update_fields=[field_name])

            self.stdout.write(
                self.style.SUCCESS(
                    f'{instance._meta.label} {instance.pk}.{field_name}: '
                    f'{current_name} -> {cloudinary_name}'
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Completed: {uploaded} uploaded, {reused} reused, '
                f'{missing} missing.'
            )
        )

    def _references(self):
        for model, field_name in self.media_fields:
            queryset = model.objects.exclude(**{field_name: ''}).exclude(
                **{f'{field_name}__isnull': True}
            )
            for instance in queryset.iterator():
                field_file = getattr(instance, field_name)
                if field_file and field_file.name:
                    yield instance, field_name, field_file.name

    def _preview(self, references, media_root, folder):
        present = 0
        missing = 0
        for instance, field_name, current_name in references:
            source_path = media_root / Path(current_name)
            status = 'ready' if source_path.is_file() else 'missing'
            present += status == 'ready'
            missing += status == 'missing'
            self.stdout.write(
                f'[{status}] {instance._meta.label} {instance.pk}.{field_name}: '
                f'{current_name}'
            )

        self.stdout.write(
            f'Dry run only. Target folder: {folder or "(root)"}. '
            f'{present} ready, {missing} missing. '
            'Run again with --execute to upload.'
        )
