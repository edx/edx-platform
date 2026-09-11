"""
Tests for language code normalization in the Studio manual transcript upload
handler (cms.djangoapps.contentstore.transcript_storage_handlers).

Bare 2-letter language codes (e.g. "es", "zh") selected in the transcript
upload UI must be normalized to their canonical edX dialect code (e.g.
"es-419", "zh-cn") before being handed to edxval, so that a transcript
uploaded under either spelling always lands on the same
`edxval_videotranscript` row.
"""

from unittest.mock import Mock, patch

import ddt
from django.core.files.base import ContentFile
from django.test import TestCase

from cms.djangoapps.contentstore import transcript_storage_handlers


VALID_SRT_CONTENT = (
    b"0\n"
    b"00:00:10,500 --> 00:00:13,000\n"
    b"Hello world\n\n"
)


def _build_request(
        edx_video_id="video-1", language_code="en", new_language_code="es", file_content=VALID_SRT_CONTENT,
):
    """
    Return a minimal Mock standing in for the WSGI request `upload_transcript` expects.
    """
    request = Mock()
    request.POST = {
        "edx_video_id": edx_video_id,
        "language_code": language_code,
        "new_language_code": new_language_code,
    }
    request.FILES = {"file": ContentFile(file_content, name="transcript.srt")}
    return request


@ddt.ddt
class UploadTranscriptLanguageNormalizationTest(TestCase):
    """
    Verify that `upload_transcript` normalizes the incoming (new) language code
    to its canonical edX dialect code before calling into edxval.
    """

    @ddt.data(
        ("zh", "zh-cn"),
        ("es", "es-419"),
        ("pt", "pt-br"),
        ("de", "de-de"),
        ("it", "it-it"),
        ("ko", "ko-kr"),
        ("tr", "tr-tr"),
        # Already-canonical codes must pass through unchanged.
        ("es-419", "es-419"),
        ("pt-br", "pt-br"),
        # "fr", "ru" and "fa" are themselves distinct canonical edX codes (not aliases of a
        # dialect, e.g. "fa" is generic Persian while "fa-ir" is a separate, distinct
        # language), so they must NOT be remapped.
        ("fr", "fr"),
        ("ru", "ru"),
        ("fa", "fa"),
        # A code with no known mapping must pass through unchanged.
        ("da", "da"),
    )
    @ddt.unpack
    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.use_mock_video_uploads", return_value=False)
    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages",
           return_value=[])
    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.create_or_update_video_transcript")
    def test_new_language_code_is_normalized_before_edxval_call(
            self, bare_code, expected_code, mock_create_or_update, mock_get_available_languages, mock_use_mock,
    ):  # pylint: disable=unused-argument, too-many-positional-arguments
        request = _build_request(new_language_code=bare_code)

        response = transcript_storage_handlers.upload_transcript(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(mock_create_or_update.call_count, 1)
        _args, kwargs = mock_create_or_update.call_args
        self.assertEqual(kwargs["metadata"]["language_code"], expected_code)

    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.use_mock_video_uploads", return_value=False)
    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.create_or_update_video_transcript")
    def test_is_replace_detection_uses_normalized_code(self, mock_create_or_update, mock_use_mock):  # pylint: disable=unused-argument
        # An existing transcript is stored under the canonical code ("zh-cn"), while the
        # upload request supplies the bare code ("zh") for the same language.
        with patch(
            "cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages",
            return_value=["zh-cn"],
        ):
            request = _build_request(new_language_code="zh")
            response = transcript_storage_handlers.upload_transcript(request)

        # Without normalization this would be treated as a brand new language (201, not 200).
        self.assertEqual(response.status_code, 200)


@ddt.ddt
class ValidateTranscriptUploadDataNormalizationTest(TestCase):
    """
    Verify that `validate_transcript_upload_data` compares/normalizes the new language
    code so that a bare code isn't wrongly treated as distinct from its canonical form.
    """

    def setUp(self):
        super().setUp()
        self.files = {"file": ContentFile(b"0\n", name="transcript.srt")}

    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages",
           return_value=["es-419"])
    def test_bare_duplicate_code_is_detected_via_normalization(self, mock_get_available_languages):  # pylint: disable=unused-argument
        # A transcript in "es-419" already exists; uploading "es" (its bare equivalent)
        # for the same language should be flagged as a duplicate.
        data = {"edx_video_id": "video-1", "language_code": "en", "new_language_code": "es"}

        error = transcript_storage_handlers.validate_transcript_upload_data(data, self.files)

        self.assertIsNotNone(error)
        self.assertIn("es-419", error)

    @patch("cms.djangoapps.contentstore.transcript_storage_handlers.get_available_transcript_languages",
           return_value=[])
    def test_no_error_when_language_not_already_present(self, mock_get_available_languages):  # pylint: disable=unused-argument
        data = {"edx_video_id": "video-1", "language_code": "en", "new_language_code": "es"}

        error = transcript_storage_handlers.validate_transcript_upload_data(data, self.files)

        self.assertIsNone(error)
