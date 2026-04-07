"""
Tests for the studio_audio_description XBlock handler and its
contentstore.enable_audio_description_upload waffle flag gate.

The handler itself delegates to the storage helpers in
cms.djangoapps.contentstore.audio_description_storage_handlers; these
tests focus on the handler's gating behavior and dispatch logic, not on
the storage helpers themselves.

These tests live in CMS-test land (rather than alongside the rest of
the LMS-side video handler tests in
lms/djangoapps/courseware/tests/test_video_handlers.py) because
cms.djangoapps.contentstore.toggles transitively imports the
Studio-only search-api and so cannot be loaded under LMS test settings.
"""

import importlib
import json
from unittest.mock import Mock, patch

from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag
from webob import Request

from cms.djangoapps.contentstore.toggles import ENABLE_AUDIO_DESCRIPTION_UPLOAD
from xmodule.video_block.video_block import VideoBlock


class StudioAudioDescriptionHandlerTest(TestCase):
    """
    The XBlock @handler decorator does not wrap the function -- it just
    sets _is_xblock_handler = True -- so we can call
    VideoBlock.studio_audio_description as a plain function with a Mock
    standing in for `self`. The handler only touches self.edx_video_id
    and self.audio_description, both of which the Mock can carry.
    """

    def setUp(self):
        super().setUp()
        # Pre-load the contentstore.views package first to force the
        # same loading order Django uses in production. video_storage_handlers
        # has a latent circular-import bug: at line 65 it imports from
        # views.course, and views/__init__.py later loads
        # views.transcript_settings, which imports
        # transcript_storage_handlers, which needs TranscriptProvider
        # from video_storage_handlers (defined further down at line
        # 114). In production this works because views/__init__.py
        # loads .course (line 6) -> finishes -> later loads
        # .transcript_settings (line 17), so video_storage_handlers is
        # only triggered AFTER views.course is fully loaded, avoiding
        # the half-initialized state. If we let
        # audio_description_storage_handlers be the first thing to
        # touch video_storage_handlers, it triggers loading of
        # views/__init__.py mid-way and trips the circular. Loading
        # views ourselves first reproduces the production order.
        importlib.import_module('cms.djangoapps.contentstore.views')
        # Eagerly import the storage handlers submodule so each test
        # can patch attributes on it via patch.object(). We use
        # patch.object on this module reference (rather than dotted
        # @patch decorators) because @patch decorators activate
        # *before* setUp runs.
        self.storage_handlers = importlib.import_module(
            'cms.djangoapps.contentstore.audio_description_storage_handlers'
        )

    def _build_block_mock(self, edx_video_id='video-1', audio_description=''):
        block = Mock(spec_set=['edx_video_id', 'audio_description'])
        block.edx_video_id = edx_video_id
        block.audio_description = audio_description
        return block

    def _call(self, block, method, body=None):
        kwargs = {'method': method}
        if body is not None:
            kwargs['body'] = body
        request = Request.blank('', **kwargs)
        return VideoBlock.studio_audio_description(block, request=request)

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION_UPLOAD, active=False)
    def test_handler_returns_404_when_flag_disabled(self):
        """
        When the upload flag is off, every HTTP method on the handler
        must return 404 so the endpoint looks non-existent to clients.
        """
        block = self._build_block_mock()
        for method in ('GET', 'POST', 'DELETE'):
            response = self._call(block, method)
            self.assertEqual(response.status_code, 404, msg=f'method={method}')

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION_UPLOAD, active=True)
    def test_post_get_upload_url_when_flag_enabled(self):
        """
        With the flag on, a POST get_upload_url request should reach the
        storage helper and return its result verbatim.
        """
        block = self._build_block_mock(edx_video_id='video-1')
        body = json.dumps({
            'action': 'get_upload_url',
            'file_name': 'bar.mp3',
            'content_type': 'audio/mpeg',
            'file_size': 1024,
        }).encode('utf-8')

        with patch.object(
            self.storage_handlers, 'generate_audio_description_upload_url'
        ) as mock_generate:
            mock_generate.return_value = {
                'upload_url': 'https://s3.example/put',
                's3_key': 'audio_descriptions/foo/bar.mp3',
                'edx_video_id': 'video-1',
                'expires_in': 3600,
            }
            response = self._call(block, 'POST', body=body)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, mock_generate.return_value)
            mock_generate.assert_called_once_with(
                edx_video_id='video-1',
                file_name='bar.mp3',
                content_type='audio/mpeg',
                file_size=1024,
            )

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION_UPLOAD, active=True)
    def test_post_complete_when_flag_enabled(self):
        """
        With the flag on, a POST complete request should reach the
        storage helper, store the filename on the block, and return the
        helper's result.
        """
        block = self._build_block_mock(edx_video_id='video-1')
        body = json.dumps({
            'action': 'complete',
            'edx_video_id': 'video-1',
            's3_key': 'audio_descriptions/foo/bar.mp3',
        }).encode('utf-8')

        with patch.object(
            self.storage_handlers, 'complete_audio_description_upload'
        ) as mock_complete:
            mock_complete.return_value = {
                'file_name': 'bar.mp3',
                'edx_video_id': 'video-1',
                'status': 'ready',
            }
            response = self._call(block, 'POST', body=body)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(block.audio_description, 'bar.mp3')
            mock_complete.assert_called_once_with(
                edx_video_id='video-1',
                s3_key='audio_descriptions/foo/bar.mp3',
            )

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION_UPLOAD, active=True)
    def test_get_returns_404_when_no_url(self):
        """
        With the flag on but no AD record on the block, the GET branch
        should return 404 (the storage helper returns None).
        """
        block = self._build_block_mock()

        with patch.object(
            self.storage_handlers, 'generate_audio_description_download_url'
        ) as mock_generate:
            mock_generate.return_value = None
            response = self._call(block, 'GET')

            self.assertEqual(response.status_code, 404)

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION_UPLOAD, active=True)
    def test_get_returns_url_when_present(self):
        """
        With the flag on and a ready AD record, the GET branch returns
        a JSON body containing the helper's pre-signed URL plus the
        block's stored filename.
        """
        block = self._build_block_mock(audio_description='bar.mp3')

        with patch.object(
            self.storage_handlers, 'generate_audio_description_download_url'
        ) as mock_generate:
            mock_generate.return_value = 'https://s3.example/get-presigned'
            response = self._call(block, 'GET')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {
                'file_name': 'bar.mp3',
                'url': 'https://s3.example/get-presigned',
            })

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION_UPLOAD, active=True)
    def test_delete_when_flag_enabled(self):
        """
        With the flag on, a DELETE request should call the storage
        helper, clear the block's audio_description field, and return
        204.
        """
        block = self._build_block_mock(audio_description='bar.mp3')

        with patch.object(
            self.storage_handlers, 'delete_audio_description'
        ) as mock_delete:
            response = self._call(block, 'DELETE')

            self.assertEqual(response.status_code, 204)
            self.assertEqual(block.audio_description, '')
            mock_delete.assert_called_once_with(block.edx_video_id)
