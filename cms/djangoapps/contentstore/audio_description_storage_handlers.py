"""
Storage handlers for audio description (AD) files.

Files are saved via edx-val's Django storage abstraction
(FileSystemStorage locally, S3Boto3Storage in production).
edx-val owns the file record and URL generation; this module
handles validation, sanitisation, and delegates to edx-val's API.
"""

import logging
import os
import re

from django.conf import settings
from django.core.files.base import ContentFile

try:
    from edxval.api import (
        create_or_update_video_audio_description,
        delete_video_audio_description,
        get_video_audio_description_url,
    )
except ImportError:
    create_or_update_video_audio_description = None
    delete_video_audio_description = None
    get_video_audio_description_url = None

log = logging.getLogger(__name__)


class AudioDescriptionUploadError(Exception):
    """Raised when an AD upload request is invalid or cannot be fulfilled."""


_CONTENT_TYPE_TO_FORMAT = {
    'audio/mpeg': 'mp3',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/wav': 'wav',
    'audio/aac': 'aac',
}

ALLOWED_FORMATS = {'mp3', 'm4a', 'wav', 'aac'}


def _sanitize_file_name(file_name):
    """
    Strip path components and any characters outside a safe subset.
    """
    base = os.path.basename(file_name or '')
    if not base:
        raise AudioDescriptionUploadError('file_name is required')

    try:
        base.encode('ascii')
    except UnicodeEncodeError as exc:
        raise AudioDescriptionUploadError(
            f'The file name for {base} must contain only ASCII characters.'
        ) from exc

    return re.sub(r'[^A-Za-z0-9._-]', '_', base)


def _resolve_format(content_type, file_name):
    """
    Pick the canonical file_format string for the given content type,
    falling back to the file extension.
    """
    fmt = _CONTENT_TYPE_TO_FORMAT.get(content_type)
    if fmt:
        return fmt
    ext = os.path.splitext(file_name or '')[1].lstrip('.').lower()
    if ext in ALLOWED_FORMATS:
        return ext
    raise AudioDescriptionUploadError(
        f'Unsupported audio description content type: {content_type}'
    )


def upload_audio_description(edx_video_id, file_name, content_type, file_data):
    """
    Validate and save an audio description file via edx-val.

    Returns the storage URL for the saved file.
    """
    if not edx_video_id:
        raise AudioDescriptionUploadError('edx_video_id is required')

    safe_name = _sanitize_file_name(file_name)
    file_format = _resolve_format(content_type, safe_name)

    max_bytes = getattr(settings, 'VIDEO_AUDIO_DESCRIPTION_SETTINGS', {}).get(
        'VIDEO_AUDIO_DESCRIPTION_MAX_BYTES', 0
    )
    if max_bytes and hasattr(file_data, 'size') and file_data.size > max_bytes:
        raise AudioDescriptionUploadError(
            f'Audio description file exceeds maximum allowed size of {max_bytes} bytes'
        )

    content = file_data if isinstance(file_data, ContentFile) else ContentFile(file_data.read())

    return create_or_update_video_audio_description(
        video_id=edx_video_id,
        metadata={'file_name': safe_name, 'file_format': file_format},
        file_data=content,
    )


def delete_audio_description(edx_video_id):
    """
    Delete the AD record and file from storage.
    Returns True if a record was deleted.
    """
    return delete_video_audio_description(edx_video_id)


def get_audio_description_url(edx_video_id):
    """
    Return the download URL for the audio description, or None.
    """
    return get_video_audio_description_url(edx_video_id)
