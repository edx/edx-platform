"""
Storage handlers for audio description (AD) files.

Audio description files can be large (hundreds of MB), so the upload flow
follows the same direct-to-S3 pattern used for video uploads:

  1. The browser asks the CMS for a pre-signed PUT URL.
  2. The browser PUTs the file directly to S3 with that URL — bytes never
     traverse the Django worker.
  3. The browser POSTs back to the CMS to mark the upload complete.

edx-val stores only the metadata (filename, format, S3 key, status); all
boto3 / S3 interaction lives here so that edx-val remains free of any AWS
dependencies, mirroring how `video_storage_handlers.py` already works for
video files.
"""

import logging
import os
import re
from uuid import uuid4

from django.conf import settings
from edxval.api import (
    create_video_audio_description,
    delete_video_audio_description,
    get_video_audio_description,
    mark_video_audio_description_ready,
)

from .video_storage_handlers import get_s3_client

log = logging.getLogger(__name__)


def _rewrite_devstack_presigned_url(url):
    """
    Devstack: localstack uses an internal docker hostname that the browser
    can't resolve. Rewrite it to localhost so the browser can reach the
    pre-signed URL. localstack is permissive about the host header so the
    SigV4 signature still validates. No-op in production.
    """
    endpoint = getattr(settings, 'AWS_S3_ENDPOINT_URL', '') or ''
    if 'edx.devstack.localstack' in endpoint:
        return url.replace('edx.devstack.localstack', 'localhost')
    return url


class AudioDescriptionUploadError(Exception):
    """Raised when an AD upload request is invalid or cannot be fulfilled."""


# Maps allowed content types to canonical file_format strings stored in
# edx-val's `VideoAudioDescription.file_format` column.
_CONTENT_TYPE_TO_FORMAT = {
    'audio/mpeg': 'mp3',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/wav': 'wav',
    'audio/aac': 'aac',
}


def _get_settings():
    """Return the VIDEO_AUDIO_DESCRIPTION_SETTINGS dict."""
    return getattr(settings, 'VIDEO_AUDIO_DESCRIPTION_SETTINGS', {})


def _get_bucket_name():
    """
    Return the S3 bucket where AD files are stored. AD files share the
    VEM bucket with video files, namespaced by S3_KEY_PREFIX.
    """
    return settings.VIDEO_UPLOAD_PIPELINE.get('VEM_S3_BUCKET', '')


def _sanitize_file_name(file_name):
    """
    Strip path components and any characters outside a safe subset.
    Mirrors the ASCII-only check used by the video upload flow.
    """
    # Drop any directory components a client may have included.
    base = os.path.basename(file_name or '')
    if not base:
        raise AudioDescriptionUploadError('file_name is required')

    try:
        base.encode('ascii')
    except UnicodeEncodeError as exc:
        raise AudioDescriptionUploadError(
            f'The file name for {base} must contain only ASCII characters.'
        ) from exc

    # Replace anything that isn't a safe URL/key character with an underscore.
    return re.sub(r'[^A-Za-z0-9._-]', '_', base)


def _resolve_format(content_type, file_name):
    """
    Pick the canonical file_format string for the given content type,
    falling back to the file extension when the content type alone is
    ambiguous (e.g. audio/mp4 → m4a).
    """
    fmt = _CONTENT_TYPE_TO_FORMAT.get(content_type)
    if fmt:
        return fmt
    ext = os.path.splitext(file_name or '')[1].lstrip('.').lower()
    if ext in {'mp3', 'm4a', 'wav', 'aac'}:
        return ext
    raise AudioDescriptionUploadError(
        f'Unsupported audio description content type: {content_type}'
    )


def generate_audio_description_upload_url(edx_video_id, file_name, content_type, file_size):
    """
    Step 1 of the upload flow.

    Validates the request, generates a pre-signed PUT URL against the VEM
    S3 bucket, and creates a pending VideoAudioDescription record in
    edx-val (status='upload').

    Returns a dict with `upload_url`, `s3_key`, `edx_video_id`, and
    `expires_in` suitable for serialization to the browser.
    """
    if not edx_video_id:
        raise AudioDescriptionUploadError('edx_video_id is required')

    ad_settings = _get_settings()
    allowed_types = ad_settings.get('ALLOWED_CONTENT_TYPES', [])
    max_bytes = ad_settings.get('MAX_BYTES', 0)
    key_prefix = ad_settings.get('S3_KEY_PREFIX', 'audio_descriptions/')
    expires_in = ad_settings.get('PRESIGNED_PUT_EXPIRATION_SECONDS', 3600)

    if content_type not in allowed_types:
        raise AudioDescriptionUploadError(
            f'Unsupported audio description content type: {content_type}'
        )

    try:
        file_size = int(file_size)
    except (TypeError, ValueError) as exc:
        raise AudioDescriptionUploadError('file_size must be an integer') from exc

    if file_size <= 0:
        raise AudioDescriptionUploadError('file_size must be greater than zero')
    if max_bytes and file_size > max_bytes:
        raise AudioDescriptionUploadError(
            f'Audio description file exceeds maximum allowed size of {max_bytes} bytes'
        )

    safe_name = _sanitize_file_name(file_name)
    file_format = _resolve_format(content_type, safe_name)
    s3_key = f'{key_prefix}{edx_video_id}/{uuid4().hex}_{safe_name}'

    bucket = _get_bucket_name()
    if not bucket:
        raise AudioDescriptionUploadError(
            'VIDEO_UPLOAD_PIPELINE.VEM_S3_BUCKET is not configured'
        )

    s3_client = get_s3_client()
    upload_url = s3_client.generate_presigned_url(
        ClientMethod='put_object',
        Params={
            'Bucket': bucket,
            'Key': s3_key,
            'ContentType': content_type,
        },
        ExpiresIn=expires_in,
    )
    upload_url = _rewrite_devstack_presigned_url(upload_url)

    create_video_audio_description(
        video_id=edx_video_id,
        file_name=safe_name,
        file_format=file_format,
        s3_key=s3_key,
        file_size=file_size,
    )

    return {
        'upload_url': upload_url,
        's3_key': s3_key,
        'edx_video_id': edx_video_id,
        'expires_in': expires_in,
    }


def complete_audio_description_upload(edx_video_id, s3_key):
    """
    Step 3 of the upload flow.

    Verifies the object exists in S3 (HEAD), then flips the edx-val
    record from 'upload' to 'ready'. Returns the serialized record.
    """
    if not edx_video_id or not s3_key:
        raise AudioDescriptionUploadError('edx_video_id and s3_key are required')

    record = get_video_audio_description(edx_video_id)
    if record is None:
        raise AudioDescriptionUploadError(
            f'No pending audio description found for video {edx_video_id}'
        )
    if record['s3_key'] != s3_key:
        raise AudioDescriptionUploadError(
            'Provided s3_key does not match the pending audio description record'
        )

    bucket = _get_bucket_name()
    s3_client = get_s3_client()
    try:
        head = s3_client.head_object(Bucket=bucket, Key=s3_key)
    except Exception as exc:
        log.exception(
            'Audio description object missing or unreadable in S3 (bucket=%s key=%s)',
            bucket,
            s3_key,
        )
        raise AudioDescriptionUploadError(
            'Audio description object was not found in S3'
        ) from exc

    expected_size = record.get('file_size')
    actual_size = head.get('ContentLength')
    if expected_size and actual_size and int(expected_size) != int(actual_size):
        log.warning(
            'Audio description size mismatch for %s: expected=%s actual=%s',
            edx_video_id,
            expected_size,
            actual_size,
        )

    return mark_video_audio_description_ready(edx_video_id)


def delete_audio_description(edx_video_id):
    """
    Delete the AD record from edx-val and the underlying object from S3.

    Returns True if a record was deleted, False if there was nothing to
    delete.
    """
    record = get_video_audio_description(edx_video_id)
    if record is None:
        return False

    bucket = _get_bucket_name()
    s3_client = get_s3_client()
    try:
        s3_client.delete_object(Bucket=bucket, Key=record['s3_key'])
    except Exception:  # pylint: disable=broad-except
        # Log and continue — orphaned S3 objects are recoverable, but a
        # dangling DB row would block re-uploads.
        log.exception(
            'Failed to delete audio description object from S3 (bucket=%s key=%s)',
            bucket,
            record['s3_key'],
        )

    delete_video_audio_description(edx_video_id)
    return True


def generate_audio_description_download_url(edx_video_id):
    """
    Generate a fresh pre-signed GET URL for the AD file. Returns None if
    no ready record exists for the given video.

    The actual S3 work lives in `xmodule.video_block.audio_description_urls`
    so the LMS video block can mint download URLs without importing
    `cms.djangoapps.contentstore` (which is not in INSTALLED_APPS for LMS).
    """
    from xmodule.video_block.audio_description_urls import (  # pylint: disable=import-outside-toplevel
        generate_audio_description_download_url as _generate,
    )
    return _generate(edx_video_id)
