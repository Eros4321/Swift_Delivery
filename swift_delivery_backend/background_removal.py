import logging
from io import BytesIO
from pathlib import Path

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


logger = logging.getLogger(__name__)

JPEG_FILE_EXTENSIONS = {'.jfif', '.jfi', '.jpe', '.jpeg', '.jpg'}
JFIF_FILE_EXTENSIONS = {'.jfif', '.jfi', '.jpe'}


class BackgroundRemovalError(Exception):
    """Raised when a menu image cannot be processed safely."""


def _error_message_for_status(status_code):
    if status_code == 400:
        return (
            'The image could not be processed. Please upload a clear '
            'JPG/JPEG (including JFIF), PNG, or WebP image.'
        )
    if status_code in (401, 403):
        return 'Background removal is not configured correctly.'
    if status_code == 402:
        return 'The background removal quota has been exhausted.'
    if status_code == 429:
        return 'Background removal is busy. Please try again shortly.'
    return 'Background removal is temporarily unavailable. Please try again.'


def _error_code_from_response(response):
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    errors = payload.get('errors')
    if not isinstance(errors, list) or not errors:
        return None

    first_error = errors[0]
    if not isinstance(first_error, dict):
        return None

    return first_error.get('code')


def _error_message_for_response(response):
    if response.status_code == 400 and _error_code_from_response(response) == 'unknown_foreground':
        return (
            'No clear food item could be isolated from this photo. '
            'Use an image where the food is centered and separated from hands, packaging, '
            'and other objects.'
        )
    return _error_message_for_status(response.status_code)


def _prepare_provider_upload(image, original_name, content_type):
    suffix = Path(original_name).suffix.lower()
    provider_name = original_name

    if suffix not in JPEG_FILE_EXTENSIONS:
        return provider_name, image, content_type

    provider_name = f'{Path(original_name).stem}.jpg'
    content_type = 'image/jpeg'

    if suffix not in JFIF_FILE_EXTENSIONS:
        return provider_name, image, content_type

    normalized_file = BytesIO()
    try:
        image.seek(0)
        with Image.open(image) as source_image:
            source_image.load()
            with ImageOps.exif_transpose(source_image).convert('RGB') as normalized_image:
                normalized_image.save(
                    normalized_file,
                    format='JPEG',
                    quality=95,
                    progressive=False,
                )
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        normalized_file.close()
        raise BackgroundRemovalError(
            'The JFIF image could not be decoded. Please upload a valid image.'
        ) from exc
    finally:
        try:
            image.seek(0)
        except (AttributeError, OSError):
            pass

    normalized_file.seek(0)
    return provider_name, normalized_file, content_type


def remove_image_background(image):
    """Return a transparent PNG produced from an uploaded menu image."""
    api_key = settings.REMOVE_BG_API_KEY
    if not api_key:
        raise BackgroundRemovalError(
            'Background removal is not configured. Set REMOVE_BG_API_KEY on the server.'
        )

    original_name = Path(image.name).name
    content_type = getattr(image, 'content_type', None) or 'application/octet-stream'
    provider_name, provider_image, content_type = _prepare_provider_upload(
        image,
        original_name,
        content_type,
    )

    try:
        image.seek(0)
        response = requests.post(
            settings.REMOVE_BG_API_URL,
            headers={'X-Api-Key': api_key},
            files={'image_file': (provider_name, provider_image, content_type)},
            data={'size': 'auto', 'format': 'png', 'type': 'product'},
            timeout=settings.REMOVE_BG_API_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning('Background removal request failed: %s', exc)
        raise BackgroundRemovalError(
            'Background removal is temporarily unavailable. Please try again.'
        ) from exc
    finally:
        try:
            image.seek(0)
        except (AttributeError, OSError):
            pass
        if provider_image is not image:
            provider_image.close()

    if response.status_code != 200:
        logger.warning(
            'Background removal returned HTTP %s: %.500s',
            response.status_code,
            response.text,
        )
        raise BackgroundRemovalError(_error_message_for_response(response))

    if not response.content:
        raise BackgroundRemovalError(
            'Background removal returned an empty image. Please try again.'
        )

    stem = Path(original_name).stem[:80] or 'menu-item'
    return ContentFile(response.content, name=f'{stem}-no-bg.png')
