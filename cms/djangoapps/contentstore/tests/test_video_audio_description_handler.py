"""
Tests for the studio_audio_description XBlock handler and its
contentstore.enable_audio_description waffle flag gate.

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
from unittest.mock import Mock, patch

from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.locator import CourseLocator
from webob import Request

from cms.djangoapps.contentstore.toggles import ENABLE_AUDIO_DESCRIPTION
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

        importlib.import_module("cms.djangoapps.contentstore.views")
        self.storage_handlers = importlib.import_module(
            "cms.djangoapps.contentstore.audio_description_storage_handlers"
        )

    def _build_block_mock(self, edx_video_id="video-1", audio_description=""):
        """
        Return a minimal Mock that satisfies the attribute contract
        expected by the studio_audio_description handler.
        """
        block = Mock(
            spec_set=[
                "edx_video_id",
                "audio_description",
                "audio_description_video_id",
                "course_id",
            ]
        )
        block.edx_video_id = edx_video_id
        block.audio_description = audio_description
        block.audio_description_video_id = ""
        block.course_id = CourseLocator(org="test", course="test", run="test")
        return block

    def _call(self, block, method, body=None, request=None):
        """
        Build a WebOb Request and invoke the handler directly,
        bypassing the XBlock runtime dispatch machinery.
        """
        if request is not None:
            return VideoBlock.studio_audio_description(block, request=request)
        kwargs = {"method": method}
        if body is not None:
            kwargs["body"] = body
        request = Request.blank("", **kwargs)
        return VideoBlock.studio_audio_description(block, request=request)

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION, active=False)
    def test_handler_returns_404_when_flag_disabled(self):
        """
        When the upload flag is off, every HTTP method on the handler
        must return 404 so the endpoint looks non-existent to clients.
        """
        block = self._build_block_mock()
        for method in ("GET", "POST", "DELETE"):
            response = self._call(block, method)
            self.assertEqual(response.status_code, 404, msg=f"method={method}")

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION, active=True)
    def test_post_uploads_file_and_returns_url(self):
        """
        With the flag on, a POST request carrying a file should reach
        upload_audio_description and return {file_name, url} with 201.
        """
        block = self._build_block_mock(edx_video_id="video-1")

        file_mock = Mock()
        file_mock.name = "bar.mp3"
        file_mock.type = "audio/mpeg"
        file_mock.file = Mock()

        request = Mock()
        request.method = "POST"
        request.POST = {
            "file": file_mock,
            "file_name": "bar.mp3",
            "content_type": "audio/mpeg",
        }

        with patch.object(
            self.storage_handlers, "upload_audio_description"
        ) as mock_upload:
            mock_upload.return_value = "https://s3.example/bar.mp3"
            response = self._call(block, "POST", request=request)

            self.assertEqual(response.status_code, 201)
            self.assertEqual(
                response.json,
                {"file_name": "bar.mp3", "url": "https://s3.example/bar.mp3"},
            )
            mock_upload.assert_called_once_with(
                edx_video_id="video-1",
                file_name="bar.mp3",
                content_type="audio/mpeg",
                file_data=file_mock.file,
            )

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION, active=True)
    def test_post_returns_400_when_file_missing(self):
        """
        With the flag on but no file in the POST body, the handler must
        return 400 with an error message.
        """
        block = self._build_block_mock(edx_video_id="video-1")

        request = Mock()
        request.method = "POST"
        request.POST = {}  # no 'file' key

        response = self._call(block, "POST", request=request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json)

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION, active=True)
    def test_get_returns_404_when_no_url(self):
        """
        With the flag on but no AD record on the block, the GET branch
        should return 404 (the storage helper returns None).
        """
        block = self._build_block_mock()

        with patch.object(self.storage_handlers, "get_audio_description_url") as mock_url:
            mock_url.return_value = None
            response = self._call(block, "GET")

            self.assertEqual(response.status_code, 404)

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION, active=True)
    def test_get_returns_url_when_present(self):
        """
        With the flag on and a ready AD record, the GET branch returns
        a JSON body containing the helper's pre-signed URL plus the
        block's stored filename.
        """
        block = self._build_block_mock(audio_description="bar.mp3")

        with patch.object(self.storage_handlers, "get_audio_description_url") as mock_url:
            mock_url.return_value = "https://s3.example/get-presigned"
            response = self._call(block, "GET")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json,
                {
                    "file_name": "bar.mp3",
                    "url": "https://s3.example/get-presigned",
                },
            )

    @override_waffle_flag(ENABLE_AUDIO_DESCRIPTION, active=True)
    def test_delete_when_flag_enabled(self):
        """
        With the flag on, a DELETE request should call the storage
        helper, clear the block's audio_description field, and return
        204.
        """
        block = self._build_block_mock(
            edx_video_id="video-1", audio_description="bar.mp3"
        )

        with patch.object(
            self.storage_handlers, "delete_audio_description"
        ) as mock_delete:
            response = self._call(block, "DELETE")

            self.assertEqual(response.status_code, 204)
            self.assertEqual(block.audio_description, "")
            mock_delete.assert_called_once_with("video-1")
