"""
LMS-safe helpers for minting pre-signed GET URLs to audio description files.

The CMS-side upload flow lives in
`cms.djangoapps.contentstore.audio_description_storage_handlers`, but the LMS
cannot import from `cms.djangoapps.*` (those models are not in INSTALLED_APPS
for the LMS process). This module duplicates only the small download-URL
slice so the LMS video block can fetch the AD file at playback time without
pulling in any Studio-only code.
"""

import boto3
from django.conf import settings
from edxval.api import get_video_audio_description


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


def _get_s3_client():
    """
    Build an S3 client that honors AWS_S3_ENDPOINT_URL when set (localstack
    / MinIO in dev). In production AWS_S3_ENDPOINT_URL is unset and boto3
    falls back to its default endpoint resolution.
    """
    kwargs = {}
    endpoint_url = getattr(settings, 'AWS_S3_ENDPOINT_URL', None)
    if endpoint_url:
        kwargs['endpoint_url'] = endpoint_url
    return boto3.client('s3', **kwargs)


def generate_audio_description_download_url(edx_video_id):
    """
    Generate a fresh pre-signed GET URL for the AD file associated with the
    given video. Returns None if no ready record exists.
    """
    record = get_video_audio_description(edx_video_id)
    if record is None or record.get('status') != 'ready':
        return None

    ad_settings = getattr(settings, 'VIDEO_AUDIO_DESCRIPTION_SETTINGS', {})
    expires_in = ad_settings.get('PRESIGNED_GET_EXPIRATION_SECONDS', 6 * 3600)
    bucket = settings.VIDEO_UPLOAD_PIPELINE.get('VEM_S3_BUCKET', '')

    download_url = _get_s3_client().generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': bucket, 'Key': record['s3_key']},
        ExpiresIn=expires_in,
    )
    return _rewrite_devstack_presigned_url(download_url)
