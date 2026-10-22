"""Tests for the read-only edx-exams client used by certificate access checks."""

from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from lms.djangoapps.certificates import exams_client
from lms.djangoapps.certificates.exams_client import ExamsServiceError, get_exam_proctoring_statuses

User = get_user_model()

COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'


@override_settings(
    EXAMS_SERVICE_URL='http://exams.example.com/api/v1',
    EXAMS_SERVICE_USERNAME='edx_exams_worker',
    EXAMS_SERVICE_REQUEST_TIMEOUT=(1, 2),
)
class ExamsClientTests(TestCase):
    """Verify the request made to edx-exams and the handling of its responses."""

    def setUp(self):
        super().setUp()
        self.worker = User.objects.create(username='edx_exams_worker')
        self.learner = mock.Mock(id=42)

        jwt_patcher = mock.patch.object(exams_client, 'create_jwt_for_user', return_value='a-jwt')
        self.create_jwt = jwt_patcher.start()
        self.addCleanup(jwt_patcher.stop)

    def _get(self, response=None, error=None):
        """Call the client with ``requests.Session.get`` mocked; returns (result, get_mock)."""
        with mock.patch.object(requests.Session, 'get', return_value=response, side_effect=error) as get:
            return get_exam_proctoring_statuses(self.learner, COURSE_KEY), get

    @staticmethod
    def _response(payload=None, status_error=None, json_error=None):
        """Build a mocked HTTP response."""
        response = mock.Mock()
        response.raise_for_status.side_effect = status_error
        response.json.side_effect = json_error
        response.json.return_value = payload
        return response

    def test_request_is_authenticated_and_scoped(self):
        exams = [{'exam_id': 1, 'content_id': 'c', 'exam_type': 'proctored', 'attempt_id': 5, 'status': 'verified'}]

        result, get = self._get(self._response({'exams': exams}))

        self.assertEqual(result, exams)
        self.create_jwt.assert_called_once_with(self.worker)
        get.assert_called_once_with(
            'http://exams.example.com/api/v1/certificate_proctoring/course_id/'
            'course-v1%3AedX%2BDemoX%2BDemo_Course/status',
            params={'lms_user_id': 42},
            timeout=(1, 2),
        )

    def test_course_key_is_url_encoded(self):
        _, get = self._get(self._response({'exams': []}))

        url = get.call_args.args[0]
        self.assertNotIn(COURSE_KEY, url)
        self.assertTrue(url.endswith('/course-v1%3AedX%2BDemoX%2BDemo_Course/status'))

    def test_session_is_closed_after_success(self):
        with mock.patch.object(requests.Session, 'close') as close:
            self._get(self._response({'exams': []}))

        close.assert_called_once_with()

    def test_session_is_closed_after_failure(self):
        with mock.patch.object(requests.Session, 'close') as close:
            with self.assertRaises(ExamsServiceError):
                self._get(error=requests.ConnectionError('down'))

        close.assert_called_once_with()

    def test_http_error_raises_service_error(self):
        response = self._response(status_error=requests.HTTPError('403'))

        with self.assertRaises(ExamsServiceError):
            self._get(response)

    def test_connection_error_raises_service_error(self):
        with self.assertRaises(ExamsServiceError):
            self._get(error=requests.ConnectionError('down'))

    def test_timeout_raises_service_error(self):
        with self.assertRaises(ExamsServiceError):
            self._get(error=requests.Timeout('slow'))

    def test_invalid_json_raises_service_error(self):
        with self.assertRaises(ExamsServiceError):
            self._get(self._response(json_error=ValueError('not json')))

    def test_malformed_payload_raises_service_error(self):
        for payload in (None, [], {}, {'exams': 'nope'}, {'exams': ['nope']}):
            with self.subTest(payload=payload), self.assertRaises(ExamsServiceError):
                self._get(self._response(payload))

    def test_no_attempt_record_is_accepted(self):
        exams = [{'exam_id': 1, 'content_id': 'c', 'exam_type': 'proctored', 'attempt_id': None, 'status': None}]

        result, _ = self._get(self._response({'exams': exams}))

        self.assertEqual(result, exams)

    def test_malformed_exam_record_raises_service_error(self):
        complete = {'exam_id': 1, 'content_id': 'c', 'exam_type': 'proctored', 'attempt_id': 5, 'status': 'verified'}
        malformed = [
            {key: value for key, value in complete.items() if key != missing}
            for missing in complete
        ] + [
            {**complete, 'status': None},  # attempt_id without a status
            {**complete, 'attempt_id': None},  # status without an attempt_id
            {**complete, 'status': 3},
            {**complete, 'exam_type': None},
        ]

        for exam in malformed:
            with self.subTest(exam=exam), self.assertRaises(ExamsServiceError):
                self._get(self._response({'exams': [exam]}))

    def test_missing_service_user_raises_service_error(self):
        self.worker.delete()

        with self.assertRaises(ExamsServiceError):
            self._get(self._response({'exams': []}))
