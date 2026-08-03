from unittest.mock import Mock, patch

from cloudinary.exceptions import NotFound
from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from Swift_Delivery.storage import CloudinaryMediaStorage


@patch('Swift_Delivery.storage.cloudinary.config')
class CloudinaryMediaStorageTests(SimpleTestCase):
    @staticmethod
    def configured_cloudinary(config):
        config.return_value = Mock(
            cloud_name='swift-delivery',
            api_key='test-key',
            api_secret='test-secret',
        )

    @patch('Swift_Delivery.storage.uploader.upload')
    def test_save_preserves_database_name_and_cloudinary_public_id(
        self,
        upload,
        config,
    ):
        self.configured_cloudinary(config)
        upload.return_value = {
            'public_id': 'swift-delivery/staging/menu_images/eazi2',
            'format': 'jpg',
        }
        storage = CloudinaryMediaStorage(folder='swift-delivery/staging')

        saved_name = storage._save(
            'menu_images/eazi2.jfif',
            ContentFile(b'image-bytes'),
        )

        self.assertEqual(saved_name, 'menu_images/eazi2.jfif')
        self.assertEqual(
            upload.call_args.kwargs['public_id'],
            'swift-delivery/staging/menu_images/eazi2',
        )
        self.assertFalse(upload.call_args.kwargs['overwrite'])

    @patch('Swift_Delivery.storage.CloudinaryResource')
    def test_jfif_delivery_uses_cloudinary_jpg_format(
        self,
        cloudinary_resource,
        config,
    ):
        self.configured_cloudinary(config)
        storage = CloudinaryMediaStorage(folder='swift-delivery/staging')

        storage.url('cafeteria_images/eazi2.jfif')

        cloudinary_resource.assert_called_once_with(
            public_id='swift-delivery/staging/cafeteria_images/eazi2',
            format='jpg',
            resource_type='image',
            type='upload',
        )

    @patch('Swift_Delivery.storage.api.resource')
    def test_exists_returns_false_only_for_not_found(self, resource, config):
        self.configured_cloudinary(config)
        resource.side_effect = NotFound('missing')
        storage = CloudinaryMediaStorage(folder='swift-delivery/production')

        self.assertFalse(storage.exists('vendor_logos/swift.png'))

    @patch('Swift_Delivery.storage.CloudinaryResource')
    def test_url_is_secure_and_contains_environment_folder(
        self,
        cloudinary_resource,
        config,
    ):
        self.configured_cloudinary(config)
        cloudinary_resource.return_value.build_url.return_value = (
            'https://res.cloudinary.com/swift-delivery/image/upload/'
            'swift-delivery/production/menu_images/jollof-no-bg.png'
        )
        storage = CloudinaryMediaStorage(folder='swift-delivery/production')

        url = storage.url('menu_images/jollof-no-bg.png')

        self.assertTrue(url.startswith('https://'))
        cloudinary_resource.assert_called_once_with(
            public_id='swift-delivery/production/menu_images/jollof-no-bg',
            format='png',
            resource_type='image',
            type='upload',
        )
        cloudinary_resource.return_value.build_url.assert_called_once_with(
            secure=True
        )
