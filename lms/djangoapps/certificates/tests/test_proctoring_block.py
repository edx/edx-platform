"""Tests for the centralized certificate/proctoring access policy."""

from datetime import date, datetime, timezone
from unittest import mock

import ddt
from django.test import SimpleTestCase, override_settings
from edx_django_utils.cache import RequestCache

from lms.djangoapps.certificates import proctoring_block
from lms.djangoapps.certificates.config import get_certificate_proctoring_review_block_effective_at


@ddt.ddt
class CertificateProctoringBlockTests(SimpleTestCase):
    """Verify the approved certificate-behavior status matrix."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def setUp(self):
        super().setUp()
        RequestCache('certificates.proctoring_block').clear()
        self.user = mock.Mock(id=42, is_authenticated=True)
        self.exam = {
            'id': 101,
            'content_id': 'block-v1:edX+DemoX+Demo_Course+type@proctored_exam+block@exam',
            'is_proctored': True,
            'is_active': True,
            'is_practice_exam': False,
        }

    def _certificate(
        self,
        created_date,
        status_before_cutoff=None,
        has_non_downloadable_after_cutoff=False,
    ):
        """Build a certificate mock with the status history used by the cutoff policy."""
        certificate = mock.Mock(id=123, created_date=created_date)
        before_cutoff = mock.MagicMock()
        before_cutoff.order_by.return_value.first.return_value = (
            mock.Mock(status=status_before_cutoff) if status_before_cutoff is not None else None
        )
        after_cutoff = mock.MagicMock()
        after_cutoff.exclude.return_value.exists.return_value = has_non_downloadable_after_cutoff
        history = mock.MagicMock()
        history.filter.side_effect = [before_cutoff, after_cutoff]
        certificate.history.all.return_value = history
        return certificate

    def _check(self, status, certificate=None, **exam_overrides):
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
            return proctoring_block.get_certificate_proctoring_status(
                self.user, self.COURSE_KEY, certificate
            )

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25T00:00:00+00:00')
    def test_certificate_created_before_policy_is_not_blocked(self):
        certificate = self._certificate(
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            status_before_cutoff='downloadable',
        )
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch('edx_proctoring.api.get_all_exams_for_course') as get_exams:
            result = proctoring_block.get_certificate_proctoring_status(
                self.user,
                self.COURSE_KEY,
                certificate,
            )

        self.assertFalse(result['blocked'])
        get_exams.assert_not_called()

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25T00:00:00+00:00')
    def test_pre_cutoff_certificate_that_was_not_downloadable_is_evaluated(self):
        certificate = self._certificate(
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            status_before_cutoff='unavailable',
        )
        result = self._check('submitted', certificate=certificate)

        self.assertTrue(result['blocked'])

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25T00:00:00+00:00')
    def test_pre_cutoff_certificate_revoked_after_policy_start_is_evaluated(self):
        certificate = self._certificate(
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            status_before_cutoff='downloadable',
            has_non_downloadable_after_cutoff=True,
        )
        result = self._check('submitted', certificate=certificate)

        self.assertTrue(result['blocked'])

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25T00:00:00+00:00')
    def test_pre_cutoff_certificate_with_downloadable_history_is_not_blocked(self):
        certificate = self._certificate(
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            status_before_cutoff='downloadable',
        )
        result = self._check('submitted', certificate=certificate)

        self.assertFalse(result['blocked'])

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25T00:00:00+00:00')
    def test_certificate_created_at_policy_start_is_evaluated(self):
        certificate = self._certificate(
            datetime(2026, 9, 25, tzinfo=timezone.utc),
        )
        result = self._check(
            'submitted',
            certificate=certificate,
        )

        self.assertTrue(result['blocked'])
        self.assertEqual(result['blocking_statuses'], ['submitted'])

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

    def test_eligible_no_attempt_is_non_blocking(self):
        result = self._check('eligible')

        self.assertFalse(result['blocked'])
        self.assertEqual(result['blocking_statuses'], [])

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

    def test_no_attempt_does_not_mask_review_pending_exam(self):
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
            side_effect=[{'status': 'eligible'}, {'status': 'submitted'}],
        ):
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertTrue(result['blocked'])
        self.assertEqual(result['blocking_statuses'], ['submitted'])

    def test_missing_summary_without_attempt_is_non_blocking(self):
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            return_value=[self.exam],
        ), mock.patch(
            'edx_proctoring.api.get_attempt_status_summary',
            return_value=None,
        ), mock.patch(
            'edx_proctoring.api.get_exam_by_content_id',
            return_value=self.exam,
        ), mock.patch(
            'edx_proctoring.api.get_current_exam_attempt',
            return_value=None,
        ):
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertFalse(result['blocked'])
        self.assertEqual(result['blocking_statuses'], [])

    def test_missing_summary_with_review_attempt_still_blocks(self):
        with mock.patch.object(
            proctoring_block.CERTIFICATE_PROCTORING_REVIEW_BLOCK,
            'is_enabled',
            return_value=True,
        ), mock.patch(
            'edx_proctoring.api.get_all_exams_for_course',
            return_value=[self.exam],
        ), mock.patch(
            'edx_proctoring.api.get_attempt_status_summary',
            return_value=None,
        ), mock.patch(
            'edx_proctoring.api.get_exam_by_content_id',
            return_value=self.exam,
        ), mock.patch(
            'edx_proctoring.api.get_current_exam_attempt',
            return_value={'status': 'submitted'},
        ):
            result = proctoring_block.get_certificate_proctoring_status(self.user, self.COURSE_KEY)

        self.assertTrue(result['blocked'])
        self.assertEqual(result['blocking_statuses'], ['submitted'])

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


@ddt.ddt
class CertificateProctoringConfigurationTests(SimpleTestCase):
    """Verify safe parsing of the certificate proctoring cutoff setting."""

    SETTING = 'CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT'
    METRIC = 'certificates.proctoring_block.invalid_effective_at'

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT=None)
    def test_missing_cutoff_is_disabled_without_error(self):
        self.assertIsNone(get_certificate_proctoring_review_block_effective_at())

    @ddt.data('', 'not-a-date', '2026-09-31T00:00:00+00:00')
    def test_invalid_cutoff_is_ignored_and_recorded(self, value):
        with mock.patch(
            'lms.djangoapps.certificates.config.increment'
        ) as increment, self.assertLogs('lms.djangoapps.certificates.config', level='ERROR'):
            with override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT=value):
                result = get_certificate_proctoring_review_block_effective_at()

        self.assertIsNone(result)
        increment.assert_called_once_with(self.METRIC)

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25')
    def test_date_only_cutoff_uses_utc_midnight(self):
        self.assertEqual(
            get_certificate_proctoring_review_block_effective_at(),
            datetime(2026, 9, 25, tzinfo=timezone.utc),
        )

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT='2026-09-25T05:30:00+05:30')
    def test_cutoff_is_normalized_to_utc(self):
        self.assertEqual(
            get_certificate_proctoring_review_block_effective_at(),
            datetime(2026, 9, 25, tzinfo=timezone.utc),
        )

    @override_settings(CERTIFICATE_PROCTORING_REVIEW_BLOCK_EFFECTIVE_AT=date(2026, 9, 25))
    def test_date_object_cutoff_uses_utc_midnight(self):
        self.assertEqual(
            get_certificate_proctoring_review_block_effective_at(),
            datetime(2026, 9, 25, tzinfo=timezone.utc),
        )
