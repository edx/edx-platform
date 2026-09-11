"""Tests for the centralized certificate/proctoring access policy."""

from unittest import mock

import ddt
from django.test import SimpleTestCase
from edx_django_utils.cache import RequestCache

from lms.djangoapps.certificates import proctoring_block


@ddt.ddt
class CertificateProctoringBlockTests(SimpleTestCase):
    """Verify the approved certificate-behavior status matrix."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def setUp(self):
        super().setUp()
        RequestCache('certificates.proctoring_block').clear()
        self.user = mock.Mock(id=42, is_authenticated=True)
        self.exam = {
            'content_id': 'block-v1:edX+DemoX+Demo_Course+type@proctored_exam+block@exam',
            'is_proctored': True,
            'is_active': True,
            'is_practice_exam': False,
        }

    def _check(self, status, **exam_overrides):
        """Return the block decision for a single mocked exam status."""
        exam = {**self.exam, **exam_overrides}
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            return_value=[exam],
        ), mock.patch(
            'edx_proctoring.api.get_attempt_status_summary',
            return_value={'status': status},
        ):
            return proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

    @ddt.data(
        'created',
        'download_software_clicked',
        'ready_to_start',
        'started',
        'ready_to_submit',
        'submitted',
        'second_review_required',
        'error',
    )
    def test_blocking_statuses(self, status):
        result = self._check(status)

        self.assertTrue(result['blocked'])
        self.assertEqual(result['blocking_statuses'], [status])

    @ddt.data('verified', 'rejected', 'timed_out', 'expired')
    def test_allowed_statuses(self, status):
        result = self._check(status)

        self.assertFalse(result['blocked'])
        self.assertEqual(result['blocking_statuses'], [])

    def test_eligible_is_derived_not_attempted(self):
        result = self._check('eligible')

        self.assertTrue(result['blocked'])
        self.assertEqual(result['reason'], 'proctored_exam_not_attempted')
        self.assertEqual(result['blocking_statuses'], ['not_attempted'])

    @ddt.data(
        {'is_proctored': False},
        {'is_active': False},
        {'is_practice_exam': True},
    )
    def test_non_required_exams_are_ignored(self, exam_overrides):
        result = self._check('submitted', **exam_overrides)

        self.assertFalse(result['blocked'])

    def test_one_blocking_exam_blocks_among_allowed_exams(self):
        second_exam = {
            **self.exam,
            'content_id': 'block-v1:edX+DemoX+Demo_Course+type@proctored_exam+block@second',
        }
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            return_value=[self.exam, second_exam],
        ), mock.patch(
            'edx_proctoring.api.get_attempt_status_summary',
            side_effect=[{'status': 'verified'}, {'status': 'started'}],
        ):
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertTrue(result['blocked'])
        self.assertEqual(result['blocking_statuses'], ['started'])

    def test_lookup_failure_blocks_and_is_not_silent(self):
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            side_effect=RuntimeError('provider unavailable'),
        ), mock.patch('lms.djangoapps.certificates.proctoring_block.increment') as increment, self.assertLogs(
            proctoring_block.log, level='ERROR'
        ):
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertTrue(result['blocked'])
        self.assertEqual(result['reason'], 'proctoring_status_unavailable')
        increment.assert_any_call('certificates.proctoring_block.lookup_error')

    def test_unclassified_status_blocks_and_is_logged(self):
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            return_value=[self.exam],
        ), mock.patch(
            'edx_proctoring.api.get_attempt_status_summary',
            return_value={'status': 'declined'},
        ), self.assertLogs(proctoring_block.log, level='ERROR'):
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertTrue(result['blocked'])
        self.assertEqual(result['reason'], 'proctoring_status_unavailable')
        self.assertEqual(result['blocking_statuses'], ['declined'])

    def test_result_is_cached_for_the_request(self):
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            return_value=[self.exam],
        ) as get_exams, mock.patch(
            'edx_proctoring.api.get_attempt_status_summary',
            return_value={'status': 'verified'},
        ) as get_summary:
            first_result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)
            second_result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertIs(first_result, second_result)
        get_exams.assert_called_once_with(self.COURSE_KEY, active_only=True)
        get_summary.assert_called_once_with(self.user.id, self.COURSE_KEY, self.exam['content_id'])

    def test_feature_flag_off_preserves_access(self):
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=False,
        ), mock.patch('edx_proctoring.api.get_all_exams_for_course') as get_exams:
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertFalse(result['blocked'])
        get_exams.assert_not_called()
