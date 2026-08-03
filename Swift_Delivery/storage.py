from pathlib import PurePosixPath

import cloudinary
from cloudinary import CloudinaryResource, api, uploader
from cloudinary.exceptions import NotFound
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible
import requests


@deconstructible
class CloudinaryMediaStorage(Storage):
    """Django media storage backed by Cloudinary's official Python SDK."""

    delivery_format_aliases = {
        'jpe': 'jpg',
        'jpeg': 'jpg',
        'jfif': 'jpg',
    }

    def __init__(self, folder='', download_timeout=30):
        self.folder = folder.strip('/')
        self.download_timeout = download_timeout

        config = cloudinary.config(secure=True)
        if not all((config.cloud_name, config.api_key, config.api_secret)):
            raise ImproperlyConfigured(
                'Cloudinary media storage requires a valid CLOUDINARY_URL.'
            )

    @staticmethod
    def _normalise_name(name):
        return str(PurePosixPath(str(name).replace('\\', '/'))).lstrip('/')

    @staticmethod
    def _split_name(name):
        path = PurePosixPath(name)
        return str(path.with_suffix('')), path.suffix.lstrip('.') or None

    def _public_id(self, name):
        normalised_name = self._normalise_name(name)
        name_without_extension, _ = self._split_name(normalised_name)
        if self.folder:
            return f'{self.folder}/{name_without_extension}'
        return name_without_extension

    def _resource(self, name):
        return api.resource(
            self._public_id(name),
            resource_type='image',
            type='upload',
        )

    def canonical_name(self, name):
        """Return the database name that matches an existing Cloudinary asset."""
        self._resource(name)
        return self._normalise_name(name)

    def _open(self, name, mode='rb'):
        if mode not in ('r', 'rb'):
            raise ValueError('Cloudinary media files can only be opened for reading.')

        response = requests.get(
            self.url(name),
            timeout=self.download_timeout,
        )
        response.raise_for_status()
        return ContentFile(response.content, name=self._normalise_name(name))

    def _save(self, name, content):
        if hasattr(content, 'seek'):
            content.seek(0)

        uploader.upload(
            content,
            public_id=self._public_id(name),
            resource_type='image',
            type='upload',
            overwrite=False,
        )
        return self._normalise_name(name)

    def delete(self, name):
        if not name:
            return
        uploader.destroy(
            self._public_id(name),
            resource_type='image',
            type='upload',
            invalidate=True,
        )

    def exists(self, name):
        try:
            self._resource(name)
        except NotFound:
            return False
        return True

    def size(self, name):
        return self._resource(name)['bytes']

    def url(self, name):
        normalised_name = self._normalise_name(name)
        public_id, image_format = self._split_name(normalised_name)
        image_format = self.delivery_format_aliases.get(
            image_format,
            image_format,
        )
        if self.folder:
            public_id = f'{self.folder}/{public_id}'
        return CloudinaryResource(
            public_id=public_id,
            format=image_format,
            resource_type='image',
            type='upload',
        ).build_url(secure=True)
